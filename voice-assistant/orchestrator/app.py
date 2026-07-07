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

import logging
import os
import time
import uuid

from fastapi import Body, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse

from . import ask as ask_mod
from . import lists as lists_mod
from . import clients, config, events, format as fmt, intent as intent_mod, verify
from .timers import TimerEngine

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s"
)
log = logging.getLogger("orchestrator.app")

app = FastAPI(title="Kitchen Voice Orchestrator")
ENGINE: TimerEngine


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
    log.info("orchestrator up; %d active timer(s) restored", len(ENGINE.active()))


@app.on_event("shutdown")
async def _shutdown() -> None:
    await ENGINE.stop()


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


async def handle_command(command: str) -> dict:
    """Parse a command and act on it. Emits thinking/response events; returns a
    structured result."""
    command = command.strip()
    if not command:
        return {"intent": "none", "response": "I didn't catch that.", "ok": False}

    await events.emit("thinking", command=command)
    parsed = await intent_mod.parse(command)
    intent = parsed["intent"]
    log.info("intent=%s parsed=%s", intent, parsed)

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
            done = await lists_mod.complete_by_text(target)
        except Exception as exc:  # noqa: BLE001
            log.warning("list complete failed: %s", exc)
            done = None
            result["response"] = "Sorry, I couldn't reach the lists service."
            result["ok"] = False
        if done:
            result["completed"] = done
            result["response"] = fmt.confirm_complete(done)
            result["ok"] = True
            # Show the updated list after a voice complete, same as adds.
            if done.get("type") in _VIEWABLE:
                await _pop_list(done["type"])
            else:
                await _broadcast_lists("list_updated", completed=done)
        elif "response" not in result:
            result["response"] = "I couldn't find that on your list."
            result["ok"] = False

    elif intent == "ask":
        query = parsed.get("query") or command
        await events.emit("ask_thinking", query=query)
        ask_result = await ask_mod.handle_ask(query)
        result["response"] = ask_result["response"]
        result["full"] = ask_result.get("full", "")
        result["ok"] = ask_result["ok"]

    else:  # none / out of scope
        result["response"] = "Sorry, I can only handle timers and questions right now."
        result["ok"] = False

    result["audio_url"] = await _speak_reply(result["response"])
    await events.emit(
        "response", text=result["response"], audio_url=result["audio_url"], intent=intent
    )
    return result


# --------------------------------------------------------------------------
# HTTP surface
# --------------------------------------------------------------------------
@app.get("/health")
def health() -> dict:
    return {"ok": True, "active_timers": len(ENGINE.active())}


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
async def command_audio(request: Request) -> dict:
    """Phase 2: the captured command utterance (post-wake). Transcribe + act.
    No wake check here — /verify already gated this turn."""
    wav = await request.body()
    if not wav:
        raise HTTPException(400, "empty audio body")
    t0 = time.time()
    transcript = await clients.transcribe(wav)
    if not transcript:
        await events.emit("response", text="I didn't catch that.", intent="none")
        return {"ok": False, "transcript": "", "response": "I didn't catch that."}
    await events.emit("transcript", text=transcript)
    result = await handle_command(transcript)
    result["transcript"] = transcript
    result["latency_ms"] = round((time.time() - t0) * 1000)
    return result


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
    """Text bypass for testing and future text-driven paths."""
    text = str(payload.get("text", "")).strip()
    if not text:
        raise HTTPException(400, "missing 'text'")
    t0 = time.time()
    result = await handle_command(text)
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
