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

from . import ask as ask_mod
from . import home_control as home_mod
from . import lists as lists_mod
from . import music as music_mod
from . import broadcast as broadcast_mod
from . import find_phone as phone_mod
from . import places as places_mod
from . import speaker as speaker_mod
from . import sports as sports_mod
from . import weather as weather_mod
from . import clients, config, events, format as fmt, intent as intent_mod, verify
from .timers import RINGING, TimerEngine

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
    _SESSION["pending"] = {"op": "clarify", "partial": partial, "question": question,
                           "kind": kind, "label": label, "sound_theme": theme,
                           "owner": owner}
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
# wake arbitration (two satellites, one house)
# --------------------------------------------------------------------------
# First VERIFIED wake wins the turn (deterministic: arrival order at this
# single-threaded event loop); the loser is told suppressed=true and shadow-
# captures. Built-in correctness during music: the drowned kitchen mic fails
# verify, so the far family-room mic wins by default, not by racing. Checked
# twice per /verify — at entry (cheap, skips ASR when the race is already
# lost) and again after our own ASR verifies (the other mic may have finished
# verifying while ours was decoding).
_ARB = {"sat": None, "until": 0.0}


def _arb_holder(sat: str) -> str | None:
    """The OTHER satellite currently holding the turn, if any."""
    if _ARB["sat"] and _ARB["sat"] != sat and time.time() < _ARB["until"]:
        return _ARB["sat"]
    return None


def _arb_claim(sat: str) -> None:
    _ARB["sat"] = sat
    _ARB["until"] = time.time() + config.ARB_SUPPRESS_S


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
    await events.emit(event_type, items=items, **extra)


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
    if intent == "home_control":
        return f"you gave a home command and heard: {(result.get('response') or '')[:100]}"
    if intent == "broadcast" and result.get("broadcast"):
        b = result["broadcast"]
        return f"you broadcast {parsed.get('query')!r} to {b['spoken']}"
    if intent == "find_phone":
        if "stopped" in result:
            return "you stopped the find-my-phone ringing"
        p = result.get("phone")
        return (f"you rang {p['spoken']}" if p
                else "you asked whose phone should ring")
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
    return await intent_mod.parse_clarify(
        clarify["partial"], reply, clarify["question"])


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
                await events.emit("transcript", text=command)
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
    if clarify:
        parsed = await _parse_clarify_reply(clarify, command)
    else:
        parsed = await intent_mod.parse(command, context=context)
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
            if any(t["id"] in ringing_ids for t in cancelled):
                await events.alarm_stop()
            await events.emit("timer_cancelled", scope="all", timers=ENGINE.active())
        else:
            timer = ENGINE.cancel(parsed["label"])
            if timer and timer["id"] in ringing_ids:
                await events.alarm_stop()
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
        owner = owner or await _speaker_name(speaker_task)
        private = intent_mod.wants_private(text)
        try:
            added = await lists_mod.add_from_text(text, owner=owner, private=private)
        except Exception as exc:  # noqa: BLE001
            log.warning("list add failed: %s", exc)
            result["response"] = "Sorry, I couldn't reach the lists service."
            result["ok"] = False
        else:
            result["added"] = added
            result["response"] = fmt.summarize_added(added)
            # Name the voice-resolved owner aloud (shopping is household-
            # shared, so attribution is only worth speaking for the rest):
            # cheap trust-building plus the audible correction path. A private
            # item says so instead — they asked for the quiet path and need to
            # hear that it took.
            if private:
                result["response"] += " Just on your phone."
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
            await events.emit("show_list", list_type=list_type, items=items,
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

    elif intent == "home_control":
        hc_result = None
        try:
            hc_result = await home_mod.handle(parsed, command)
        except Exception as exc:  # noqa: BLE001 — HA down/slow; still refuse below
            log.warning("home control failed: %s", exc)
        if hc_result:
            result.update(hc_result)
        else:
            # Deliberately NOT the ask fallback (contrast with sports/weather):
            # a control phrase must never turn into a web search or a guess.
            result["response"] = "I don't control that."
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

    elif intent in ("business_hours", "place_search"):
        places_result = None
        try:
            places_result = await places_mod.handle(parsed)
        except Exception as exc:  # noqa: BLE001 — slow ask path is the fallback
            log.warning("business-hours lookup failed, falling back to ask: %s", exc)
        if places_result:
            result.update(places_result)
            await events.emit("show_places", **places_result["places_view"])
            # Preserve the named place for a pronoun follow-up handled by ask.
            ask_mod.remember(command, places_result["response"])
        else:
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

    elif intent == "show_answer":
        # Recall the last answer: the handler re-emits ask_thinking/ask_full to
        # rebuild the fullscreen answer; the spoken part is re-spoken here.
        show_result = await ask_mod.handle_show_answer()
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


@app.post("/verify")
async def verify_wake(request: Request, sat: str = "kitchen") -> dict:
    """Phase 1: stage-2 verification on the pre-roll (wake phrase audio only).
    Fast path so the satellite can chime the instant the wake word is confirmed,
    then start capturing the command. `command` is any speech already trailing
    the wake phrase in the pre-roll (if the user ran it together).

    Dual decode: a full-pre-roll reject gets a second decode of just the tail
    (VERIFY_TAIL_S); either passing verifies the turn. This rescues wake words
    spoken OVER another voice — Parakeet is single-speaker and latches onto the
    stream with more context, so the competing voice's lead-in must be cut, not
    out-fuzzed. Runs only on rejects; the passing path costs nothing extra."""
    wav = await request.body()
    if not wav:
        raise HTTPException(400, "empty audio body")
    t0 = time.time()
    winner = _arb_holder(sat)
    if winner:
        # Race already lost — don't burn an ASR decode (it's the same
        # utterance the winner just verified) and don't double the dashboard
        # badge events. The satellite shadow-captures on this response.
        log.info("verify sat=%s suppressed (winner=%s)", sat, winner)
        return {"verified": False, "suppressed": True, "winner": winner}
    # Fire-and-forget: this POST to the dashboard sat serially BEFORE the ASR
    # call, putting a cosmetic badge (with a 4s timeout tail) on the chime path.
    asyncio.create_task(events.emit("verifying"))
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
    log.info("verify sat=%s transcript=%r verified=%s score=%s decode=%s",
             sat, transcript, verified, score, decode)
    if verified:
        winner = _arb_holder(sat)
        if winner:
            # The other mic's verify completed while our ASR ran. Its events
            # already drove the dashboard; go quiet.
            log.info("verify sat=%s suppressed post-ASR (winner=%s)", sat, winner)
            return {"verified": False, "suppressed": True, "winner": winner}
        _arb_claim(sat)
    await events.emit("wake_confirmed" if verified else "wake_rejected",
                      score=score, transcript=transcript)
    return {
        "verified": verified, "score": score, "transcript": transcript,
        "command": command, "decode": decode,
        "latency_ms": round((time.time() - t0) * 1000),
    }


@app.post("/command/audio")
async def command_audio(request: Request, followup: bool = False,
                        stitched: bool = False, sat: str = "kitchen") -> dict:
    """Phase 2: the captured command utterance. Transcribe + act. No wake check —
    /verify already gated this turn. `followup=1` marks a continued-conversation
    turn (no wake word): background speech is dropped silently. `stitched=1`
    marks wake-turn audio with the 2.5s pre-roll prepended (the satellite's fix
    for run-together commands losing their first words in the stage-1 detect
    gap): strip everything through the wake phrase before acting."""
    wav = await request.body()
    if not wav:
        raise HTTPException(400, "empty audio body")
    t0 = time.time()
    transcript = await clients.transcribe(wav)
    log.info("command sat=%s followup=%s transcript=%r", sat, followup, transcript)
    if stitched and transcript:
        wake_found, command, _ = verify.verify_and_extract(transcript)
        if wake_found:
            log.info("stitched %r -> command %r", transcript, command)
            transcript = command
        # No wake phrase in the decode (odd, /verify just matched this audio):
        # keep the full transcript — a stray lead-in beats a lost turn.
    if not transcript:
        # Silence: on a follow-up, stay quiet; on a wake turn, say we missed it.
        if not followup:
            await events.emit("response", text="I didn't catch that.", intent="none")
        return {"ok": False, "transcript": "", "response": "",
                "intent": "none", "silent": followup}
    if not followup:
        await events.emit("transcript", text=transcript)
    # Speaker ID: in active mode the embed starts now (concurrent with intent
    # parsing) and person-dependent handlers await it lazily; in shadow mode
    # the scoring runs entirely off-turn. Either way every turn is logged to
    # SPEAKER_SHADOW_LOG — the soak data that watches the thresholds.
    spk_task = None
    if config.SPEAKER_MODE == "active":
        spk_task = asyncio.create_task(speaker_mod.identify(wav))
    result = await handle_command(transcript, followup=followup,
                                  speaker_task=spk_task)
    result["transcript"] = transcript
    result["latency_ms"] = round((time.time() - t0) * 1000)
    if spk_task is not None:
        asyncio.create_task(speaker_mod.log_task(
            spk_task, transcript, intent_name=result.get("intent"), sat=sat,
            followup=followup))
    else:
        asyncio.create_task(speaker_mod.shadow(
            wav, transcript, intent_name=result.get("intent"), sat=sat,
            followup=followup))
    return result


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
    # Awaited before responding (see events.alarm_stop docstring): the
    # satellite's own end-of-ring POST lands here too, and its next queued
    # alarm must not start until this dismiss has been delivered.
    await events.alarm_stop()
    await events.emit("timer_dismissed", timer=timer, timers=ENGINE.active())
    return {"ok": True, "timer": timer}


@app.post("/timers/{timer_id}/cancel")
async def cancel_timer(timer_id: str) -> dict:
    row = ENGINE.get(timer_id)
    was_ringing = bool(row and row["state"] == RINGING)
    timer = ENGINE.cancel_by_id(timer_id)
    if not timer:
        raise HTTPException(404, "no active timer with that id")
    if was_ringing:
        await events.alarm_stop()
    await events.emit("timer_cancelled", timer=timer, timers=ENGINE.active())
    return {"ok": True, "timer": timer}


@app.post("/alarm/stop")
async def alarm_stop_route() -> dict:
    """Front door for 'make the ringing stop' (kiosk tap/swipe, phone). Marks
    the ringing timer dismissed if the engine knows of one, but silences the
    satellite UNCONDITIONALLY — state skew must never keep the alarm ringing."""
    timer = ENGINE.dismiss_any_ringing()
    await events.alarm_stop()
    if timer:
        await events.emit("timer_dismissed", timer=timer, timers=ENGINE.active())
    return {"ok": True, "timer": timer}


@app.post("/timers/{timer_id}/unattended")
async def unattended_timer(timer_id: str) -> dict:
    """Satellite watchdog escalation: the alarm has been ringing ~15s with
    nobody dismissing it — push to the household phones so dinner doesn't
    burn while everyone is upstairs."""
    timer = ENGINE.get(timer_id)
    if not timer or timer["state"] != RINGING:
        return {"ok": False, "reason": "not ringing"}
    name = fmt.timer_name(timer)
    dur = fmt.humanize_seconds(timer.get("duration_seconds") or 0)
    body = f"The {name} ({dur}) is ringing in the kitchen and nobody has stopped it."
    await events.phone_alert(
        "Kitchen timer unattended", body,
        event_type="timer_unattended", timer_id=timer_id,
    )
    await events.emit("timer_unattended", timer=timer, timers=ENGINE.active())
    return {"ok": True}


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
