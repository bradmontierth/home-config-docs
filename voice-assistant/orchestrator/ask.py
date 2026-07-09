"""Knowledge / ask mode: route a factual question to the smart model and split
the reply short-first at the ===MORE=== sentinel.

handle_ask() streams only until the sentinel, so it returns fast with the short
spoken answer (~1.5s). The rest of the stream is drained by a background task
that fans the growing full answer to the dashboard (ask_stream / ask_full).
The satellite is unaffected — it still just plays the spoken reply.
"""

from __future__ import annotations

import asyncio
import logging
import os
import random
import re
import time
from datetime import datetime
from zoneinfo import ZoneInfo

import httpx

from . import clients, config, events, openrouter

log = logging.getLogger("orchestrator.ask")

SENTINEL = config.ASK_SENTINEL
_STREAM_EMIT_INTERVAL = 0.3  # seconds between dashboard ask_stream flushes

# --- filler: satellite speaks while the smart model works ------------------
_FILLER_PHRASES = [
    "Let me look that up for you.",
    "One sec, checking.",
    "Good question, let me check.",
    "Let me search the web real quick.",
    "Hmm, let me find out.",
]
_filler_urls: list[str] = []


async def ensure_fillers() -> None:
    """Pre-render the filler phrases to the audio cache (skipping any WAV that
    already exists from a previous run). Safe to call repeatedly."""
    for i, phrase in enumerate(_FILLER_PHRASES):
        name, url = f"filler-{i}.wav", f"/audio/filler-{i}.wav"
        if url in _filler_urls:
            continue
        path = os.path.join(config.ANNOUNCE_CACHE_DIR, name)
        if not os.path.exists(path):
            try:
                wav = await clients.synthesize(phrase)
            except Exception as exc:  # noqa: BLE001
                log.warning("filler TTS failed (%r): %s", phrase, exc)
                continue
            with open(path, "wb") as fh:
                fh.write(wav)
        _filler_urls.append(url)


async def _play_filler() -> None:
    """Best-effort: tell the satellite to speak a random filler right now,
    in parallel with the smart-model round trip. Never raises."""
    if not config.ASK_FILLER or not config.SATELLITE_SPEAK_URL:
        return
    if not _filler_urls:
        await ensure_fillers()          # TTS was down at startup — retry now
        if not _filler_urls:
            return
    try:
        async with httpx.AsyncClient(timeout=4) as client:
            await client.post(
                config.SATELLITE_SPEAK_URL,
                json={"url": random.choice(_filler_urls)},
            )
    except Exception as exc:  # noqa: BLE001
        log.info("satellite filler dispatch failed: %s", exc)


# --- conversation history so follow-up asks have context -------------------
_history: list[dict] = []  # {"q": str, "a": str, "t": monotonic}


def _remember(query: str, answer: str) -> dict:
    """Record a Q+A pair NOW (a follow-up can arrive while the full answer is
    still streaming) and return the entry so the streamer can extend it."""
    entry = {"q": query, "a": answer.strip(), "t": time.monotonic()}
    _history.append(entry)
    del _history[:-config.ASK_HISTORY_TURNS]
    return entry


def remember(query: str, answer: str) -> None:
    """Public hook: other intents (sports) record their Q+A here so pronoun
    follow-ups that route to ask ("who do they play next?") have context."""
    _remember(query, answer)


def _history_messages() -> list[dict]:
    """Recent Q+A pairs as chat messages, oldest first. Stale pairs age out so
    a fresh question hours later isn't polluted by old context."""
    cutoff = time.monotonic() - config.ASK_HISTORY_TTL_S
    out: list[dict] = []
    for h in _history:
        if h["t"] >= cutoff and h["a"]:
            out.append({"role": "user", "content": h["q"]})
            out.append({"role": "assistant", "content": h["a"]})
    return out

def _system() -> str:
    now = datetime.now(ZoneInfo(config.ASK_TIMEZONE))
    return (
        "You are the knowledge engine for a household kitchen voice assistant. "
        "The user asked a spoken question. Answer it directly and factually.\n\n"
        f"Right now it is {now:%A, %B %d, %Y, %I:%M %p} in the user's home "
        "timezone. Resolve every relative time word in the question — 'today', "
        "'yesterday', 'last night', 'this morning' — against THIS local "
        "date/time, NEVER against dates implied by search results: web pages "
        "and search tools often run on UTC or other timezones where it may "
        "already be a different day. If search results suggest a different "
        "current date, trust the date given here.\n\n"
        "Your built-in knowledge ends months before this date. You have a web "
        "search tool: for ANYTHING that may have changed since your training — "
        "sports results, news, weather, prices, schedules, anything 'today', "
        "'yesterday', 'last night', or 'latest' — you MUST search the web before "
        "answering. Never answer such questions from memory and never say you "
        "don't know without having searched. If a search comes back unclear or "
        "inconclusive, search again with different terms — do not give up or "
        "report failure after one attempt, and report only facts (scores, "
        "numbers) actually stated in results, never guessed. Before claiming "
        "that something did NOT happen (no game, no event, nothing scheduled), "
        "confirm with a second search — absence from one set of results is not "
        "proof. Only skip the "
        "search for timeless general knowledge (conversions, definitions, "
        "how-tos), where searching just adds delay.\n\n"
        "Return your answer in TWO parts separated by a line containing only "
        f"{SENTINEL}\n"
        "Part 1 (before the separator): the SPOKEN answer — 1 to 2 short "
        "sentences, plain and direct, the way you'd say it out loud across a "
        "kitchen. Lead with the actual answer. Never include URLs, citations, "
        "or source names here.\n"
        f"Part 2 (after {SENTINEL}): the FULL answer for reading on a screen — "
        "more detail, useful specifics, short lists or steps where helpful. "
        "Plain text, no markdown headers or links; if you searched, you may "
        "name sources here.\n\n"
        "Do not offer follow-ups, do not ask questions back, do not mention "
        "that you are an AI. Always include the separator line.\n\n"
        "Earlier questions and answers from the last few minutes may precede "
        "the current question — use them to resolve references like 'they', "
        "'it', or 'the game' in follow-ups."
    )


# The model sometimes drops search citations into the answer despite the
# prompt — " ([fifa.com](https://…))" read aloud is a disaster. Strip
# parenthesized citations entirely; collapse other markdown links to their text.
_MD_CITATION = re.compile(r"\s*\(\[[^\]]*\]\([^)\s]*\)\)")
_MD_LINK = re.compile(r"\[([^\]]*)\]\([^)\s]*\)")


def _strip_links(text: str) -> str:
    return _MD_LINK.sub(r"\1", _MD_CITATION.sub("", text)).strip()


def _spoken_fallback(text: str) -> str:
    """When the model omits the sentinel, derive a short spoken version: the
    first sentence or two of the full answer."""
    text = text.strip()
    if not text:
        return "Sorry, I couldn't find an answer to that."
    out, count = [], 0
    for ch in text:
        out.append(ch)
        if ch in ".!?":
            count += 1
            if count >= 2:
                break
    spoken = "".join(out).strip()
    return spoken or text[:200].strip()


async def _stream_full(query: str, entry: dict, initial: str, agen) -> None:
    """Drain the rest of the stream, fanning the growing full answer to the
    dashboard. Runs detached from the request that produced the spoken reply."""
    acc = initial
    last = 0.0
    try:
        await events.emit("ask_stream", query=query, text=_strip_links(acc), done=False)
        async for delta in agen:
            acc += delta
            now = time.monotonic()
            if now - last >= _STREAM_EMIT_INTERVAL:
                await events.emit("ask_stream", query=query, text=_strip_links(acc), done=False)
                last = now
    except Exception as exc:  # noqa: BLE001
        log.warning("ask full-stream failed: %s", exc)
    finally:
        try:
            await agen.aclose()
        except Exception:  # noqa: BLE001
            pass
    entry["a"] = f"{entry['a']}\n{acc}".strip()
    await events.emit("ask_full", query=query, text=_strip_links(acc), done=True)


async def handle_ask(query: str) -> dict:
    """Stream the answer up to the sentinel and return the short spoken part.
    Spawns a background task to finish streaming the full part to the dashboard.

    Returns {"response": <spoken>, "full": <full-so-far>, "ok": bool}.
    """
    query = query.strip()
    if not query:
        return {"response": "I didn't catch the question.", "full": "", "ok": False}

    # Speak the filler in parallel — the satellite is blocked on our HTTP
    # response, so the "let me look that up" must be pushed, not returned.
    asyncio.create_task(_play_filler())

    messages = [
        {"role": "system", "content": _system()},
        *_history_messages(),
        {"role": "user", "content": query},
    ]
    agen = openrouter.stream_chat(messages)
    acc = ""
    spoken: str | None = None
    remainder = ""
    stream_alive = False
    try:
        async for delta in agen:
            acc += delta
            if SENTINEL in acc:
                idx = acc.index(SENTINEL)
                spoken = acc[:idx].strip()
                remainder = acc[idx + len(SENTINEL):].lstrip()
                stream_alive = True  # generator paused mid-flight, more to come
                break
        # loop fell through -> generator exhausted before any sentinel
    except Exception as exc:  # noqa: BLE001
        log.warning("ask stream failed before sentinel: %s", exc)
        try:
            await agen.aclose()
        except Exception:  # noqa: BLE001
            pass
        if not acc.strip():
            return {
                "response": "Sorry, I couldn't reach the knowledge service.",
                "full": "", "ok": False,
            }

    if spoken is None:
        # No sentinel: whole reply is the answer. Speak a truncated version,
        # push the full text to the dashboard now (nothing left to stream).
        full = _strip_links(acc)
        spoken = _spoken_fallback(full)
        _remember(query, full)
        await events.emit("ask_full", query=query, text=full, done=True)
        return {"response": spoken, "full": full, "ok": bool(full)}

    if stream_alive:
        # Finish the full part in the background so the spoken reply returns
        # now; remember the spoken part immediately so an instant follow-up
        # ("but who's playing in it?") already has context.
        entry = _remember(query, spoken)
        asyncio.create_task(_stream_full(query, entry, remainder, agen))
    else:
        _remember(query, f"{spoken}\n{remainder}".strip())
        await events.emit("ask_full", query=query, text=_strip_links(remainder), done=True)

    if not spoken:
        spoken = _spoken_fallback(remainder) if remainder else \
            "Here's what I found."
    return {"response": _strip_links(spoken), "full": _strip_links(remainder), "ok": True}
