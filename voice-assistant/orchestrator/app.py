"""FastAPI orchestrator: the brain of the kitchen assistant (timers slice).

Pipelines
---------
POST /wake     raw WAV utterance from the satellite
               -> Parakeet transcribe -> stage-2 verify (okay computer)
               -> extract command -> handle_command
POST /command  {"text": "..."}  (bypass audio; testing + future text paths)
               -> handle_command

handle_command -> intent parse (LLM) -> timer engine -> spoken reply + events.

Every step fans an event to the dashboard so the kiosk shows the live badge,
caption, response, and timer cards.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import time
import uuid

from fastapi import Body, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse

from . import ask as ask_mod
from . import lists as lists_mod
from . import music as music_mod
from . import sports as sports_mod
from . import weather as weather_mod
from . import clients, config, events, format as fmt, intent as intent_mod, verify
from .timers import TimerEngine

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s"
)
log = logging.getLogger("orchestrator.app")

app = FastAPI(title="Kitchen Voice Orchestrator")
ENGINE: TimerEngine


# --------------------------------------------------------------------------
# conversation session (for follow-up turns — "also add milk", "make it 15")
# --------------------------------------------------------------------------
# Single satellite for now, so one module-level session is enough. Holds a
# short summary of the last turn; a follow-up parse gets it as context so the
# LLM can resolve references and, crucially, reject unrelated room chatter.
SESSION_TTL_S = 90.0
_SESSION: dict = {"ts": 0.0, "summary": "", "last_added": [], "pending": None}


def session_set_pending(op: str, items: list[dict], list_type: str | None = None) -> None:
    """Stash a destructive bulk op awaiting a spoken yes/no."""
    _SESSION["pending"] = {"op": op, "items": items, "list_type": list_type}
    _SESSION["ts"] = time.time()


def session_pending() -> dict | None:
    if _SESSION.get("pending") and time.time() - _SESSION["ts"] <= SESSION_TTL_S:
        return _SESSION["pending"]
    return None


def session_clear_pending() -> None:
    _SESSION["pending"] = None


_YES_WORDS = {"yes", "yeah", "yep", "yup", "yea", "sure", "confirm", "confirmed",
              "correct", "affirmative", "ok", "okay", "okey"}
_YES_PHRASES = {"do it", "go ahead", "please do", "yes please", "sounds good",
                "go for it", "that's right", "thats right"}
_NO_WORDS = {"no", "nope", "nah", "cancel", "stop", "dont", "don't"}
_NO_PHRASES = {"never mind", "nevermind", "forget it", "leave it", "do not",
               "no thanks", "no thank you"}


def _affirmation(text: str) -> str | None:
    """Classify a reply to a yes/no confirmation as 'yes', 'no', or None (the
    user said something else and the pending op should be dropped)."""
    t = re.sub(r"[^a-z' ]", "", text.lower()).strip()
    if not t:
        return None
    words = t.split()
    first, two = words[0], " ".join(words[:2])
    if first in _YES_WORDS or two in _YES_PHRASES:
        return "yes"
    if first in _NO_WORDS or two in _NO_PHRASES:
        return "no"
    return None


def session_note(summary: str) -> None:
    """Record a one-line summary of what the last actionable turn did."""
    _SESSION["summary"] = summary
    _SESSION["ts"] = time.time()


def session_set_added(items: list[dict]) -> None:
    """Remember the items the last add produced so a follow-up 'undo' / 'scratch
    my last' can remove exactly them."""
    _SESSION["last_added"] = items or []
    _SESSION["ts"] = time.time()


def session_last_added() -> list[dict]:
    if time.time() - _SESSION["ts"] > SESSION_TTL_S:
        return []
    return _SESSION["last_added"]


def session_context() -> str | None:
    """The recent-turn summary if still fresh, else None (session expired)."""
    if not _SESSION["summary"] or time.time() - _SESSION["ts"] > SESSION_TTL_S:
        return None
    return _SESSION["summary"]


# --------------------------------------------------------------------------
# expiry -> alarm
# --------------------------------------------------------------------------
async def _on_timer_expire(timer: dict) -> None:
    announce_url = None
    if ENGINE.announce_wav_path(timer["id"]):
        announce_url = f"/timers/{timer['id']}/announcement.wav"
    await events.emit(
        "timer_done",
        timer=timer,
        text=fmt.timer_name(timer).capitalize() + " is done!",
        announce_url=announce_url,
    )
    await events.alarm(timer, announce_url)


@app.on_event("startup")
async def _startup() -> None:
    global ENGINE
    ENGINE = TimerEngine(on_expire=_on_timer_expire)
    ENGINE.start()
    music_mod.start()
    # Render the ask filler WAVs now so the first ask doesn't pay TTS latency.
    asyncio.create_task(ask_mod.ensure_fillers())
    log.info("orchestrator up; %d active timer(s) restored", len(ENGINE.active()))


@app.on_event("shutdown")
async def _shutdown() -> None:
    await ENGINE.stop()
    await music_mod.stop()


# --------------------------------------------------------------------------
# command handling
# --------------------------------------------------------------------------
async def _speak_reply(text: str) -> str | None:
    """Pre-render the spoken confirmation so the satellite/dashboard can play it.
    Returns a relative audio URL, or None on TTS failure (text still stands)."""
    try:
        wav = await clients.synthesize(text)
    except Exception as exc:  # noqa: BLE001
        log.warning("reply TTS failed: %s", exc)
        return None
    name = f"reply-{uuid.uuid4().hex[:10]}.wav"
    with open(os.path.join(config.ANNOUNCE_CACHE_DIR, name), "wb") as fh:
        fh.write(wav)
    return f"/audio/{name}"


async def _broadcast_lists(event_type: str, **extra) -> None:
    """Fan a fresh snapshot of the shared active lists to the dashboard so any
    open list view refreshes. Best-effort; a lists-service hiccup here must not
    fail the spoken confirmation the caller already built."""
    try:
        items = await lists_mod.fetch()
    except Exception as exc:  # noqa: BLE001
        log.warning("list snapshot for %s failed: %s", event_type, exc)
        return
    await events.emit(event_type, items=items, **extra)


# The overlay only has todo/shopping views; reminders are push-only.
_VIEWABLE = ("shopping", "todo")


def _view_type_for(items: list[dict]) -> str | None:
    """Which list view to pop for a set of just-added items. Prefer shopping,
    then todo (the kitchen's common cases); None if only reminders."""
    types = {it.get("type") for it in items}
    for t in _VIEWABLE:
        if t in types:
            return t
    return None


async def _pop_list(list_type: str) -> None:
    """Open the shared list view on the kiosk showing the current state — so a
    voice add/complete shows the LIST, not just a spoken read-back (a bare
    'added it' on screen isn't useful)."""
    try:
        items = await lists_mod.fetch()
    except Exception as exc:  # noqa: BLE001
        log.warning("pop list %s failed: %s", list_type, exc)
        return
    await events.emit("show_list", list_type=list_type, items=items)


def _summarize_turn(intent: str, result: dict) -> str:
    """One-line summary of an actionable turn, fed back as follow-up context."""
    parsed = result.get("parsed", {})
    if intent == "set_timer" and result.get("timer"):
        t = result["timer"]
        return f"you set a {fmt.timer_name(t)} for {fmt.humanize_seconds(t['duration_seconds'])}"
    if intent in ("add_items", "set_reminder"):
        names = ", ".join((i.get("text") or "").strip() for i in result.get("added") or [])
        return f"you added {names or 'items'} to your lists"
    if intent == "complete_item" and result.get("completed"):
        return "you checked off " + ", ".join(
            (i.get("text") or "").strip() for i in result["completed"])
    if intent in ("remove_items", "clear_list") and result.get("removed"):
        return "you removed " + ", ".join(
            (i.get("text") or "").strip() for i in result["removed"])
    if intent == "timer_adjust" and result.get("timer"):
        return f"you adjusted the {fmt.timer_name(result['timer'])}"
    if intent == "timer_cancel":
        return "you cancelled a timer"
    if intent in ("show_todos", "show_shopping"):
        return "you were shown a list"
    if intent == "timer_query":
        return "you asked how much time was left"
    if intent == "ask":
        return f"you asked: {parsed.get('query') or ''}"
    if intent == "sports":
        return f"you asked about {parsed.get('query') or 'sports'} and heard: " \
               f"{(result.get('response') or '')[:100]}"
    if intent == "weather":
        return f"you asked about the weather ({result.get('weather_when') or 'now'}) " \
               f"and heard: {(result.get('response') or '')[:100]}"
    if intent == "play_music":
        name = (result.get("music") or {}).get("name")
        return f"you started playing {name}" if name else "you resumed the music"
    if intent == "music_control":
        return f"you told the music player: {parsed.get('music_action')}"
    if intent == "music_query":
        return "you asked what music is playing"
    return (result.get("response") or "")[:120]


async def _finalize(result: dict, intent: str) -> dict:
    """Common tail: render the spoken reply, emit the response event, and record
    the turn for follow-up context."""
    result["audio_url"] = await _speak_reply(result["response"])
    await events.emit(
        "response", text=result["response"], audio_url=result["audio_url"], intent=intent
    )
    session_note(_summarize_turn(intent, result))
    return result


async def _after_list_change(items: list[dict]) -> None:
    """Pop the affected list view (or just refresh) after a bulk change."""
    view = _view_type_for(items)
    if view:
        await _pop_list(view)
    else:
        await _broadcast_lists("list_updated")


async def _run_pending(pending: dict) -> dict:
    """Execute a confirmed destructive bulk op."""
    op = pending["op"]
    if op == "clear":
        removed = await lists_mod.clear(pending.get("list_type"))
        await _after_list_change(removed)
        return {"intent": "clear_list", "removed": removed, "ok": True,
                "response": fmt.confirm_cleared(pending.get("list_type"), removed)}
    removed = await lists_mod.delete_ids(pending["items"])
    await _after_list_change(removed)
    return {"intent": "remove_items", "removed": removed, "ok": True,
            "response": fmt.confirm_removed(removed)}


async def handle_command(command: str, followup: bool = False) -> dict:
    """Parse a command and act on it. Emits thinking/response events; returns a
    structured result. `followup` = a continued-conversation turn (no wake word):
    parse with session context and drop non-actionable speech silently."""
    command = command.strip()
    if not command:
        return {"intent": "none", "response": "I didn't catch that.", "ok": False}

    # A destructive bulk op is awaiting a yes/no? Resolve that first.
    pending = session_pending()
    if pending:
        decision = _affirmation(command)
        if decision is not None:
            session_clear_pending()
            if followup:
                await events.emit("transcript", text=command)
            if decision == "no":
                return await _finalize(
                    {"intent": "none", "response": "Okay, I left it.", "ok": True}, "none")
            result = await _run_pending(pending)
            return await _finalize(result, result["intent"])
        session_clear_pending()   # user moved on -> abandon the pending op

    context = session_context() if followup else None
    if not followup:
        await events.emit("thinking", command=command)
    parsed = await intent_mod.parse(command, context=context)
    intent = parsed["intent"]
    log.info("intent=%s followup=%s parsed=%s", intent, followup, parsed)

    if followup and intent == "none":
        # Background chatter / not addressed to us. Drop silently: no events, no
        # audio, no dashboard flash — a dropped follow-up must be invisible. The
        # satellite reads intent "none" and closes the follow-up window.
        log.info("followup dropped as not-for-us: %r", command)
        return {"intent": "none", "response": "", "ok": False, "silent": True}
    if followup:
        # Actionable follow-up: surface caption + thinking now (deferred past the
        # none gate so background chatter never flashes on the dashboard).
        await events.emit("transcript", text=command)
        await events.emit("thinking", command=command)

    result: dict = {"intent": intent, "parsed": parsed}

    if intent == "set_timer":
        if not parsed["duration_seconds"]:
            result["response"] = "How long should I set the timer for?"
            result["ok"] = False
        else:
            timer = await ENGINE.create(
                parsed["label"], parsed["duration_seconds"], parsed["sound_theme"]
            )
            result["timer"] = timer
            result["response"] = fmt.confirm_set(timer)
            result["ok"] = True
            await events.emit("timer_created", timer=timer, timers=ENGINE.active())

    elif intent == "timer_query":
        if parsed["label"]:
            t = next((x for x in ENGINE.active() if x["label"] == parsed["label"]), None)
            timers = [t] if t else []
        else:
            timers = ENGINE.active()
        result["response"] = fmt.report_query(timers)
        result["ok"] = True

    elif intent == "timer_adjust":
        delta = parsed["duration_seconds"] or 0
        timer = await ENGINE.adjust(parsed["label"], delta)
        if timer:
            result["timer"] = timer
            result["response"] = fmt.confirm_adjust(timer)
            result["ok"] = True
            await events.emit("timer_updated", timer=timer, timers=ENGINE.active())
        else:
            result["response"] = "I couldn't find that timer."
            result["ok"] = False

    elif intent == "timer_cancel":
        if parsed["scope"] == "all":
            cancelled = ENGINE.cancel_all()
            n = len(cancelled)
            result["response"] = (
                "Cancelled all timers." if n else "There were no timers to cancel."
            )
            result["ok"] = True
            await events.emit("timer_cancelled", scope="all", timers=ENGINE.active())
        else:
            timer = ENGINE.cancel(parsed["label"])
            if timer:
                result["timer"] = timer
                result["response"] = fmt.confirm_cancel(timer)
                result["ok"] = True
                await events.emit(
                    "timer_cancelled", timer=timer, timers=ENGINE.active()
                )
            else:
                result["response"] = "I couldn't find that timer."
                result["ok"] = False

    elif intent in ("add_items", "set_reminder"):
        try:
            added = await lists_mod.add_from_text(command)
        except Exception as exc:  # noqa: BLE001
            log.warning("list add failed: %s", exc)
            result["response"] = "Sorry, I couldn't reach the lists service."
            result["ok"] = False
        else:
            result["added"] = added
            result["response"] = fmt.summarize_added(added)
            result["ok"] = bool(added)
            session_set_added(added)   # enable a follow-up "scratch my last"
            # Pop the list so the kiosk shows the current state, not just the
            # spoken "added it". Reminder-only adds have no view -> refresh only.
            view = _view_type_for(added)
            if view:
                await _pop_list(view)
            else:
                await _broadcast_lists("list_updated", added=added)

    elif intent in ("show_todos", "show_shopping"):
        list_type = "todo" if intent == "show_todos" else "shopping"
        try:
            items = await lists_mod.fetch(types=(list_type,))
        except Exception as exc:  # noqa: BLE001
            log.warning("list fetch failed: %s", exc)
            result["response"] = "Sorry, I couldn't reach the lists service."
            result["ok"] = False
        else:
            result["items"] = items
            result["response"] = fmt.summarize_list(list_type, items)
            result["ok"] = True
            await events.emit("show_list", list_type=list_type, items=items)

    elif intent == "complete_item":
        target = parsed.get("item_text") or command
        try:
            items = await lists_mod.fetch(status="active")
            targets = await lists_mod.resolve_targets(target, items)
            done = await lists_mod.complete_ids(targets)
        except Exception as exc:  # noqa: BLE001
            log.warning("list complete failed: %s", exc)
            done = []
            result["response"] = "Sorry, I couldn't reach the lists service."
            result["ok"] = False
        if done:
            result["completed"] = done
            result["response"] = fmt.confirm_completed(done)
            result["ok"] = True
            await _after_list_change(done)   # completing isn't destructive -> just do it
        elif "response" not in result:
            result["response"] = "I couldn't find that on your list."
            result["ok"] = False

    elif intent == "remove_items":
        try:
            if parsed.get("item_text"):
                items = await lists_mod.fetch(status="active")
                targets = await lists_mod.resolve_targets(parsed["item_text"], items)
            else:  # "scratch my last" / "undo" -> whatever was just added
                targets = session_last_added()
        except Exception as exc:  # noqa: BLE001
            log.warning("list resolve failed: %s", exc)
            targets = []
            result["response"] = "Sorry, I couldn't reach the lists service."
            result["ok"] = False
        if targets and len(targets) > 1 and parsed.get("item_text"):
            # Named a target that hit MULTIPLE items -> confirm before wiping.
            # (Undo of a just-made add skips this — you clearly meant those.)
            session_set_pending("remove", targets)
            result["response"] = fmt.confirm_bulk_question("remove", targets)
            result["ok"] = True
        elif targets:
            removed = await lists_mod.delete_ids(targets)
            if not parsed.get("item_text"):
                session_set_added([])   # undo consumed; don't double-undo
            result["removed"] = removed
            result["response"] = fmt.confirm_removed(removed)
            result["ok"] = True
            await _after_list_change(removed)
        elif "response" not in result:
            result["response"] = "I couldn't find anything to remove."
            result["ok"] = False

    elif intent == "clear_list":
        list_type = parsed.get("list_type") or "all"
        try:
            items = await lists_mod.fetch(
                types=None if list_type == "all" else (list_type,), status="active")
        except Exception as exc:  # noqa: BLE001
            log.warning("list fetch failed: %s", exc)
            items = []
            result["response"] = "Sorry, I couldn't reach the lists service."
            result["ok"] = False
        else:
            if not items:
                result["response"] = fmt.confirm_cleared(list_type, [])
                result["ok"] = True
            else:
                session_set_pending("clear", items, list_type)   # always confirm a clear
                result["response"] = fmt.confirm_bulk_question("clear", items, list_type)
                result["ok"] = True

    elif intent == "play_music":
        try:
            sel = await music_mod.play(parsed.get("query"), parsed.get("media_type"))
        except music_mod.MusicUnavailable:
            result["response"] = "Sorry, I can't reach the music player."
            result["ok"] = False
        except LookupError:
            result["response"] = f"I couldn't find {parsed.get('query')} to play."
            result["ok"] = False
        except Exception as exc:  # noqa: BLE001
            log.warning("play_music failed: %s", exc)
            result["response"] = "Sorry, something went wrong starting the music."
            result["ok"] = False
        else:
            result["music"] = sel
            result["response"] = fmt.confirm_play(sel)
            result["ok"] = True
            # Pop the existing jukebox now-playing modal on the kiosk — it polls
            # live MA queue state, so voice-started music renders there for free.
            await events.emit("show_music", **sel)

    elif intent == "music_control":
        action = parsed.get("music_action")
        if not action:
            result["response"] = "Sorry, I didn't catch what to do with the music."
            result["ok"] = False
        else:
            try:
                await music_mod.control(action)
            except music_mod.MusicUnavailable:
                result["response"] = "Sorry, I can't reach the music player."
                result["ok"] = False
            except Exception as exc:  # noqa: BLE001
                log.warning("music_control %s failed: %s", action, exc)
                result["response"] = "Sorry, that didn't work."
                result["ok"] = False
            else:
                result["response"] = fmt.confirm_music_control(action)
                result["ok"] = True

    elif intent == "music_query":
        try:
            np = music_mod.now_playing()
        except music_mod.MusicUnavailable:
            result["response"] = "Sorry, I can't reach the music player."
            result["ok"] = False
        else:
            result["now_playing"] = np
            result["response"] = fmt.now_playing_phrase(np)
            result["ok"] = True
            if np:
                await events.emit("show_music", **np)

    elif intent == "sports":
        sports_result = None
        try:
            sports_result = await sports_mod.handle(parsed)
        except Exception as exc:  # noqa: BLE001 — unofficial API; never fatal
            log.warning("sports lookup failed, falling back to ask: %s", exc)
        if sports_result:
            result.update(sports_result)
            # Seed ask history so a pronoun follow-up ("who do they play
            # next?") routed to the smart model knows what we just said.
            ask_mod.remember(command, sports_result["response"])
        else:
            # Unresolvable team/league or ESPN change -> slow-but-right path.
            await events.emit("ask_thinking", query=command)
            ask_result = await ask_mod.handle_ask(command)
            result["response"] = ask_result["response"]
            result["full"] = ask_result.get("full", "")
            result["ok"] = ask_result["ok"]

    elif intent == "weather":
        weather_result = await weather_mod.handle(parsed)   # None -> fall back
        if weather_result:
            result.update(weather_result)
            # Seed ask history so "what about the weekend?" routed to the smart
            # model knows what we just said (sports does the same).
            ask_mod.remember(command, weather_result["response"])
        else:
            # HA down, or a day outside the 6-day met.no window.
            await events.emit("ask_thinking", query=command)
            ask_result = await ask_mod.handle_ask(command)
            result["response"] = ask_result["response"]
            result["full"] = ask_result.get("full", "")
            result["ok"] = ask_result["ok"]

    elif intent == "ask":
        query = parsed.get("query") or command
        await events.emit("ask_thinking", query=query)
        ask_result = await ask_mod.handle_ask(query)
        result["response"] = ask_result["response"]
        result["full"] = ask_result.get("full", "")
        result["ok"] = ask_result["ok"]

    elif intent == "unclear":
        # Follow-up addressed to us but not mappable to an action. Give brief
        # feedback and KEEP the session open (non-silent reply -> satellite
        # re-arms) instead of dying silently like a background-chatter drop.
        result["response"] = "Sorry, I didn't catch that."
        result["ok"] = False

    else:  # none / out of scope
        result["response"] = "Sorry, I can only handle timers, lists, and questions right now."
        result["ok"] = False

    return await _finalize(result, intent)


# --------------------------------------------------------------------------
# HTTP surface
# --------------------------------------------------------------------------
@app.get("/health")
def health() -> dict:
    return {"ok": True, "active_timers": len(ENGINE.active())}


@app.post("/session/listening")
async def session_listening() -> dict:
    """Satellite pings this when it opens a follow-up listen window (no wake
    word). Emits a dashboard cue so the kiosk shows 'Listening…' — the user must
    be able to tell the mic is still open without guessing."""
    await events.emit("followup_listening")
    return {"ok": True}


@app.post("/wake")
async def wake(request: Request) -> dict:
    """Satellite posts the raw WAV utterance (pre-roll + speech)."""
    wav = await request.body()
    if not wav:
        raise HTTPException(400, "empty audio body")
    t0 = time.time()
    await events.emit("verifying")
    transcript = await clients.transcribe(wav)
    verified, command, score = verify.verify_and_extract(transcript)
    log.info("wake transcript=%r verified=%s score=%s cmd=%r",
             transcript, verified, score, command)
    if not verified:
        await events.emit("wake_rejected", transcript=transcript, score=score)
        return {"verified": False, "transcript": transcript, "score": score}

    await events.emit("transcript", text=command, wake_score=score)
    result = await handle_command(command)
    result.update(
        verified=True, transcript=transcript, score=score,
        latency_ms=round((time.time() - t0) * 1000),
    )
    return result


@app.post("/verify")
async def verify_wake(request: Request) -> dict:
    """Phase 1: stage-2 verification on the pre-roll (wake phrase audio only).
    Fast path so the satellite can chime the instant the wake word is confirmed,
    then start capturing the command. `command` is any speech already trailing
    the wake phrase in the pre-roll (if the user ran it together)."""
    wav = await request.body()
    if not wav:
        raise HTTPException(400, "empty audio body")
    t0 = time.time()
    await events.emit("verifying")
    transcript = await clients.transcribe(wav)
    verified, command, score = verify.verify_and_extract(transcript)
    log.info("verify transcript=%r verified=%s score=%s", transcript, verified, score)
    await events.emit("wake_confirmed" if verified else "wake_rejected",
                      score=score, transcript=transcript)
    return {
        "verified": verified, "score": score, "transcript": transcript,
        "command": command, "latency_ms": round((time.time() - t0) * 1000),
    }


@app.post("/command/audio")
async def command_audio(request: Request, followup: bool = False) -> dict:
    """Phase 2: the captured command utterance. Transcribe + act. No wake check —
    /verify already gated this turn. `followup=1` marks a continued-conversation
    turn (no wake word): background speech is dropped silently."""
    wav = await request.body()
    if not wav:
        raise HTTPException(400, "empty audio body")
    t0 = time.time()
    transcript = await clients.transcribe(wav)
    if not transcript:
        # Silence: on a follow-up, stay quiet; on a wake turn, say we missed it.
        if not followup:
            await events.emit("response", text="I didn't catch that.", intent="none")
        return {"ok": False, "transcript": "", "response": "",
                "intent": "none", "silent": followup}
    if not followup:
        await events.emit("transcript", text=transcript)
    result = await handle_command(transcript, followup=followup)
    result["transcript"] = transcript
    result["latency_ms"] = round((time.time() - t0) * 1000)
    return result


@app.post("/partial")
async def partial(request: Request, seq: int = 0) -> dict:
    """Live-caption snapshot. During command capture the satellite POSTs the
    ENTIRE buffer-so-far every ~400ms; each one is re-decoded as a normal
    full-context batch (so partials have zero accuracy penalty vs the final)
    and fanned to the dashboard as partial_transcript {text, seq}. Display-only
    by design: intent parsing always runs on the final /command/audio
    transcript, never a partial. Best-effort throughout — failures return ok
    False and cost nothing but a skipped caption frame."""
    wav = await request.body()
    if not wav:
        raise HTTPException(400, "empty audio body")
    try:
        text = await clients.transcribe(wav)
    except Exception as exc:  # noqa: BLE001 — captions are cosmetic
        log.debug("partial transcribe failed: %s", exc)
        return {"ok": False, "seq": seq}
    if text:
        await events.emit("partial_transcript", text=text, seq=seq)
    return {"ok": True, "seq": seq, "text": text}


# -- music ducking (satellite) ----------------------------------------------
@app.post("/music/duck")
async def music_duck() -> dict:
    """Satellite fires this on a stage-1 wake trigger / alarm start so speech
    isn't buried under the music. Best-effort — never errors."""
    try:
        await music_mod.duck()
    except Exception as exc:  # noqa: BLE001 — includes MusicUnavailable
        log.debug("duck skipped: %s", exc)
        return {"ok": False}
    return {"ok": True}


@app.post("/music/unduck")
async def music_unduck() -> dict:
    try:
        await music_mod.unduck()
    except Exception as exc:  # noqa: BLE001
        log.debug("unduck skipped: %s", exc)
        return {"ok": False}
    return {"ok": True}


@app.get("/music/state")
def music_state() -> dict:
    """Debug/CLI peek at what the kitchen queue is doing."""
    try:
        return {"ok": True, "now_playing": music_mod.now_playing()}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc)}


@app.post("/transcribe")
async def transcribe_audio(request: Request) -> dict:
    """Raw transcription (no intent). Used by the satellite to listen for a
    'stop' barge-in while an alarm is ringing."""
    wav = await request.body()
    if not wav:
        raise HTTPException(400, "empty audio body")
    return {"transcript": await clients.transcribe(wav)}


@app.post("/command")
async def command(payload: dict = Body(...)) -> dict:
    """Text bypass for testing and future text-driven paths. `followup: true`
    exercises the continued-conversation path (session context + silent none)."""
    text = str(payload.get("text", "")).strip()
    if not text:
        raise HTTPException(400, "missing 'text'")
    t0 = time.time()
    result = await handle_command(text, followup=bool(payload.get("followup")))
    result["latency_ms"] = round((time.time() - t0) * 1000)
    return result


# -- timer REST (dashboard + testing) --------------------------------------
@app.get("/timers")
def list_timers() -> dict:
    return {"timers": ENGINE.active()}


@app.post("/timers/{timer_id}/dismiss")
async def dismiss_timer(timer_id: str) -> dict:
    timer = ENGINE.dismiss(timer_id)
    if not timer:
        raise HTTPException(404, "no ringing timer with that id")
    await events.emit("timer_dismissed", timer=timer, timers=ENGINE.active())
    return {"ok": True, "timer": timer}


@app.post("/timers/{timer_id}/cancel")
async def cancel_timer(timer_id: str) -> dict:
    timer = ENGINE.cancel_by_id(timer_id)
    if not timer:
        raise HTTPException(404, "no active timer with that id")
    await events.emit("timer_cancelled", timer=timer, timers=ENGINE.active())
    return {"ok": True, "timer": timer}


@app.post("/timers/{timer_id}/add")
async def add_time(timer_id: str, seconds: int = 60) -> dict:
    row = ENGINE.get(timer_id)
    if not row:
        raise HTTPException(404, "unknown timer")
    timer = await ENGINE.adjust(row.get("label"), seconds)
    await events.emit("timer_updated", timer=timer, timers=ENGINE.active())
    return {"ok": True, "timer": timer}


@app.get("/timers/{timer_id}/announcement.wav")
def announcement(timer_id: str) -> FileResponse:
    path = ENGINE.announce_wav_path(timer_id)
    if not path or not os.path.exists(path):
        raise HTTPException(404, "no announcement for that timer")
    return FileResponse(path, media_type="audio/wav")


# -- lists REST (dashboard) -------------------------------------------------
@app.get("/lists")
async def get_lists() -> dict:
    """Shared active items, for the kiosk to render/restore a list view. The
    dashboard proxies this (orchestrator sets no CORS headers)."""
    try:
        items = await lists_mod.fetch()
    except Exception as exc:  # noqa: BLE001
        log.warning("GET /lists failed: %s", exc)
        raise HTTPException(502, f"lists service unreachable: {exc}")
    return {"items": items}


@app.post("/lists/items/{item_id}/complete")
async def complete_list_item(item_id: int) -> dict:
    """Check an item off from a touchscreen tap. Emits list_updated with a fresh
    shared snapshot so every kiosk's open list view reconciles."""
    try:
        done = await lists_mod.complete_by_id(item_id)
    except Exception as exc:  # noqa: BLE001
        log.warning("complete list item %s failed: %s", item_id, exc)
        raise HTTPException(502, f"lists service unreachable: {exc}")
    if done is None:
        raise HTTPException(404, "no active item with that id")
    await _broadcast_lists("list_updated", completed=done)
    return {"ok": True, "item": done}


@app.get("/audio/{name}")
def audio(name: str) -> FileResponse:
    # basename guard against path traversal
    safe = os.path.basename(name)
    path = os.path.join(config.ANNOUNCE_CACHE_DIR, safe)
    if not os.path.exists(path):
        raise HTTPException(404, "no such audio")
    return FileResponse(path, media_type="audio/wav")
