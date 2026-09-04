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
import contextvars
import io
import logging
import os
import re
import time
import uuid
import wave

import httpx
from fastapi import Body, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse

from . import answers as answers_mod
from . import ask as ask_mod
from . import climate as climate_mod
from . import covers as covers_mod
from . import home_control as home_mod
from . import lists as lists_mod
from . import music as music_mod
from . import broadcast as broadcast_mod
from . import camera as camera_mod
from . import clock as clock_mod
from . import find_phone as phone_mod
from . import people
from . import places as places_mod
from . import policy as policy_mod
from . import speaker as speaker_mod
from . import sports as sports_mod
from . import weather as weather_mod
from . import clients, config, events, format as fmt, intent as intent_mod, verify
from . import loudness, timing, turns as turns_mod, zones
from .timers import RINGING, TimerEngine

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s"
)
log = logging.getLogger("orchestrator.app")

app = FastAPI(title="Household Voice Orchestrator")
ENGINE: TimerEngine


# --------------------------------------------------------------------------
# conversation session (for follow-up turns — "also add milk", "make it 15")
# --------------------------------------------------------------------------
# Each satellite owns an independent session. A short summary of that room's
# last turn lets a follow-up resolve references without importing another
# room's conversation, confirmation, clarify slot, or "undo my last" state.
SESSION_TTL_S = 90.0


def _new_session() -> dict:
    return {"ts": 0.0, "summary": "", "last_added": [], "pending": None}


_SESSION: dict = _new_session()  # kitchen/default compatibility for old callers/tests
_SESSIONS: dict[str, dict] = {config.DEFAULT_SAT: _SESSION}


def _session() -> dict:
    """State for the in-flight satellite, defaulting legacy callers to the
    kitchen. `_CUR_SAT` is declared below and resolved when this runs, after
    module initialization has completed."""
    sat = _CUR_SAT.get() or config.DEFAULT_SAT
    return _SESSIONS.setdefault(sat, _new_session())


def session_set_pending(op: str, items: list[dict], list_type: str | None = None) -> None:
    """Stash a destructive bulk op awaiting a spoken yes/no."""
    session = _session()
    session["pending"] = {"op": op, "items": items, "list_type": list_type}
    session["ts"] = time.time()


def session_set_clarify(partial: str, question: str, kind: str = "timer",
                        label: str | None = None, theme: str | None = None,
                        owner: str | None = None) -> None:
    """Stash a command that stopped short, awaiting the missing piece. The
    partial TEXT is what matters: the next turn stitches the reply onto it and
    re-parses the whole thing, so a new slot needs no new grammar.

    `kind` names the slot family ("timer" / "add") so a reply is only ever
    stitched onto a partial of its own kind, and only that kind's fast path
    runs — "five minutes" answering "remind you to do what?" must not quietly
    become a timer. Label/theme ride along so the duration fast path can skip
    the re-parse without losing what the first parse worked out. `owner` is the
    speaker identified on THIS turn: the full utterance is longer and cleaner
    audio than the one-phrase reply, and a list add has to land on the list of
    whoever started the sentence."""
    session = _session()
    session["pending"] = {"op": "clarify", "partial": partial, "question": question,
                          "kind": kind, "label": label, "sound_theme": theme,
                          "owner": owner}
    session["ts"] = time.time()


def session_pending() -> dict | None:
    session = _session()
    if session.get("pending") and time.time() - session["ts"] <= SESSION_TTL_S:
        return session["pending"]
    return None


def session_clear_pending() -> None:
    _session()["pending"] = None


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
    session = _session()
    session["summary"] = summary
    session["ts"] = time.time()


def session_set_added(items: list[dict]) -> None:
    """Remember the items the last add produced so a follow-up 'undo' / 'scratch
    my last' can remove exactly them."""
    session = _session()
    session["last_added"] = items or []
    session["ts"] = time.time()


def session_last_added() -> list[dict]:
    session = _session()
    if time.time() - session["ts"] > SESSION_TTL_S:
        return []
    return session["last_added"]


def session_context() -> str | None:
    """The recent-turn summary if still fresh, else None (session expired)."""
    session = _session()
    if not session["summary"] or time.time() - session["ts"] > SESSION_TTL_S:
        return None
    return session["summary"]


# --------------------------------------------------------------------------
# wake arbitration (two satellites, one house)
# --------------------------------------------------------------------------
# First VERIFIED wake wins the turn (deterministic: arrival order at this
# single-threaded event loop); the loser is told suppressed=true and shadow-
# captures. Built-in correctness during music: the drowned kitchen mic fails
# verify, so the far family-room mic wins by default, not by racing. Checked
# twice per /verify — at entry (cheap, skips ASR when the race is already
# lost) and again after our own ASR verifies (the other mic may have finished
# verifying while ours was decoding).
# turn_id / rms_db / stage1 describe the winner's wake so the loser's /verify
# can be paired onto the winner's row (turns.other_*) — the evidence for
# attributing a room by which mic heard the speaker louder, see loudness.py.
_ARB = {"sat": None, "until": 0.0, "turn_id": None, "rms_db": None,
        "stage1": None, "at": 0.0}

# Wake turns handed to a louder same-hardware peer (config.ARB_LOUDNESS_GROUPS):
# the first mic's turn_id -> the sat that answers instead. Consumed when the
# first mic's /command/audio arrives and is demoted to a shadow. Bounded: a
# capture that never posts (empty VAD) would otherwise leave its key behind.
_ARB_HANDOFF: dict[str, str] = {}
_ARB_HANDOFF_MAX = 32


def _loudness_peers(a: str | None, b: str | None) -> bool:
    """Same hardware class, so wake loudness is directly comparable."""
    return bool(a and b and a != b
                and any(a in g and b in g for g in config.ARB_LOUDNESS_GROUPS))


def _in_loudness_group(sat: str | None) -> bool:
    return bool(sat and any(sat in g for g in config.ARB_LOUDNESS_GROUPS))


def _arb_holder(sat: str) -> str | None:
    """The OTHER satellite currently holding the turn, if any."""
    if _ARB["sat"] and _ARB["sat"] != sat and time.time() < _ARB["until"]:
        return _ARB["sat"]
    return None


def _arb_claim(sat: str, stage1: float | None = None,
               rms: float | None = None) -> None:
    now = time.time()
    _ARB.update(sat=sat, until=now + config.ARB_SUPPRESS_S,
                turn_id=None, rms_db=rms, stage1=stage1, at=now)


# Which satellites are inside a follow-up listen window right now (sat ->
# when it opened). A satellite in capture never posts /verify, so this is how
# the in-capture re-wake takes part in arbitration: see config.ARB_PEERS.
_FOLLOWUP_LISTEN: dict[str, float] = {}


def _followup_listening(sat: str) -> float | None:
    since = _FOLLOWUP_LISTEN.get(sat)
    if since is None or time.time() - since > config.FOLLOWUP_LISTEN_MAX_S:
        return None
    return since


def _peer_in_followup(sat: str) -> str | None:
    """A PEER mic (same audio space) that is mid-follow-up, if any."""
    for group in config.ARB_PEERS:
        if sat in group:
            for other in group:
                if other != sat and _followup_listening(other) is not None:
                    return other
    return None


async def _await_rewake_claim(sat: str) -> str | None:
    """A peer is in a follow-up listen: give its partial the chance to claim
    this wake before we do. Returns the claimant, or None on timeout."""
    deadline = time.time() + config.REWAKE_ARB_WAIT_S
    while time.time() < deadline:
        holder = _arb_holder(sat)
        if holder:
            return holder
        await asyncio.sleep(0.05)
    return _arb_holder(sat)


async def _note_wake_loudness(turn_id: str, wav: bytes, sat: str,
                              winner: str | None = None,
                              stage1: float | None = None,
                              rms: float | None = None) -> None:
    """Measure this wake's loudness off the chime path and file it.

    Runs as a task after the /verify response is on the wire: a few ms of
    pure-Python RMS that must not sit between the wake and the chime. For an
    arbitration loser, also pairs the reading onto the winner's row, so one
    row carries both mics' loudness and stage-1 scores for the same wake.
    """
    if rms is None:
        rms = loudness.peak_window_dbfs(wav)
    turns_mod.update(turn_id, wake_rms_db=rms)
    if winner is None:
        if _ARB["sat"] == sat and _ARB["rms_db"] is None:
            _ARB["rms_db"] = rms
        return
    if _ARB["sat"] == winner and _ARB["turn_id"]:
        turns_mod.update(_ARB["turn_id"], other_sat=sat, other_stage1=stage1,
                         other_rms_db=rms)
    log.info("arb evidence winner=%s rms=%s stage1=%s | loser=%s rms=%s stage1=%s",
             winner, _ARB["rms_db"], _ARB["stage1"], sat, rms, stage1)


async def _loudness_handoff(sat: str, winner: str, wav: bytes,
                            peak: float | None,
                            decoded: tuple | None) -> dict | None:
    """Hand the turn `winner` just claimed to `sat` if `sat` heard the wake
    louder by config.ARB_LOUDNESS_MARGIN_DB -- same-hardware peers only.

    Returns the /verify response that makes `sat` the primary (it chimes and
    captures), or None to leave the suppression in place. `decoded` is the
    stage-2 result if the caller already ran it; otherwise it runs here, and
    only once the loudness test has passed, so a plain loser costs no ASR.
    The winner's capture, when it posts, is demoted via _ARB_HANDOFF.
    """
    if not _loudness_peers(sat, winner):
        return None
    heard = _ARB["rms_db"]
    if heard is None or _ARB["sat"] != winner:
        return None
    rms = loudness.peak_window_dbfs(wav)
    if rms is None or rms - heard < config.ARB_LOUDNESS_MARGIN_DB:
        return None
    if decoded is None:
        decoded = await _decode_wake(wav)
    verified, command, transcript, score, decode = decoded
    if not verified:
        log.info("arb handoff refused: sat=%s louder by %.1f dB but stage-2 "
                 "rejected %r", sat, rms - heard, transcript)
        return None
    winner_turn = _ARB["turn_id"]
    turn_id = turns_mod.start(
        sat, "wake", verified=True, transcript=transcript, wake_score=score,
        decode=decode, stage1_score=peak, arb_turn_id=winner_turn,
        wake_rms_db=rms, **_wake_timings())
    if winner_turn:
        turns_mod.update(winner_turn, other_sat=sat, other_stage1=peak,
                         other_rms_db=rms, arb_winner=sat,
                         reject_reason="handoff")
        _ARB_HANDOFF[winner_turn] = sat
        while len(_ARB_HANDOFF) > _ARB_HANDOFF_MAX:
            del _ARB_HANDOFF[next(iter(_ARB_HANDOFF))]
    log.info("arb handoff %s -> %s: %.1f dBFS vs %.1f (margin %.1f) turn=%s",
             winner, sat, rms, heard, config.ARB_LOUDNESS_MARGIN_DB, turn_id)
    _ARB.update(sat=sat, turn_id=turn_id, rms_db=rms, stage1=peak,
                at=time.time())
    route = zones.route_for(sat)
    if route:
        asyncio.create_task(
            broadcast_mod.amp_wake(route["rooms"], route.get("volume")))
    await _turn_event("wake_confirmed", sat=sat, score=score,
                      transcript=transcript)
    return {"verified": True, "score": score, "transcript": transcript,
            "command": command, "decode": decode, "turn_id": turn_id,
            "handoff_from": winner}


# Which satellite the in-flight turn belongs to, so _finalize can route the
# reply (zones.py) without threading `sat` through every intent handler and all
# six _finalize call sites. A ContextVar rather than a module global because
# turns from different satellites can interleave on the event loop -- each
# request coroutine gets its own copy.
_CUR_SAT: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "current_sat", default=None)

# "argument not given", distinct from an explicit sat=None — which is a real
# value here, meaning the pre-rooms caller that reads as the kitchen.
_UNSET: object = object()


# --------------------------------------------------------------------------
# expiry -> alarm
# --------------------------------------------------------------------------
async def _turn_event(event_type: str, sat: str | None = _UNSET,
                      **fields: object) -> None:
    """Push a conversation event to the kitchen display — if it is that room's
    conversation.

    Captions, "thinking", the reply text, a list or a now-playing card: all of
    it is one room's turn, and the screen lives in the kitchen. A bath command
    painting the kitchen display shows a conversation nobody standing there is
    having (Brad, 2026-08-09, watching "Okay, louder" appear while the music it
    referred to was two floors up).

    Defaults to the satellite of the turn in flight. Endpoints that fire
    outside a dispatched turn — the wake badge, partial captions — pass their
    own; a caller with no room at all reads as the kitchen, which is what every
    one of these events was before rooms existed.
    """
    if sat is _UNSET:
        sat = _CUR_SAT.get()
    if not events.on_dashboard(sat):
        return
    await events.emit(event_type, **fields)


async def _timer_event(event_type: str, timer: dict | None = None,
                       **fields: object) -> None:
    """Push a timer change to the kitchen display — if it is that room's timer.

    The display is one room's board, not the household's. A master bath timer
    appearing on it counts down a sound the person standing in the kitchen
    cannot hear, which reads as an alarm that failed rather than one ringing
    somewhere else (Brad, 2026-08-08, watching bath tests pop cards).

    House-wide events with no single timer (cancel-all) still go through: the
    board has to drop whatever it was showing. Only the list is scoped."""
    if timer is not None and not events.on_dashboard(timer.get("sat")):
        log.info("timer event %s not for the display (sat=%s)",
                 event_type, timer.get("sat"))
        return
    if timer is not None:
        fields["timer"] = timer
    await events.emit(event_type, timers=ENGINE.active(config.DASHBOARD_SAT),
                      **fields)


async def _on_timer_expire(timer: dict) -> None:
    announce_url = None
    if ENGINE.announce_wav_path(timer["id"]):
        announce_url = f"/timers/{timer['id']}/announcement.wav"
    await _timer_event(
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
    # A restart during a find-phone ring skips its volume restore; undo it.
    asyncio.create_task(phone_mod.restore_stranded())
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
    await _turn_event(event_type, items=items, **extra)


# List views the kiosk can render, in the order an add prefers them.
_VIEWABLE = ("shopping", "todo", "reminder")


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
    await _turn_event("show_list", list_type=list_type, items=items)


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
    if intent == "timer_rename" and result.get("timer"):
        return f"you renamed it to the {fmt.timer_name(result['timer'])}"
    if intent == "timer_cancel":
        return "you cancelled a timer"
    if intent in ("show_todos", "show_shopping", "show_reminders"):
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
    if intent == "time_query":
        return f"you asked what {parsed.get('time_kind') or 'time'} it is " \
               f"and heard: {(result.get('response') or '')[:100]}"
    if intent in ("business_hours", "place_search"):
        return f"you asked about {parsed.get('query') or 'a nearby place'} and heard: " \
               f"{(result.get('response') or '')[:100]}"
    if intent == "play_music":
        name = (result.get("music") or {}).get("name")
        return f"you started playing {name}" if name else "you resumed the music"
    if intent == "music_control":
        return f"you told the music player: {parsed.get('music_action')}"
    if intent == "music_query":
        return "you asked what music is playing"
    if intent in ("home_control", "cover_set", "climate_set"):
        return f"you gave a home command and heard: {(result.get('response') or '')[:100]}"
    if intent == "broadcast" and result.get("broadcast"):
        b = result["broadcast"]
        return f"you broadcast {parsed.get('query')!r} to {b['spoken']}"
    if intent == "show_camera" and result.get("camera"):
        # Worth a real summary rather than the generic tail: it is what lets a
        # follow-up "close it" resolve to the camera and not the music.
        return (f"you put {camera_mod.SPOKEN[result['camera']]}'s camera "
                f"on the kitchen display")
    if intent == "close_camera":
        return "you closed the camera on the kitchen display"
    if intent == "find_phone":
        if "stopped" in result:
            return "you stopped the find-my-phone ringing"
        p = result.get("phone")
        return (f"you rang {p['spoken']}" if p
                else "you asked whose phone should ring")
    return (result.get("response") or "")[:120]


async def _finalize(result: dict, intent: str) -> dict:
    """Common tail: render the spoken reply, emit the response event, and record
    the turn for follow-up context.

    Zone-routed satellites (zones.py) take a different tail: the reply text is
    published to a whole-home audio zone and no audio_url is produced, which is
    what stops the satellite playing it locally (assistant.py:811). We skip our
    own TTS entirely there — Node-RED re-renders from text — so this path is a
    render cheaper, not more expensive."""
    route = zones.route_for(_CUR_SAT.get())
    if route and result.get("response"):
        try:
            await broadcast_mod.send(route["rooms"], result["response"],
                                     route.get("volume"), route.get("voice"))
            result["audio_url"] = None
            result["reply_zone"] = route["rooms"]
            # Short lead-in mute only; the echo check does the real work of
            # keeping our own answer out of the follow-up.
            result["mute_ms"] = zones.mute_ms_for(result["response"])
            zones.note_reply(_CUR_SAT.get(), result["response"])
        except Exception as exc:  # noqa: BLE001 — HA/MQTT down
            # Fall back to answering on the satellite rather than losing the
            # turn: a reply from the wrong speaker beats silence.
            log.warning("zone reply failed (%s), falling back to satellite: %s",
                        route["rooms"], exc)
            result["audio_url"] = await _speak_reply(result["response"])
    else:
        result["audio_url"] = await _speak_reply(result["response"])
    await _turn_event(
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


async def _parse_clarify_reply(clarify: dict, reply: str) -> dict:
    """Parse the answer to a slot question.

    Fast path first: "eight minutes" is the overwhelmingly common answer to "for
    how long?", and reading it locally saves a ~2s LLM round trip on the one
    turn where the user is already waiting on us. The label and theme were
    resolved when we asked the question, so the fast path loses nothing.
    Anything the duration reader doesn't recognise goes to the parser with the
    partial command stitched back on.

    Gated on the TIMER slot: a reminder's content can read as a bare duration
    ("remind me to" -> "five minutes"), and silently turning that into a timer
    would lose the reminder without ever saying so."""
    if clarify.get("kind") == "timer":
        seconds = intent_mod.spoken_duration(reply)
        if seconds is not None:
            log.info("clarify fast path: %r -> %ss", reply, seconds)
            return intent_mod.validate({
                "intent": "set_timer", "duration_seconds": seconds,
                "label": clarify.get("label"),
                "sound_theme": clarify.get("sound_theme"),
            })
    if clarify.get("kind") == "timer_rename":
        parsed = intent_mod.fast_parse_timer_rename(
            f"{clarify['partial']} {reply}".strip())
        if parsed is not None and parsed.get("new_label"):
            parsed["label"] = clarify.get("label")
            log.info("rename clarify fast path: %r -> %r",
                     reply, parsed["new_label"])
            return parsed
    return await intent_mod.parse_clarify(
        clarify["partial"], reply, clarify["question"], _CUR_SAT.get())


async def _speaker_name(speaker_task: asyncio.Task | None) -> str | None:
    """This turn's voice-identified speaker, or None (text turn, speaker ID
    not active, service down, or "unsure"). None ALWAYS means today's
    fallback behavior — identification never guesses."""
    if speaker_task is None:
        return None
    try:
        ident = await speaker_task
    except Exception as exc:  # noqa: BLE001 — identification must not break the turn
        log.warning("speaker task failed: %s", exc)
        return None
    if not ident or ident.get("speaker") == "unsure":
        return None
    return ident["speaker"]


async def handle_command(command: str, followup: bool = False,
                         speaker_task: asyncio.Task | None = None) -> dict:
    """Parse a command and act on it. Emits thinking/response events; returns a
    structured result. `followup` = a continued-conversation turn (no wake word):
    parse with session context and drop non-actionable speech silently.
    `speaker_task` resolves to speaker.identify() of this turn's audio (only
    set by /command/audio in active mode); person-dependent handlers await it
    lazily to route by voice."""
    command = command.strip()
    if not command:
        return {"intent": "none", "response": "I didn't catch that.", "ok": False}

    # "Whose phone?" is awaiting a name? Resolve without an LLM parse: the
    # answer is one word ("Brad's", "adrienne"), and find_phone.resolve
    # strips the filler. Anything that is neither a name nor a "no" means
    # the user moved on -> abandon and fall through to a normal parse.
    pending = session_pending()
    if pending and pending["op"] == "find_phone":
        session_clear_pending()
        entry = phone_mod.resolve(command)
        if entry is not None or _affirmation(command) == "no":
            if followup:
                await _turn_event("transcript", text=command)
            if entry is None:
                return await _finalize(
                    {"intent": "none", "response": "Okay.", "ok": True}, "none")
            try:
                result = {"intent": "find_phone",
                          **await phone_mod.ring_and_reply(entry)}
            except Exception as exc:  # noqa: BLE001 — HA down / notify failed
                log.warning("find phone ring failed: %s", exc)
                result = {"intent": "find_phone", "ok": False,
                          "response": "Sorry, I couldn't reach the phone."}
            return await _finalize(result, "find_phone")
        pending = None

    # An incomplete command is awaiting its missing piece ("set a timer for" ->
    # "Sure, for how long?"). Don't return here — stitch the reply onto the
    # partial, re-parse, and let the answer fall through the normal dispatch
    # below, so "eight minutes" ends up in exactly the same set_timer branch a
    # complete command would.
    #
    # FOLLOW-UP TURNS ONLY. A fresh wake word means the user gave up on the
    # question and started over; stitching their new command onto the abandoned
    # partial would produce nonsense.
    clarify = None
    if pending and pending["op"] == "clarify":
        session_clear_pending()
        if followup:
            clarify = pending
        pending = None

    # A destructive bulk op is awaiting a yes/no? Resolve that first.
    if pending:
        decision = _affirmation(command)
        if decision is not None:
            session_clear_pending()
            if followup:
                await _turn_event("transcript", text=command)
            if decision == "no":
                return await _finalize(
                    {"intent": "none", "response": "Okay, I left it.", "ok": True}, "none")
            result = await _run_pending(pending)
            return await _finalize(result, result["intent"])
        session_clear_pending()   # user moved on -> abandon the pending op

    context = session_context() if followup else None
    if not followup:
        await _turn_event("thinking", command=command)
    # Deterministic room-scoped fast path, analogous to HA's local intent
    # matching. An exact curated alias needs neither classifier latency nor a
    # probabilistic label; fuzzy aliases still go through the classifier and
    # can never override another intent.
    if (intent_mod.is_camera_back(command)
            and events.on_dashboard(_CUR_SAT.get())
            and await camera_mod.is_open()):
        # "go back" is a camera command only while a camera is actually up, and
        # the display is the only thing that knows — the on-screen Back button
        # closes a view without telling us. The state check costs a LAN round
        # trip, so it is gated behind the phrase match and never runs on a
        # normal turn.
        parsed = intent_mod.validate({"intent": "close_camera"})
        log.info("camera dismissal bypassed classifier: %r", command)
    elif home_mod.has_exact_match(command, _CUR_SAT.get()):
        log.info("exact home command bypassed classifier: %r", command)
        parsed = intent_mod.validate({
            "intent": "home_control",
            "query": command,
        })
    elif (cover := intent_mod.fast_parse_cover_level(
            command, _CUR_SAT.get())) is not None:
        # A named blind plus a named level is unambiguous, and the classifier
        # has no cover_set rule to read it with anyway -- the grammar IS the
        # intent here, not a shortcut around one.
        parsed = cover
        log.info("deterministic cover_set bypassed classifier: %r", command)
    elif (clim := intent_mod.fast_parse_climate_setpoint(
            command, _CUR_SAT.get())) is not None:
        # Same idea one appliance over: a thermostat word plus a degree in a
        # room with a mini split. Live 2026-08-30 "set temperature to 72" in a
        # kid's room reached home_control as a button phrase and was refused.
        parsed = clim
        log.info("deterministic climate_set bypassed classifier: %r", command)
    elif clarify:
        parsed = await _parse_clarify_reply(clarify, command)
    elif followup and (
            rename := intent_mod.fast_parse_timer_rename(command)) is not None:
        parsed = rename
        log.info("deterministic timer_rename bypassed follow-up classifier: %r",
                 command)
    elif not followup and (fast := intent_mod.fast_parse(command)) is not None:
        parsed = fast
        log.info("deterministic %s bypassed classifier: %r",
                 parsed["intent"], command)
    else:
        parsed = await intent_mod.parse(
            command, context=context, sat=_CUR_SAT.get())
    if parsed["intent"] == "weather" and not parsed.get("weather_location"):
        named_weather = intent_mod.fast_parse_weather_location(command)
        if named_weather is not None:
            parsed = {
                **parsed,
                "weather_location": named_weather["weather_location"],
                "weather_when": named_weather["weather_when"],
            }
            log.info("restored deterministic named weather slots: %r", command)
    intent = parsed["intent"]
    log.info("intent=%s followup=%s clarify=%s parsed=%s",
             intent, followup, bool(clarify), parsed)

    # The classifier reads a timer command that stopped before its duration as
    # "unclear" — which is honest, but costs the user the turn. Force it onto
    # the slot-fill path; the duration stays null, so it lands in the "ask how
    # long" branch below.
    if intent in ("unclear", "none") and intent_mod.is_truncated_timer(command):
        log.info("truncated timer rescued from intent=%s: %r", intent, command)
        parsed = {**parsed, "intent": "set_timer", "duration_seconds": None}
        intent = "set_timer"
    elif intent in ("unclear", "none"):
        # Same rescue, one slot over: "remind me to" / "add to my to-do list"
        # cut off before the thing itself (live 2026-07-27).
        add_intent = intent_mod.is_truncated_add(command)
        if add_intent:
            log.info("truncated %s rescued from intent=%s: %r",
                     add_intent, intent, command)
            parsed = {**parsed, "intent": add_intent, "missing_content": True}
            intent = add_intent
    if intent in ("none", "unclear") and not followup and command:
        # The classifier does not know every curated phrase ("it's story
        # time" came back none, live 2026-08-25; "show me pac man" came back
        # unclear three times running, live 2026-08-27). A room-scoped alias
        # that clears the home_control threshold is the same decision that
        # path already makes, so take it here rather than apologising.
        rescued = home_mod.fuzzy_match(command, _CUR_SAT.get())
        if rescued:
            log.info("%s rescued by home command %s: %r",
                     intent, rescued, command)
            parsed = {**parsed, "intent": "home_control", "query": command}
            intent = "home_control"
    if intent == "play_music" and command:
        # A room's effect buttons are named like songs ("pac man", "waves",
        # "dino stomp") and the classifier cannot see the room's table. Live
        # 2026-08-27 and 08-31: "sorry i didnt touch that give me pac man"
        # became play_music, and the resolver then fuzzy-matched the album
        # "Piano Man" at 75. A near-exact room alias for the query or for the
        # whole command is the stronger claim on the words; "play the album
        # piano man" matches no alias and stays music.
        for text in (parsed.get("query"), command):
            pressed = home_mod.strong_match(text, _CUR_SAT.get())
            if pressed:
                log.info("play_music overridden by home command %s: %r",
                         pressed, text)
                parsed = {**parsed, "intent": "home_control", "query": text}
                intent = "home_control"
                break
    if followup and intent == "none":
        # Background chatter / not addressed to us. Drop silently: no events, no
        # audio, no dashboard flash — a dropped follow-up must be invisible. The
        # satellite reads intent "none" and closes the follow-up window.
        log.info("followup dropped as not-for-us: %r", command)
        return {"intent": "none", "response": "", "ok": False, "silent": True}
    if followup:
        # Actionable follow-up: surface caption + thinking now (deferred past the
        # none gate so background chatter never flashes on the dashboard).
        await _turn_event("transcript", text=command)
        await _turn_event("thinking", command=command)

    result: dict = {"intent": intent, "parsed": parsed}

    if intent == "set_timer":
        if not parsed["duration_seconds"]:
            if clarify:
                # We already asked once and still have no duration. A second
                # "for how long?" is a loop, not a conversation — let it go.
                result["response"] = "Okay, never mind."
                result["ok"] = False
            else:
                question = fmt.ask_timer_duration(parsed["label"])
                result["response"] = question
                result["ok"] = False
                # Tells the satellite to hold the mic open longer than a normal
                # follow-up: we just asked a question the user has to think about.
                result["awaiting_slot"] = True
                session_set_clarify(command, question, kind="timer",
                                    label=parsed["label"],
                                    theme=parsed["sound_theme"])
        elif (parsed["label"] and intent_mod.is_implicit_adjust(command)
              and any(t["label"] == parsed["label"] for t in ENGINE.active())):
            # "three minutes to my call fire timer" with a call fire timer
            # already running: an add-time command that lost its verb (see
            # is_implicit_adjust). A duplicate timer is the worse mistake.
            timer = await ENGINE.adjust(parsed["label"], parsed["duration_seconds"],
                                        _CUR_SAT.get())
            intent = parsed["intent"] = "timer_adjust"
            result["timer"] = timer
            result["response"] = fmt.confirm_adjust(timer)
            result["ok"] = True
            await _timer_event("timer_updated", timer=timer)
        else:
            timer = await ENGINE.create(
                parsed["label"], parsed["duration_seconds"],
                parsed["sound_theme"], _CUR_SAT.get()
            )
            result["timer"] = timer
            result["response"] = fmt.confirm_set(timer)
            result["ok"] = True
            await _timer_event("timer_created", timer=timer)

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
        timer = await ENGINE.adjust(parsed["label"], delta, _CUR_SAT.get())
        if timer:
            result["timer"] = timer
            result["response"] = fmt.confirm_adjust(timer)
            result["ok"] = True
            await _timer_event("timer_updated", timer=timer)
        else:
            result["response"] = "I couldn't find that timer."
            result["ok"] = False

    elif intent == "timer_rename":
        if not parsed.get("new_label"):
            if clarify:
                result["response"] = "Okay, never mind."
                result["ok"] = False
            else:
                question = fmt.ask_timer_name()
                result["response"] = question
                result["ok"] = False
                result["awaiting_slot"] = True
                session_set_clarify(command, question, kind="timer_rename",
                                    label=parsed.get("label"))
        else:
            timer = await ENGINE.rename(
                parsed.get("label"), parsed["new_label"], _CUR_SAT.get())
            if timer:
                result["timer"] = timer
                result["response"] = fmt.confirm_rename(timer)
                result["ok"] = True
                await _timer_event("timer_updated", timer=timer)
            else:
                result["response"] = "I couldn't find that timer."
                result["ok"] = False

    elif intent == "timer_cancel":
        # Snapshot ringing state BEFORE cancel mutates it: cancelling a
        # ringing timer must also silence the satellite's alarm sound.
        ringing_ids = {t["id"] for t in ENGINE.active() if t["state"] == RINGING}
        if parsed["scope"] == "all":
            cancelled = ENGINE.cancel_all()
            n = len(cancelled)
            result["response"] = (
                "Cancelled all timers." if n else "There were no timers to cancel."
            )
            result["ok"] = True
            for t in cancelled:
                if t["id"] in ringing_ids:
                    await events.alarm_stop(t.get("sat"))
            await _timer_event("timer_cancelled", scope="all")
        else:
            timer = ENGINE.cancel(parsed["label"], _CUR_SAT.get())
            if timer and timer["id"] in ringing_ids:
                await events.alarm_stop(timer.get("sat"))
            if timer:
                result["timer"] = timer
                result["response"] = fmt.confirm_cancel(timer)
                result["ok"] = True
                await _timer_event("timer_cancelled", timer=timer)
            else:
                result["response"] = "I couldn't find that timer."
                result["ok"] = False

    elif intent in ("add_items", "set_reminder") and parsed.get("missing_content"):
        if clarify:
            # We asked once and still have nothing to add. A second "what?" is
            # a loop, not a conversation — same rule as the timer slot.
            result["response"] = "Okay, never mind."
            result["ok"] = False
        else:
            question = fmt.ask_add_content(intent)
            result["response"] = question
            result["ok"] = False
            result["awaiting_slot"] = True   # satellite holds the mic longer
            session_set_clarify(command, question, kind="add",
                                owner=await _speaker_name(speaker_task))

    elif intent in ("add_items", "set_reminder"):
        # The companion types each item from the framing words in the text it
        # is given ("remind me", "todo" — see lists.py), so on a clarify turn
        # it must see the STITCHED command: the bare reply ("call the dentist
        # at five") would come back a to-do instead of a reminder.
        text, owner = command, None
        if clarify and clarify.get("kind") == "add":
            text = f"{clarify['partial']} {command}".strip()
            owner = clarify.get("owner")
        # "remind brad to…": the item belongs to the person NAMED, not the
        # speaker — it lands on their list and their phone. The text goes to
        # the companion in first person (see people.py for why).
        target, text = people.target_in(text)
        owner = target or owner or await _speaker_name(speaker_task)
        private = intent_mod.wants_private(text)
        try:
            added = await lists_mod.add_from_text(text, owner=owner, private=private)
        except Exception as exc:  # noqa: BLE001
            log.warning("list add failed: %s", exc)
            result["response"] = "Sorry, I couldn't reach the lists service."
            result["ok"] = False
        else:
            result["added"] = added
            result["response"] = fmt.summarize_added(added, for_name=target)
            # Name the voice-resolved owner aloud (shopping is household-
            # shared, so attribution is only worth speaking for the rest):
            # cheap trust-building plus the audible correction path. A private
            # item says so instead — they asked for the quiet path and need to
            # hear that it took.
            if private:
                result["response"] += " Just on your phone."
            elif target and any(i.get("type") != "shopping" for i in added):
                result["response"] += f" It'll pop up on {target.title()}'s phone."
            elif owner and any(i.get("type") != "shopping" for i in added):
                result["response"] += f" On {owner.title()}'s list."
            result["ok"] = bool(added)
            session_set_added(added)   # enable a follow-up "scratch my last"
            # Pop the list so the kiosk shows the current state, not just the
            # spoken "added it". Reminder-only adds have no view -> refresh only.
            view = _view_type_for(added)
            if view:
                await _pop_list(view)
            else:
                await _broadcast_lists("list_updated", added=added)

    elif intent in ("show_todos", "show_shopping", "show_reminders"):
        list_type = {"show_todos": "todo", "show_shopping": "shopping",
                     "show_reminders": "reminder"}[intent]
        # "show MY to-dos" narrows to whoever asked; "the/our to-dos" stays the
        # household view, and so does a voice we can't place — an unsure read
        # falls back to today's behavior rather than guessing whose list to
        # show. Shopping is never narrowed: one house, one shopping trip.
        owner = None
        if list_type in ("todo", "reminder") and intent_mod.wants_own_list(command):
            owner = await _speaker_name(speaker_task)
        try:
            items = await lists_mod.fetch(types=(list_type,), user=owner)
        except Exception as exc:  # noqa: BLE001
            log.warning("list fetch failed: %s", exc)
            result["response"] = "Sorry, I couldn't reach the lists service."
            result["ok"] = False
        else:
            result["items"] = items
            result["owner"] = owner
            result["response"] = fmt.summarize_list(list_type, items, owner=owner)
            result["ok"] = True
            # `owner` rides along so the kiosk keeps showing the SAME set it
            # just read out — a later list_updated snapshot is household-wide.
            await _turn_event("show_list", list_type=list_type, items=items,
                              owner=owner)

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
        target = zones.music_target(_CUR_SAT.get())
        try:
            sel = await music_mod.play(parsed.get("query"), parsed.get("media_type"),
                                       target)
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
            # Only for the room the screen is in: it renders whatever the KITCHEN
            # queue holds, so a bath play would put the wrong now-playing on it.
            await _turn_event("show_music", **sel)

    elif intent == "music_control":
        action = parsed.get("music_action")
        if not action:
            result["response"] = "Sorry, I didn't catch what to do with the music."
            result["ok"] = False
        else:
            try:
                effective_volume = await music_mod.control(
                    action, zones.music_target(_CUR_SAT.get()),
                    parsed.get("music_volume"))
            except music_mod.MusicUnavailable:
                result["response"] = "Sorry, I can't reach the music player."
                result["ok"] = False
            except Exception as exc:  # noqa: BLE001
                log.warning("music_control %s failed: %s", action, exc)
                result["response"] = "Sorry, that didn't work."
                result["ok"] = False
            else:
                result["response"] = fmt.confirm_music_control(
                    action, effective_volume)
                if effective_volume is not None:
                    result["music_volume"] = effective_volume
                result["ok"] = True

    elif intent == "music_query":
        try:
            np = music_mod.now_playing(zones.music_target(_CUR_SAT.get()))
        except music_mod.MusicUnavailable:
            result["response"] = "Sorry, I can't reach the music player."
            result["ok"] = False
        else:
            result["now_playing"] = np
            result["response"] = fmt.now_playing_phrase(np)
            result["ok"] = True
            if np:
                await _turn_event("show_music", **np)

    elif intent == "home_control":
        hc_result = None
        try:
            hc_result = await home_mod.handle(parsed, command, _CUR_SAT.get())
        except Exception as exc:  # noqa: BLE001 — HA down/slow; still refuse below
            log.warning("home control failed: %s", exc)
        if hc_result:
            result.update(hc_result)
        else:
            # Deliberately NOT the ask fallback (contrast with sports/weather):
            # a control phrase must never turn into a web search or a guess.
            result["response"] = "I don't control that."
            result["ok"] = False

    elif intent == "cover_set":
        # home_control's refusal rules apply unchanged: no ask fallback, and a
        # failure says so rather than pretending the blind moved.
        cover_result = None
        try:
            cover_result = await covers_mod.handle(parsed)
        except Exception as exc:  # noqa: BLE001 — HA down/slow; refuse below
            log.warning("cover set failed: %s", exc)
        if cover_result:
            result.update(cover_result)
        else:
            result["response"] = "Sorry, I couldn't move that blind."
            result["ok"] = False

    elif intent == "climate_set":
        # Same refusal rules as cover_set: no ask fallback, and an HA failure
        # says so rather than claiming the setpoint moved.
        climate_result = None
        try:
            climate_result = await climate_mod.handle(parsed)
        except Exception as exc:  # noqa: BLE001 — HA down/slow; refuse below
            log.warning("climate set failed: %s", exc)
        if climate_result:
            result.update(climate_result)
        else:
            result["response"] = "Sorry, I couldn't set the temperature."
            result["ok"] = False

    elif intent == "broadcast":
        # Like home_control: no ask fallback — a relay phrase must never turn
        # into a web search. Delivery is Node-RED's job past the MQTT publish.
        try:
            result.update(await broadcast_mod.handle(parsed))
        except Exception as exc:  # noqa: BLE001 — HA down / publish failed
            log.warning("broadcast failed: %s", exc)
            result["response"] = "Sorry, I couldn't reach the speakers."
            result["ok"] = False

    elif intent == "find_phone":
        # No ask fallback (home_control pattern): a find-phone phrase must
        # never become a web search. "my phone" resolves from the voice
        # (speaker ID); below-confidence keeps the ask-whose flow — the
        # pending op the one-word answer resolves without another parse.
        if phone_mod.is_self(parsed.get("phone_owner")):
            owner = await _speaker_name(speaker_task)
            if owner:
                parsed = {**parsed, "phone_owner": owner}
        try:
            fp_result = await phone_mod.handle(parsed)
        except Exception as exc:  # noqa: BLE001 — HA down / notify failed
            log.warning("find phone failed: %s", exc)
            fp_result = {"response": "Sorry, I couldn't reach the phones.",
                         "ok": False}
        result.update(fp_result)
        if fp_result.get("needs_owner"):
            session_set_pending("find_phone", [])

    elif intent == "show_camera":
        # There is one display and it is in the kitchen, so this is gated the
        # same way the dashboard cards are: rooms that paint the kitchen screen
        # may drive it, everywhere else says so rather than silently lighting
        # up a screen nobody asked about. No ask fallback — "show me Simon"
        # must never become a web search about a person.
        if not events.on_dashboard(_CUR_SAT.get()):
            result["response"] = "I can only show that on the kitchen display."
            result["ok"] = False
        else:
            result.update(await camera_mod.handle(parsed))

    elif intent == "close_camera":
        if not events.on_dashboard(_CUR_SAT.get()):
            result["response"] = "I can only do that on the kitchen display."
            result["ok"] = False
        else:
            result.update(await camera_mod.handle_close())

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
            # store_as: a score is a real question with a real answer someone
            # may want back later, so it joins the browsable answers (weather
            # and places stay out — transient, and each has its own view).
            ask_mod.remember(command, sports_result["response"],
                             sat=_CUR_SAT.get(), store_as="sports")
        else:
            # Unresolvable team/league or ESPN change -> slow-but-right path.
            await _turn_event("ask_thinking", query=command)
            ask_result = await ask_mod.handle_ask(command, _CUR_SAT.get())
            result["response"] = ask_result["response"]
            result["full"] = ask_result.get("full", "")
            result["ok"] = ask_result["ok"]

    elif intent == "weather":
        weather_result = await weather_mod.handle(
            parsed, command)   # None -> fall back
        if weather_result:
            result.update(weather_result)
            # Seed ask history so "what about the weekend?" routed to the smart
            # model knows what we just said (sports does the same).
            ask_mod.remember(command, weather_result["response"],
                             sat=_CUR_SAT.get())
        else:
            # HA down, or a day outside the 6-day met.no window.
            await _turn_event("ask_thinking", query=command)
            ask_result = await ask_mod.handle_ask(command, _CUR_SAT.get())
            result["response"] = ask_result["response"]
            result["full"] = ask_result.get("full", "")
            result["ok"] = ask_result["ok"]

    elif intent == "time_query":
        # No fallback branch, unlike weather/sports/places: there is no service
        # to be down, so the clock either answers or the process is gone.
        result.update(clock_mod.handle(parsed))
        # Seed ask history so "what about tomorrow" after a date answer has
        # something to refer back to (weather and sports do the same).
        ask_mod.remember(command, result["response"], sat=_CUR_SAT.get())

    elif intent in ("business_hours", "place_search"):
        places_result = None
        try:
            places_result = await places_mod.handle(parsed)
        except Exception as exc:  # noqa: BLE001 — slow ask path is the fallback
            log.warning("business-hours lookup failed, falling back to ask: %s", exc)
        if places_result:
            result.update(places_result)
            await _turn_event("show_places", **places_result["places_view"])
            # Preserve the named place for a pronoun follow-up handled by ask.
            ask_mod.remember(command, places_result["response"],
                             sat=_CUR_SAT.get())
        else:
            await _turn_event("ask_thinking", query=command)
            ask_result = await ask_mod.handle_ask(command, _CUR_SAT.get())
            result["response"] = ask_result["response"]
            result["full"] = ask_result.get("full", "")
            result["ok"] = ask_result["ok"]

    elif intent == "ask":
        query = parsed.get("query") or command
        await _turn_event("ask_thinking", query=query)
        ask_result = await ask_mod.handle_ask(query, _CUR_SAT.get())
        result["response"] = ask_result["response"]
        result["full"] = ask_result.get("full", "")
        result["ok"] = ask_result["ok"]

    elif intent == "show_answer":
        # Recall the last answer: the handler re-emits ask_thinking/ask_full to
        # rebuild the fullscreen answer; the spoken part is re-spoken here.
        show_result = await ask_mod.handle_show_answer(_CUR_SAT.get())
        result["response"] = show_result["response"]
        result["ok"] = show_result["ok"]

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


async def _dispatch(command: str, **kw) -> dict:
    """handle_command with a turn-closing safety net.

    Every entry point emits "transcript"/"thinking" before dispatching, and the
    dashboard hands its hide timer to the "response" event that normally
    follows. So an exception escaping as a 500 doesn't just lose the reply — it
    strands the popup on screen with no way to clear (2026-08-06: a long ozone
    question overran the intent parser's token ceiling, and the transcript sat
    pinned all evening). Answer out loud instead, so the turn always closes."""
    try:
        return await handle_command(command, **kw)
    except Exception:  # noqa: BLE001 — a dead turn must still close itself
        log.exception("command dispatch failed: %r", command)
        return await _finalize(
            {"intent": "none", "ok": False,
             "response": "Sorry, I had trouble with that."}, "none")


# --------------------------------------------------------------------------
# HTTP surface
# --------------------------------------------------------------------------
@app.get("/health")
def health() -> dict:
    return {"ok": True, "active_timers": len(ENGINE.active())}


@app.post("/session/listening")
async def session_listening(sat: str | None = None) -> dict:
    """Satellite pings this when it opens a follow-up listen window (no wake
    word). Emits a dashboard cue so the kiosk shows 'Listening…' — the user must
    be able to tell the mic is still open without guessing."""
    if sat:
        _FOLLOWUP_LISTEN[sat] = time.time()
    await _turn_event("followup_listening", sat=sat)
    return {"ok": True}


@app.post("/session/idle")
async def session_idle(sat: str | None = None) -> dict:
    """Satellite pings this when its follow-up loop ends (any exit path), so
    a peer's cold wake stops deferring to a conversation that is over."""
    if sat:
        _FOLLOWUP_LISTEN.pop(sat, None)
    return {"ok": True}


@app.post("/wake")
async def wake(request: Request) -> dict:
    """Satellite posts the raw WAV utterance (pre-roll + speech)."""
    wav = await request.body()
    if not wav:
        raise HTTPException(400, "empty audio body")
    t0 = time.time()
    await _turn_event("verifying")
    transcript = await clients.transcribe(wav)
    verified, command, score = verify.verify_and_extract(transcript)
    log.info("wake transcript=%r verified=%s score=%s cmd=%r",
             transcript, verified, score, command)
    if not verified:
        await _turn_event("wake_rejected", transcript=transcript, score=score)
        return {"verified": False, "transcript": transcript, "score": score}

    await _turn_event("transcript", text=command, wake_score=score)
    result = await _dispatch(command)
    result.update(
        verified=True, transcript=transcript, score=score,
        latency_ms=round((time.time() - t0) * 1000),
    )
    return result


def _wav_tail(wav: bytes, seconds: float) -> bytes | None:
    """Last `seconds` of a WAV, re-wrapped. None if the clip is already that
    short (a second decode would just repeat the first) or unparseable."""
    try:
        with wave.open(io.BytesIO(wav), "rb") as w:
            rate, width, ch = w.getframerate(), w.getsampwidth(), w.getnchannels()
            n = w.getnframes()
            keep = int(seconds * rate)
            if n <= keep:
                return None
            w.setpos(n - keep)
            pcm = w.readframes(keep)
        buf = io.BytesIO()
        with wave.open(buf, "wb") as out:
            out.setnchannels(ch)
            out.setsampwidth(width)
            out.setframerate(rate)
            out.writeframes(pcm)
        return buf.getvalue()
    except Exception:  # noqa: BLE001 — malformed wav: skip the rescue decode
        return None


async def _decode_wake(wav: bytes) -> tuple[bool, str, str, float, str]:
    """Run the normal full/tail stage-2 decode without turn side effects."""
    transcript = await clients.transcribe(wav)
    verified, command, score = verify.verify_and_extract(transcript)
    decode = "full"
    if not verified and config.VERIFY_TAIL_S > 0:
        tail = _wav_tail(wav, config.VERIFY_TAIL_S)
        if tail is not None:
            tail_transcript = await clients.transcribe(tail)
            t_verified, t_command, t_score = verify.verify_and_extract(tail_transcript)
            log.info("verify tail transcript=%r verified=%s score=%s",
                     tail_transcript, t_verified, t_score)
            if t_verified:
                verified, command, score = t_verified, t_command, t_score
                transcript, decode = tail_transcript, "tail"
    return verified, command, transcript, score, decode


def _wake_timings() -> dict[str, int]:
    """ASR time spent so far on this turn, for the turn row. Covers both decodes
    when the dual-decode tail rescue runs — the stage timer accumulates."""
    snap = timing.snapshot()
    return {"asr_ms": snap["asr"]} if "asr" in snap else {}


@app.post("/verify/probe")
async def verify_wake_probe(request: Request, sat: str = "kitchen") -> dict:
    """Silent stage-2 diagnostic: ASR only, with no claim/events/amp wake."""
    decision = await policy_mod.evaluate(sat)
    if not decision["allowed"]:
        return {"verified": False, "silent": True, "policy": decision,
                "probe": True}
    wav = await request.body()
    if not wav:
        raise HTTPException(400, "empty audio body")
    t0 = time.time()
    verified, command, transcript, score, decode = await _decode_wake(wav)
    log.info("verify probe sat=%s transcript=%r verified=%s score=%s decode=%s",
             sat, transcript, verified, score, decode)
    return {
        "verified": verified, "score": score, "transcript": transcript,
        "command": command, "decode": decode, "probe": True, "silent": True,
        "latency_ms": round((time.time() - t0) * 1000),
    }


@app.post("/verify")
async def verify_wake(request: Request, sat: str = "kitchen",
                      peak: float | None = None) -> dict:
    """Phase 1: stage-2 verification on the pre-roll (wake phrase audio only).
    Fast path so the satellite can chime the instant the wake word is confirmed,
    then start capturing the command. `command` is any speech already trailing
    the wake phrase in the pre-roll (if the user ran it together).

    Dual decode: a full-pre-roll reject gets a second decode of just the tail
    (VERIFY_TAIL_S); either passing verifies the turn. This rescues wake words
    spoken OVER another voice — Parakeet is single-speaker and latches onto the
    stream with more context, so the competing voice's lead-in must be cut, not
    out-fuzzed. Runs only on rejects; the passing path costs nothing extra.

    `peak` is the satellite's stage-1 detector score for this trigger. It also
    arrives later on /telemetry, but an arbitration loser never gets that far,
    and the loser's score is half of the paired-mic evidence (loudness.py)."""
    decision = await policy_mod.evaluate(sat)
    if not decision["allowed"]:
        log.info("verify sat=%s silent no-op policy=%s", sat, decision["reason"])
        turns_mod.start(sat, "wake", verified=False, reject_reason="policy")
        return {"verified": False, "silent": True, "policy": decision}
    wav = await request.body()
    if not wav:
        raise HTTPException(400, "empty audio body")
    t0 = time.time()
    timing.start()
    peer = _peer_in_followup(sat)
    if peer:
        # A peer mic is mid-follow-up and may be about to claim this very
        # phrase off its live partials (~0.6-1.0s after it is spoken, vs our
        # ~0.3s). The open conversation keeps the turn if it heard it.
        log.info("verify sat=%s peer %s in follow-up -> deferring up to %.1fs",
                 sat, peer, config.REWAKE_ARB_WAIT_S)
        await _await_rewake_claim(sat)
    winner = _arb_holder(sat)
    if winner:
        handed = await _loudness_handoff(sat, winner, wav, peak, None)
        if handed:
            handed["latency_ms"] = round((time.time() - t0) * 1000)
            return handed
        # Race already lost — don't burn an ASR decode (it's the same
        # utterance the winner just verified) and don't double the dashboard
        # badge events. The satellite shadow-captures on this response.
        log.info("verify sat=%s suppressed (winner=%s)", sat, winner)
        turn_id = turns_mod.start(sat, "wake", verified=False, stage1_score=peak,
                                  reject_reason="suppressed", arb_winner=winner,
                                  arb_turn_id=_ARB["turn_id"])
        asyncio.create_task(_note_wake_loudness(turn_id, wav, sat, winner, peak))
        return {"verified": False, "suppressed": True, "winner": winner}
    # Fire-and-forget: this POST to the dashboard sat serially BEFORE the ASR
    # call, putting a cosmetic badge (with a 4s timeout tail) on the chime path.
    asyncio.create_task(_turn_event("verifying", sat=sat))
    verified, command, transcript, score, decode = await _decode_wake(wav)
    rms: float | None = None
    log.info("verify sat=%s transcript=%r verified=%s score=%s decode=%s",
             sat, transcript, verified, score, decode)
    if verified:
        winner = _arb_holder(sat)
        if winner:
            handed = await _loudness_handoff(
                sat, winner, wav, peak,
                (verified, command, transcript, score, decode))
            if handed:
                handed["latency_ms"] = round((time.time() - t0) * 1000)
                return handed
            # The other mic's verify completed while our ASR ran. Its events
            # already drove the dashboard; go quiet.
            log.info("verify sat=%s suppressed post-ASR (winner=%s)", sat, winner)
            turn_id = turns_mod.start(sat, "wake", verified=False,
                                      transcript=transcript, stage1_score=peak,
                                      wake_score=score, decode=decode,
                                      reject_reason="suppressed", arb_winner=winner,
                                      arb_turn_id=_ARB["turn_id"],
                                      **_wake_timings())
            asyncio.create_task(_note_wake_loudness(turn_id, wav, sat, winner, peak))
            return {"verified": False, "suppressed": True, "winner": winner}
        # Same-hardware peers compare wake loudness at hand-off time, and the
        # peer's /verify can land ~10 ms behind this claim -- too soon for
        # the background reading below. A few ms of pure-Python RMS, only for
        # sats in a loudness group; everyone else keeps it off the chime path.
        rms = loudness.peak_window_dbfs(wav) if _in_loudness_group(sat) else None
        _arb_claim(sat, peak, rms)
        # Zone-routed satellites answer through the whole-home amp, which needs
        # ~3s to come out of standby. Start that now so it finishes under the
        # ASR + intent + TTS that follows, instead of eating the reply's first
        # words. Fire-and-forget: the turn must not wait on it.
        route = zones.route_for(sat)
        if route:
            asyncio.create_task(
                broadcast_mod.amp_wake(route["rooms"], route.get("volume")))
    await _turn_event("wake_confirmed" if verified else "wake_rejected", sat=sat,
                      score=score, transcript=transcript)
    # Opens the turn row. The satellite hands this id back on /telemetry (with
    # the numbers only it can measure) and on /command/audio, so one turn stays
    # one row instead of scattering across three.
    turn_id = turns_mod.start(
        sat, "wake", verified=verified, transcript=transcript,
        wake_score=score, decode=decode, stage1_score=peak,
        reject_reason=None if verified else ("empty" if not transcript
                                             else "low_score"),
        **_wake_timings())
    if verified:
        _ARB["turn_id"] = turn_id
    asyncio.create_task(_note_wake_loudness(turn_id, wav, sat, rms=rms))
    return {
        "verified": verified, "score": score, "transcript": transcript,
        "command": command, "decode": decode, "turn_id": turn_id,
        "latency_ms": round((time.time() - t0) * 1000),
    }


@app.post("/command/audio")
async def command_audio(request: Request, followup: bool = False,
                        stitched: bool = False, sat: str = "kitchen",
                        turn_id: str | None = None) -> dict:
    """Phase 2: the captured command utterance. Transcribe + act. No wake check —
    /verify already gated this turn. `followup=1` marks a continued-conversation
    turn (no wake word): background speech is dropped silently. `stitched=1`
    marks wake-turn audio with the 2.5s pre-roll prepended (the satellite's fix
    for run-together commands losing their first words in the stage-1 detect
    gap): strip everything through the wake phrase before acting."""
    decision = await policy_mod.evaluate(sat)
    if not decision["allowed"]:
        log.info("command sat=%s silent no-op policy=%s", sat, decision["reason"])
        return {"ok": False, "transcript": "", "response": "",
                "intent": "none", "silent": True, "policy": decision}
    wav = await request.body()
    if not wav:
        raise HTTPException(400, "empty audio body")
    _CUR_SAT.set(sat)           # read by _finalize for per-satellite reply routing
    t0 = time.time()
    # A wake turn continues the row /verify opened; a follow-up, a text turn or
    # the dashboard mic button has no wake step and starts its own. An id from
    # a satellite that predates this feature simply won't exist in the table,
    # and update() no-ops rather than erroring.
    timing.start()
    if not turn_id:
        turn_id = turns_mod.start(sat, "followup" if followup else "manual")
    if turn_id and (answer := _ARB_HANDOFF.pop(turn_id, None)):
        # This wake was handed to a louder same-hardware peer at /verify
        # (_loudness_handoff); that room is answering. Transcribe for the
        # paper trail -- the two captures side by side are the evidence for
        # which mic hears the command better -- and go quiet.
        transcript = await clients.transcribe(wav)
        log.info("shadow command (handoff to %s) sat=%s transcript=%r",
                 answer, sat, transcript)
        turns_mod.update(turn_id, transcript=transcript, intent="none",
                         ok=False, reject_reason="handoff", arb_winner=answer,
                         **_wake_timings())
        return {"ok": False, "silent": True, "yield": True, "winner": answer,
                "transcript": transcript, "response": "", "intent": "none"}
    if followup:
        listen_since = _followup_listening(sat)
        _FOLLOWUP_LISTEN.pop(sat, None)
        # A peer mic verified a cold wake AFTER this listen opened: that wake
        # was the person re-waking (or a different person the peer owns), and
        # the peer is answering it. Whatever we captured is theirs -- drop it,
        # or both rooms answer (live 2026-08-28, kitchen + family room).
        if (listen_since is not None and _ARB["sat"] and _ARB["sat"] != sat
                and _ARB["at"] >= listen_since):
            log.info("followup sat=%s yields: %s took a wake during this listen",
                     sat, _ARB["sat"])
            turns_mod.update(turn_id, intent="none", ok=False,
                             reject_reason="yield", arb_winner=_ARB["sat"])
            return {"ok": False, "yield": True, "winner": _ARB["sat"],
                    "transcript": "", "response": "", "intent": "none"}
    # The final decode: hedged Cohere/Parakeet when FINAL_ASR_URL is set,
    # plain Parakeet otherwise (clients.transcribe_final). Partials, verify
    # and the shadow paths above stay on Parakeet.
    asr = await clients.transcribe_final(wav)
    transcript = asr.text
    log.info("command sat=%s followup=%s transcript=%r%s", sat, followup,
             transcript,
             f" asr={asr.model} primary_ms={asr.primary_ms} "
             f"fallback_ms={asr.fallback_ms} reason={asr.fallback_reason or 'none'}"
             if asr.model else "")
    turns_mod.update(turn_id, **asr.turn_fields())
    if stitched and transcript:
        wake_found, command, _ = verify.verify_and_extract(transcript)
        if wake_found:
            log.info("stitched %r -> command %r", transcript, command)
            transcript = command
        # No wake phrase in the decode (odd, /verify just matched this audio):
        # keep the full transcript — a stray lead-in beats a lost turn.
    # A zone-routed satellite hears its own answer off the room speakers, and
    # the mic is open during the reply on purpose (barge-in), so the capture
    # can be the reply alone or the reply with the person's words after it.
    # Strip the reply off the front and act on what is left. Whole-capture
    # echo on a follow-up tells the satellite to keep listening rather than
    # end the conversation -- that is what makes the follow-up window
    # self-timing instead of a guess at the reply's length.
    heard = transcript
    echoed = False
    if transcript:
        transcript, echoed = zones.strip_echo(sat, transcript, followup=followup)
    if not transcript:
        if echoed and followup:
            turns_mod.update(turn_id, transcript=heard, intent="none",
                             ok=False, reject_reason="echo", **_wake_timings())
            return {"ok": False, "echo": True, "transcript": heard,
                    "response": "", "intent": "none"}
        # Silence: on a follow-up, stay quiet; on a wake turn, say we missed it.
        if not followup:
            await _turn_event("response", text="I didn't catch that.", intent="none")
        turns_mod.update(turn_id, intent="none", ok=False,
                         reject_reason="echo" if echoed else "no_command",
                         **({"transcript": heard} if heard else {}),
                         **_wake_timings())
        return {"ok": False, "transcript": "", "response": "",
                "intent": "none", "silent": followup}
    if followup:
        # Saying the wake word again mid-conversation is natural, and without
        # this it lands in the classifier as part of the command ("okay
        # computer what's the forecast") or, said bare, ends the session.
        wake_found, stripped, _ = verify.verify_and_extract(transcript)
        if wake_found:
            if not stripped:
                log.info("followup sat=%s bare wake word -> relisten", sat)
                turns_mod.update(turn_id, transcript=transcript, intent="none",
                                 ok=False, reject_reason="rewake",
                                 **_wake_timings())
                return {"ok": False, "rewake": True, "transcript": transcript,
                        "response": "", "intent": "none"}
            log.info("followup wake-strip %r -> %r", transcript, stripped)
            transcript = stripped
    if not followup:
        await _turn_event("transcript", text=transcript)
    # Speaker ID: in active mode the embed starts now (concurrent with intent
    # parsing) and person-dependent handlers await it lazily; in shadow mode
    # the scoring runs entirely off-turn. Either way every turn is logged to
    # SPEAKER_SHADOW_LOG — the soak data that watches the thresholds.
    spk_task = None
    if config.SPEAKER_MODE == "active":
        spk_task = asyncio.create_task(speaker_mod.identify(wav))
    result = await _dispatch(transcript, followup=followup,
                             speaker_task=spk_task)
    result["transcript"] = transcript
    result["latency_ms"] = round((time.time() - t0) * 1000)
    turns_mod.update(turn_id, command=transcript)
    turns_mod.finish(turn_id, result, timings=timing.snapshot(),
                     total_ms=result["latency_ms"])
    if spk_task is not None:
        asyncio.create_task(speaker_mod.log_task(
            spk_task, transcript, intent_name=result.get("intent"), sat=sat,
            followup=followup))
        # Same identification the handlers consumed, onto the turn row. Awaited
        # off-turn: the reply has already been rendered by here, and an embed
        # that is still in flight must not hold the response open.
        asyncio.create_task(_note_turn_speaker(turn_id, spk_task))
    else:
        asyncio.create_task(speaker_mod.shadow(
            wav, transcript, intent_name=result.get("intent"), sat=sat,
            followup=followup))
    return result


async def _note_turn_speaker(turn_id: str, spk_task: asyncio.Task) -> None:
    """Attach a turn's voice identification once its embed resolves."""
    try:
        turns_mod.note_speaker(turn_id, await spk_task)
    except Exception as exc:  # noqa: BLE001 — telemetry never breaks a turn
        log.warning("turn speaker note failed: %s", exc)


@app.post("/telemetry")
async def telemetry(payload: dict) -> dict:
    """Satellite-measured numbers for a turn already opened by /verify.

    Three of these can only be measured on the satellite: `chime_ms` (stage-1
    trigger → chime start, the 500ms number), `rtt_ms` (the /verify round trip
    as the satellite sees it — subtract our own latency_ms and what's left is
    WiFi + HTTP), and the stage-1 detector's own `peak_score`/model, which we
    never see because the satellite only calls us once it has already fired.

    Fire-and-forget from the satellite's side, so this stays cheap and never
    errors back at it: an unknown turn_id is a no-op."""
    turns_mod.update(
        payload.get("turn_id"),
        stage1_score=payload.get("peak_score"),
        wake_model=payload.get("model"),
        chime_ms=payload.get("chime_ms"),
        rtt_ms=payload.get("rtt_ms"),
        # Our own latency as the SATELLITE clocked it. Stored rather than
        # recomputed because rtt_ms minus server_ms is the WiFi + HTTP cost,
        # and that difference is only meaningful between two numbers taken
        # from the same pair of clocks.
        server_ms=payload.get("server_ms"),
        clip=payload.get("clip"),
    )
    return {"ok": True}


@app.post("/near_miss")
async def near_miss(payload: dict, sat: str | None = None) -> dict:
    """A stage-1 score that got close but never crossed the trigger line. The
    satellite never calls /verify for these, so without this row a wake the
    house heard as "nothing happened" is indistinguishable from silence (live
    2026-08-26: Adrienne's 5pm "okay computer" left no trace anywhere). One
    row per near-miss episode, rate-limited on the satellite; the pre-roll
    clip is kept there (near-*.wav) for listening and for retraining."""
    turns_mod.start(
        sat, "near_miss",
        stage1_score=payload.get("peak_score"),
        wake_model=payload.get("model"),
        clip=payload.get("clip"),
    )
    return {"ok": True}


@app.get("/turns")
async def list_turns(limit: int = 50, sat: str | None = None) -> dict:
    """Read path for the voice-ops dashboard (and for eyeballing the table from
    a shell). Read-only; the dashboard also reads the SQLite file directly."""
    return {"turns": turns_mod.recent(limit=max(1, min(limit, 500)), sat=sat)}


@app.post("/satellite/play")
async def satellite_play(request: Request, alarm: int = 0) -> dict:
    """Playback-relay proxy. The family-room satellite POSTs its chime/TTS
    WAV here (it can't reach the kitchen box across the VLAN firewall) and we
    forward to the kitchen satellite's /play. Synchronous end-to-end: this
    responds when the audio has finished playing in the kitchen, because the
    relayer times its capture/drains off playback completion."""
    if not config.SATELLITE_PLAY_URL:
        raise HTTPException(503, "no satellite play target configured")
    wav = await request.body()
    if not wav:
        raise HTTPException(400, "empty audio body")
    try:
        async with httpx.AsyncClient(timeout=120) as client:
            r = await client.post(
                config.SATELLITE_PLAY_URL, params={"alarm": alarm},
                content=wav, headers={"Content-Type": "audio/wav"})
        r.raise_for_status()
    except Exception as exc:  # noqa: BLE001 — a mute chime must not 500 the turn
        log.warning("satellite play relay failed: %s", exc)
        return {"ok": False}
    return {"ok": True}


@app.post("/command/shadow")
async def command_shadow(request: Request, sat: str = "unknown") -> dict:
    """The arbitration LOSER's capture of the utterance the winner is already
    handling. Transcribe and log only — no intents, no events, no reply. The
    point is the paper trail: grep 'shadow command' next to the winner's
    'command sat=' line to see which mic had the cleaner take (the go/no-go
    data for the v2 dual-transcribe chooser)."""
    wav = await request.body()
    if not wav:
        raise HTTPException(400, "empty audio body")
    transcript = await clients.transcribe(wav)
    # Same wake-phrase strip as stitched wake turns, so the two log lines are
    # directly comparable.
    wake_found, command, _ = verify.verify_and_extract(transcript)
    log.info("shadow command sat=%s transcript=%r",
             sat, command if wake_found else transcript)
    return {"ok": True, "transcript": transcript}


@app.post("/partial")
async def partial(request: Request, seq: int = 0, sat: str | None = None,
                  followup: int = 0) -> dict:
    """Live-caption snapshot. During command capture the satellite POSTs the
    ENTIRE buffer-so-far every ~400ms; each one is re-decoded as a normal
    full-context batch (so partials have zero accuracy penalty vs the final)
    and fanned to the dashboard as partial_transcript {text, seq}. Display-only
    by design: intent parsing always runs on the final /command/audio
    transcript, never a partial. Best-effort throughout — failures return ok
    False and cost nothing but a skipped caption frame.

    `followup=1` (continued-conversation capture): also report whether the
    wake phrase leads the partial — `wake` — and whether it is ALL of it —
    `bare`. The satellite chimes on `wake` so a re-wake said mid-conversation
    ("okay computer, what about tomorrow") is acknowledged while the person is
    still talking instead of only after the endpoint + round trip (user
    request 2026-08-28: with no ding you can't tell listening from a missed
    wake). Same matcher the final transcript goes through below, so the chime
    fires iff the final turn will strip/rewake on it. The caption drops the
    wake phrase for the same reason the final transcript does."""
    wav = await request.body()
    if not wav:
        raise HTTPException(400, "empty audio body")
    try:
        text = await clients.transcribe(wav)
    except Exception as exc:  # noqa: BLE001 — captions are cosmetic
        log.debug("partial transcribe failed: %s", exc)
        return {"ok": False, "seq": seq}
    wake = bare = False
    caption = text
    if text and followup:
        found, stripped, _ = verify.verify_and_extract(text)
        if found:
            # This is a wake, so it takes part in arbitration: a peer mic that
            # cold-verified the same phrase first owns it -> this capture
            # yields (no chime, session ends quietly). Otherwise claim, so the
            # peer's /verify (deferred, see _peer_in_followup) is suppressed.
            holder = _arb_holder(sat) if sat else None
            if holder:
                log.info("partial sat=%s rewake yields to %s", sat, holder)
                return {"ok": True, "seq": seq, "text": text, "wake": False,
                        "bare": False, "yield": True, "winner": holder}
            if sat:
                _arb_claim(sat)
            wake, bare, caption = True, not stripped, stripped
    if caption:
        await _turn_event("partial_transcript", text=caption, seq=seq, sat=sat)
    return {"ok": True, "seq": seq, "text": text, "wake": wake, "bare": bare}


# -- music ducking (satellite) ----------------------------------------------
@app.get("/satellite/policy")
async def satellite_policy(sat: str = "kitchen") -> dict:
    """Pre-feedback guard for remote bridges; the ASR routes enforce it too."""
    return await policy_mod.evaluate(sat)


@app.post("/music/button-stop")
async def music_button_stop(sat: str = "kitchen") -> dict:
    """Physical satellite button: idempotently stop this room's alarm/music."""
    timer = ENGINE.dismiss_any_ringing(sat)
    if timer:
        await events.alarm_stop(sat)
        await _timer_event("timer_dismissed", timer=timer)
    target = zones.music_target(sat)
    if not target["local"]:
        return {"ok": True, "skipped": "no music in this room"}
    try:
        await music_mod.control("stop", target)
    except Exception as exc:  # noqa: BLE001 — a stop while idle stays harmless
        log.warning("button stop failed sat=%s: %s", sat, exc)
        return {"ok": False, "error": type(exc).__name__}
    return {"ok": True, "timer": timer}


@app.post("/music/duck")
async def music_duck(sat: str | None = None) -> dict:
    """Satellite fires this on a stage-1 wake trigger / alarm start so speech
    isn't buried under the music. Best-effort — never errors.

    A room only ducks music that is playing in that room. Before rooms had
    their own queues this was the kitchen's, and the master closet ducked it
    every time it heard its wake word or rang a bath timer."""
    target = zones.music_target(sat)
    if not target["local"]:
        return {"ok": True, "skipped": "no music in this room"}
    try:
        await music_mod.duck(target)
    except Exception as exc:  # noqa: BLE001 — includes MusicUnavailable
        log.debug("duck skipped: %s", exc)
        return {"ok": False}
    return {"ok": True}


@app.post("/music/unduck")
async def music_unduck(sat: str | None = None) -> dict:
    # Symmetrical with the duck, and it has to be: the duck is refcounted, so
    # an unduck from a room that never ducked would decrement someone else's
    # hold and un-duck that room mid-sentence.
    target = zones.music_target(sat)
    if not target["local"]:
        return {"ok": True, "skipped": "no music in this room"}
    try:
        await music_mod.unduck(target)
    except Exception as exc:  # noqa: BLE001
        log.debug("unduck skipped: %s", exc)
        return {"ok": False}
    return {"ok": True}


@app.get("/music/state")
def music_state(sat: str | None = None) -> dict:
    """Debug/CLI peek at what a room's queue is doing (default: the kitchen)."""
    try:
        return {"ok": True, "now_playing": music_mod.now_playing(
            zones.music_target(sat))}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc)}


@app.post("/transcribe")
async def transcribe_audio(request: Request) -> dict:
    """Raw transcription (no intent). Used by the satellite to listen for a
    'stop' barge-in while an alarm is ringing. Optional ?client= picks the
    GX10 bias profile (satellites send kitchen-alarm for ring windows so
    'stop' isn't competing with 102 music/command phrases for bias weight)."""
    wav = await request.body()
    if not wav:
        raise HTTPException(400, "empty audio body")
    client_name = request.query_params.get("client") or None
    return {"transcript": await clients.transcribe(wav, client_name)}


@app.post("/command")
async def command(payload: dict = Body(...)) -> dict:
    """Text bypass for testing and future text-driven paths. `followup: true`
    exercises the continued-conversation path (session context + silent none);
    `sat` names the satellite to answer as, which room-scoped home commands
    and zone reply routing both key off."""
    text = str(payload.get("text", "")).strip()
    if not text:
        raise HTTPException(400, "missing 'text'")
    sat = payload.get("sat") or None
    _CUR_SAT.set(sat)
    t0 = time.time()
    timing.start()
    # A text turn is a real turn — it just skips the wake and ASR stages. Kept
    # on the same table so "what did the house do today" is one query, with
    # kind='text' to keep it out of the wake-funnel arithmetic.
    turn_id = turns_mod.start(sat, "text", command=text)
    result = await _dispatch(text, followup=bool(payload.get("followup")))
    result["latency_ms"] = round((time.time() - t0) * 1000)
    turns_mod.finish(turn_id, result, timings=timing.snapshot(),
                     total_ms=result["latency_ms"])
    return result


# -- timer REST (dashboard + testing) --------------------------------------
@app.get("/timers")
def list_timers(sat: str | None = None) -> dict:
    """Active timers, house-wide by default. `sat` scopes to one room — the
    kitchen display asks that way so a reload does not repopulate its board
    with the bath timers the live events are careful not to send it."""
    return {"timers": ENGINE.active(sat)}


@app.post("/timers/{timer_id}/dismiss")
async def dismiss_timer(timer_id: str) -> dict:
    timer = ENGINE.dismiss(timer_id)
    if not timer:
        raise HTTPException(404, "no ringing timer with that id")
    # Awaited before responding (see events.alarm_stop docstring): the
    # satellite's own end-of-ring POST lands here too, and its next queued
    # alarm must not start until this dismiss has been delivered.
    await events.alarm_stop(timer.get("sat"))
    await _timer_event("timer_dismissed", timer=timer)
    return {"ok": True, "timer": timer}


@app.post("/timers/{timer_id}/cancel")
async def cancel_timer(timer_id: str) -> dict:
    row = ENGINE.get(timer_id)
    was_ringing = bool(row and row["state"] == RINGING)
    timer = ENGINE.cancel_by_id(timer_id)
    if not timer:
        raise HTTPException(404, "no active timer with that id")
    if was_ringing:
        await events.alarm_stop(timer.get("sat"))
    await _timer_event("timer_cancelled", timer=timer)
    return {"ok": True, "timer": timer}


@app.post("/alarm/stop")
async def alarm_stop_route(payload: dict | None = Body(None)) -> dict:
    """Front door for 'make the ringing stop' (kiosk tap/swipe, phone). Marks
    the ringing timer dismissed if the engine knows of one, but silences the
    satellite UNCONDITIONALLY — state skew must never keep the alarm ringing.

    `sat` scopes it to one room; it defaults to the kitchen because the one
    caller today is the kitchen touchscreen, and a tap on a screen in the
    kitchen should not reach into the bathroom."""
    sat = (payload or {}).get("sat") or config.DEFAULT_SAT
    timer = ENGINE.dismiss_any_ringing(sat)
    await events.alarm_stop((timer or {}).get("sat") or sat)
    if timer:
        await _timer_event("timer_dismissed", timer=timer)
    return {"ok": True, "timer": timer}


@app.post("/timers/{timer_id}/unattended")
async def unattended_timer(timer_id: str) -> dict:
    """Satellite watchdog escalation: the alarm has been ringing ~15s with
    nobody dismissing it — push to the household phones so dinner doesn't
    burn while everyone is upstairs.

    The room comes from the timer, not from a constant: a master bath timer
    that pushes "Kitchen timer unattended" sends you to the wrong floor."""
    timer = ENGINE.get(timer_id)
    if not timer or timer["state"] != RINGING:
        return {"ok": False, "reason": "not ringing"}
    name = fmt.timer_name(timer)
    dur = fmt.humanize_seconds(timer.get("duration_seconds") or 0)
    room = zones.spoken_for(timer.get("sat"))
    body = f"The {name} ({dur}) is ringing in the {room} and nobody has stopped it."
    await events.phone_alert(
        f"{room.capitalize()} timer unattended", body,
        event_type="timer_unattended", timer_id=timer_id,
    )
    await _timer_event("timer_unattended", timer=timer)
    return {"ok": True}


@app.post("/timers/{timer_id}/add")
async def add_time(timer_id: str, seconds: int = 60) -> dict:
    row = ENGINE.get(timer_id)
    if not row:
        raise HTTPException(404, "unknown timer")
    timer = await ENGINE.adjust(row.get("label"), seconds)
    await _timer_event("timer_updated", timer=timer)
    return {"ok": True, "timer": timer}


@app.get("/timers/{timer_id}/announcement.wav")
def announcement(timer_id: str) -> FileResponse:
    path = ENGINE.announce_wav_path(timer_id)
    if not path or not os.path.exists(path):
        raise HTTPException(404, "no announcement for that timer")
    return FileResponse(path, media_type="audio/wav")


# -- answers REST (dashboard) -----------------------------------------------
@app.get("/answers")
async def get_answers(limit: int = 50) -> dict:
    """Past questions, newest first, without their bodies — enough for the
    kiosk's sidebar. Proxied by the dashboard like the rest of this API."""
    return {"answers": answers_mod.recent(limit)}


@app.get("/answers/{answer_id}")
async def get_answer(answer_id: int) -> dict:
    """One answer with its full on-screen text."""
    row = answers_mod.get(answer_id)
    if not row:
        raise HTTPException(404, "no such answer")
    return row


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


@app.post("/lists/shopping")
async def add_shopping_items(payload: dict = Body(...)) -> dict:
    """Add known items to the shared shopping list (cookmode's recipe staging).

    The orchestrator stays the single write path into the household list even
    though the caller is not a voice turn: it owns the companion's address, and
    it is what tells the kitchen display something changed. A recipe committed
    from the phone therefore updates an open list view on the kiosk for free.
    """
    texts = payload.get("items") or payload.get("texts") or []
    if not isinstance(texts, list):
        raise HTTPException(400, "items must be a list of strings")
    texts = [str(t) for t in texts if str(t).strip()]
    if not texts:
        raise HTTPException(400, "no items")
    try:
        result = await lists_mod.add_shopping(texts)
    except Exception as exc:  # noqa: BLE001
        log.warning("POST /lists/shopping failed: %s", exc)
        raise HTTPException(502, f"lists service unreachable: {exc}")
    added = result.get("added") or []
    if added:
        await _broadcast_lists("list_updated", added=added)
    return {
        "added": [it.get("text") for it in added],
        "duplicates": result.get("duplicates") or [],
    }


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


@app.post("/reminder/due")
async def reminder_due(payload: dict = Body(...)) -> dict:
    """A reminder just came due and the companion has pushed it to its owner's
    phone. Decide whether it ALSO belongs on the kitchen display.

    The rule is provenance, never content: a reminder created by voice was
    already said out loud in that room, so echoing it there tells the room
    nothing it hasn't heard. A phone-typed one was never uttered in shared
    space, and "remind me privately to…" filed itself under a mode that is
    deliberately absent from REMINDER_DISPLAY_SOURCES. We never ask a model
    whether something is "personal" — a wrong no in front of guests is the one
    failure that matters, and provenance can't be wrong that way.

    Answering 200 either way: this is the companion's fire-and-forget tail, and
    a declined display is a normal outcome, not an error."""
    text = str(payload.get("text") or "").strip()
    if not text:
        raise HTTPException(400, "missing 'text'")
    source = str(payload.get("source") or "")
    if source not in config.REMINDER_DISPLAY_SOURCES:
        log.info("reminder %s not displayed (source=%r)", payload.get("item_id"), source)
        return {"ok": True, "shown": False, "reason": "source"}
    owner = str(payload.get("user") or "") or None
    log.info("reminder %s due -> kitchen display (%s): %r",
             payload.get("item_id"), owner or "unattributed", text)
    await events.emit("reminder_due", item_id=payload.get("item_id"),
                      owner=owner, text=text, due_at=payload.get("due_at"))
    await events.satellite_chime(config.REMINDER_CHIME_PATH)
    return {"ok": True, "shown": True}


@app.get("/sounds/{name}")
def sound(name: str) -> FileResponse:
    """Short WAVs the satellite fetches when we ask it to play one by URL."""
    safe = os.path.basename(name)
    path = os.path.join(os.path.dirname(__file__), "sounds", safe)
    if not os.path.exists(path):
        raise HTTPException(404, "no such sound")
    return FileResponse(path, media_type="audio/wav")


@app.get("/audio/{name}")
def audio(name: str) -> FileResponse:
    # basename guard against path traversal
    safe = os.path.basename(name)
    path = os.path.join(config.ANNOUNCE_CACHE_DIR, safe)
    if not os.path.exists(path):
        raise HTTPException(404, "no such audio")
    return FileResponse(path, media_type="audio/wav")


# --- home-commands editor (phone page linked from the homelab homepage) -----

@app.get("/home-commands")
def home_commands() -> dict:
    return {"ok": True, "commands": home_mod.snapshot(),
            "threshold": home_mod._THRESHOLD}


@app.get("/home-commands/match")
def home_commands_match(q: str) -> dict:
    """Dry-run a phrase against the alias table — never presses a button."""
    return home_mod.evaluate(q)


@app.post("/home-commands/alias")
def home_commands_add_alias(payload: dict = Body(...)) -> dict:
    try:
        entry = home_mod.add_alias(payload.get("command") or "",
                                   payload.get("alias") or "")
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    return {"ok": True, "entry": entry}


@app.post("/home-commands/alias/delete")
def home_commands_remove_alias(payload: dict = Body(...)) -> dict:
    try:
        entry = home_mod.remove_alias(payload.get("command") or "",
                                      payload.get("alias") or "")
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    return {"ok": True, "entry": entry}


@app.get("/home-commands/ui")
def home_commands_ui() -> FileResponse:
    return FileResponse(
        os.path.join(os.path.dirname(__file__), "home_commands_ui.html"),
        media_type="text/html")


# -- broadcast REST (Voice Notes phone app) ---------------------------------
@app.post("/phone/found")
async def phone_found() -> dict:
    """Phone-side find-my-phone stop: the notification's Found It action (or
    a swipe-dismiss) fires an HA mobile_app event; Node-RED (tab "Find
    Phone") bridges it here. Cancelling the loop also clears the alert."""
    stopped = phone_mod.stop()
    log.info("phone found (ring %s)", "stopped" if stopped else "not active")
    return {"stopped": stopped}


@app.get("/broadcast/rooms")
def broadcast_rooms() -> dict:
    """Room chips for the phone UI — table edits reach the app without an
    app release."""
    return {"ok": True, "rooms": broadcast_mod.rooms_list()}


@app.post("/broadcast")
async def broadcast_send(payload: dict = Body(...)) -> dict:
    """Explicit-rooms send (no intent parse, no fuzzy matching): the phone
    UI already made both target and message unambiguous."""
    volume = payload.get("volume")
    if volume is not None and not (
            isinstance(volume, int) and 0 <= volume <= 100):
        raise HTTPException(400, "volume must be an int 0-100")
    try:
        sent = await broadcast_mod.send(
            payload.get("rooms"), payload.get("message") or "", volume)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    except Exception as exc:  # noqa: BLE001 — HA down / publish failed
        log.warning("broadcast REST publish failed: %s", exc)
        raise HTTPException(502, "couldn't reach the speakers")
    return {"ok": True, "sent": sent}
