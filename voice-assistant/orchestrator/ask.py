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
import time

from . import config, events, openrouter

log = logging.getLogger("orchestrator.ask")

SENTINEL = config.ASK_SENTINEL
_STREAM_EMIT_INTERVAL = 0.3  # seconds between dashboard ask_stream flushes

_SYSTEM = (
    "You are the knowledge engine for a household kitchen voice assistant. The "
    "user asked a spoken question. Answer it directly and factually.\n\n"
    "Return your answer in TWO parts separated by a line containing only "
    f"{SENTINEL}\n"
    "Part 1 (before the separator): the SPOKEN answer — 1 to 2 short sentences, "
    "plain and direct, the way you'd say it out loud across a kitchen. Lead with "
    "the actual answer.\n"
    f"Part 2 (after {SENTINEL}): the FULL answer for reading on a screen — more "
    "detail, useful specifics, short lists or steps where helpful. Plain text, "
    "no markdown headers.\n\n"
    "Do not offer follow-ups, do not ask questions back, do not mention that you "
    "are an AI. Always include the separator line."
)


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


async def _stream_full(query: str, initial: str, agen) -> None:
    """Drain the rest of the stream, fanning the growing full answer to the
    dashboard. Runs detached from the request that produced the spoken reply."""
    acc = initial
    last = 0.0
    try:
        await events.emit("ask_stream", query=query, text=acc.strip(), done=False)
        async for delta in agen:
            acc += delta
            now = time.monotonic()
            if now - last >= _STREAM_EMIT_INTERVAL:
                await events.emit("ask_stream", query=query, text=acc.strip(), done=False)
                last = now
    except Exception as exc:  # noqa: BLE001
        log.warning("ask full-stream failed: %s", exc)
    finally:
        try:
            await agen.aclose()
        except Exception:  # noqa: BLE001
            pass
    await events.emit("ask_full", query=query, text=acc.strip(), done=True)


async def handle_ask(query: str) -> dict:
    """Stream the answer up to the sentinel and return the short spoken part.
    Spawns a background task to finish streaming the full part to the dashboard.

    Returns {"response": <spoken>, "full": <full-so-far>, "ok": bool}.
    """
    query = query.strip()
    if not query:
        return {"response": "I didn't catch the question.", "full": "", "ok": False}

    messages = [
        {"role": "system", "content": _SYSTEM},
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
        full = acc.strip()
        spoken = _spoken_fallback(full)
        await events.emit("ask_full", query=query, text=full, done=True)
        return {"response": spoken, "full": full, "ok": bool(full)}

    if stream_alive:
        # Finish the full part in the background so the spoken reply returns now.
        asyncio.create_task(_stream_full(query, remainder, agen))
    else:
        await events.emit("ask_full", query=query, text=remainder.strip(), done=True)

    if not spoken:
        spoken = _spoken_fallback(remainder) if remainder else \
            "Here's what I found."
    return {"response": spoken, "full": remainder.strip(), "ok": True}
