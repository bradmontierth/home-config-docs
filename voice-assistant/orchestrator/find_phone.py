"""Find-phone intent — ring a household phone through the HA companion app.

"where's Adrienne's phone" -> the phone rings through silent/DND and shows
a real, dismissible notification. v2 (Brad feedback on the v1 TTS pilot:
robotic "here I am" was grating and there was nothing to tap to stop it):
a REGULAR notification on the special `channel: alarm_stream` plays the
channel's notification sound on the alarm stream — the sound is a per-
phone Android setting (HA app > Notifications > "Alarm Stream" channel),
so each phone picks its own musical ringtone. ttl 0 + priority high are
REQUIRED for delivery to a locked, dozing phone (verified live
2026-07-24; without them the first pilot sat until unlock).

The orchestrator re-posts the same-tagged notification every
FIND_PHONE_INTERVAL_S (each re-post RESTARTS the sound, so the interval
must exceed the ringtone's length — at 5s a ~25s tone never finished) up
to FIND_PHONE_REPEATS times. Three ways to stop: say "found it", tap the
notification's Found It button, or swipe it away — the latter two reach
/phone/found via Node-RED's HA event bridge (tab "Find Phone").

Volume is pegged to full for the ring window and handed back afterwards:
regular notifications ignore `alarm_stream_max` (TTS-only), so it takes
command_volume_level, which the app never reverts on its own. Pegging is
therefore gated on being able to READ the phone's alarm volume first
(phones.json "volume_sensor") — otherwise a restore would be a guess that
silently re-tunes their real morning alarm.

Owner resolution follows the broadcast pattern: a hot-reloaded alias table
(phones.json, mtime-checked) fuzzy-matched with plain fuzz.ratio. "my
phone" cannot be attributed until speaker ID exists (backlog item 9), so
the handler returns needs_owner=True and the app asks "Brad's or
Adrienne's?" and stashes a pending op that resolves the follow-up name
without another LLM parse. When speaker ID lands, "my" resolves there and
this module needs no changes.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re

import httpx
from rapidfuzz import fuzz

from . import config
from .weather import _token  # same mounted ha_token as weather/home_control

log = logging.getLogger("orchestrator.find_phone")

_THRESHOLD = 80

_RING_TAG = "find_phone"

# Upper bound on a believable alarm-stream volume STEP (see _alarm_volume).
_MAX_PLAUSIBLE_STEP = 30

# Words that carry no owner information in an answer like "brad's phone"
# or "ring mine" — stripped before matching so possessives score clean.
_STOP = {"phone", "phones", "the", "a", "ring", "find", "my", "mine", "me",
         "it", "its", "is", "please"}

# Owner words that mean the SPEAKER — unresolvable until speaker ID.
_SELF = {"", "my", "mine", "me", "our", "ours"}


def is_self(owner: str | None) -> bool:
    """True when the parsed phone_owner means the speaker ("my phone")."""
    return (owner or "").strip().lower() in _SELF

_phones_cache: tuple[float, dict] | None = None  # (mtime, parsed json)

_ring_task: asyncio.Task | None = None  # the active ring loop, if any
_diag_task: asyncio.Task | None = None  # peg-effectiveness readback, if any


def _phones() -> dict:
    global _phones_cache
    path = config.PHONES_FILE
    mtime = os.stat(path).st_mtime
    if _phones_cache is None or _phones_cache[0] != mtime:
        with open(path) as fh:
            _phones_cache = (mtime, json.load(fh))
        log.info("phones loaded: %d", len(_phones_cache[1]))
    return _phones_cache[1]


def _spoken_choices() -> str:
    return " or ".join(e["spoken"] for e in _phones().values())


def resolve(text: str) -> dict | None:
    """Best table entry (with its key added) for an owner phrase, or None.

    Accepts anything from a parsed phone_owner ("adrienne", "mom") to a raw
    follow-up answer ("Brad's phone.", "ring adrienne's"): non-letters and
    filler words are stripped first, so only the owner word is scored.
    """
    q = re.sub(r"[^a-z ]", "", (text or "").lower())
    q = " ".join(w for w in q.split() if w not in _STOP)
    if not q:
        return None
    best: tuple[str, dict, float] | None = None
    for key, entry in _phones().items():
        score = max(fuzz.ratio(q, a) for a in entry["aliases"])
        if best is None or score > best[2]:
            best = (key, entry, score)
    if best and best[2] >= _THRESHOLD:
        key, entry, score = best
        log.info("phone owner %r -> %s (score %.0f)", text, key, score)
        return {"key": key, **entry}
    if best:
        log.info("no phone match for %r (best=%s %.0f)", text, best[0], best[2])
    return None


async def _post(service: str, payload: dict) -> None:
    async with httpx.AsyncClient(timeout=10) as client:
        r = await client.post(
            f"{config.HA_URL}/api/services/notify/{service}",
            headers={"Authorization": f"Bearer {_token()}"},
            json=payload)
        r.raise_for_status()


async def _alarm_volume(entry: dict) -> int | None:
    """The phone's CURRENT alarm-stream volume step, or None when it can't
    be read (sensor not configured, or "Volume Levels" not enabled in the
    companion app). None disables pegging — see config.FIND_PHONE_PEG_VOLUME.
    """
    entity = entry.get("volume_sensor")
    if not entity or not config.FIND_PHONE_PEG_VOLUME:
        return None
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            r = await client.get(
                f"{config.HA_URL}/api/states/{entity}",
                headers={"Authorization": f"Bearer {_token()}"})
        if r.status_code == 404:
            log.info("no %s — alarm volume left alone (enable Volume Levels "
                     "in the companion app to peg it)", entity)
            return None
        r.raise_for_status()
        level = int(r.json()["state"])
    except Exception as exc:  # noqa: BLE001 — unavailable/non-numeric state
        log.info("alarm volume unreadable (%s): %s — leaving it alone",
                 entity, exc)
        return None
    # The alarm stream is step-based (7-15 steps typically). A big number
    # would mean the sensor reports a PERCENTAGE, and restoring it verbatim
    # would clamp to max — i.e. silently leave their real alarm at full.
    # Refuse to peg rather than risk that.
    if not 0 <= level <= _MAX_PLAUSIBLE_STEP:
        log.warning("%s = %s is not a plausible volume step — not pegging "
                    "(check the sensor's scale before enabling)", entity, level)
        return None
    return level


def _remember_peg(entry: dict, prior: int) -> None:
    """Journal the pegged level so a restart mid-ring can undo it."""
    try:
        path = config.FIND_PHONE_VOLUME_STATE_FILE
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as fh:
            json.dump({"phone": entry["key"], "prior": prior}, fh)
    except Exception as exc:  # noqa: BLE001 — journal is insurance, not a gate
        log.warning("could not journal pegged volume: %s", exc)


def _forget_peg() -> None:
    try:
        os.remove(config.FIND_PHONE_VOLUME_STATE_FILE)
    except FileNotFoundError:
        pass
    except Exception as exc:  # noqa: BLE001
        log.warning("could not clear volume journal: %s", exc)


async def restore_stranded() -> None:
    """Startup hook: a deploy or crash during a ring skips the in-loop
    restore and would leave the alarm pegged at max (i.e. their real morning
    alarm). Undo it from the journal."""
    try:
        with open(config.FIND_PHONE_VOLUME_STATE_FILE) as fh:
            state = json.load(fh)
    except FileNotFoundError:
        return
    except Exception as exc:  # noqa: BLE001 — corrupt journal, drop it
        log.warning("unreadable volume journal: %s", exc)
        _forget_peg()
        return
    entry = _phones().get(state.get("phone"))
    prior = state.get("prior")
    if not entry or not isinstance(prior, int):
        _forget_peg()
        return
    try:
        await _set_alarm_volume(entry, prior)
        log.warning("alarm volume was left pegged on %s by a restart — "
                    "restored to %s", state["phone"], prior)
        _forget_peg()
    except Exception as exc:  # noqa: BLE001 — retry on the next startup
        log.warning("could not restore stranded alarm volume on %s: %s",
                    state["phone"], exc)


async def _set_alarm_volume(entry: dict, level: int) -> None:
    """command_volume_level on the alarm stream. The app clamps to the
    stream's real max and does NOT revert on its own — every peg needs a
    matching restore."""
    await _post(entry["service"], {
        "message": "command_volume_level",
        "data": {"media_stream": "alarm_stream", "command": level,
                 "ttl": 0, "priority": "high"},
    })


async def _warn_if_peg_ignored(entry: dict, prior: int) -> None:
    """Volume commands need Do Not Disturb access, and without it the app
    DROPS them and only shows a grant-permission notice on the phone — the
    ring just comes out quiet with nothing in our logs (hit live
    2026-07-24). Read the level back and say so plainly.

    Best-effort diagnostic only: it never blocks or fails the ring. Skipped
    entirely once the ring is over, because the restore has already put the
    level back and reading it then looks exactly like a dropped command
    (that false alarm fired on Brad's +6s tap 2026-07-24).
    """
    try:
        await asyncio.sleep(4)
        await _post(entry["service"], {"message": "command_update_sensors",
                                       "data": {"ttl": 0, "priority": "high"}})
        await asyncio.sleep(4)
        if _ring_task is None or _ring_task.done():
            return
        level = await _alarm_volume(entry)
        if level is not None and level <= prior:
            log.warning(
                "alarm volume peg had NO effect on %s (still %s) — grant Do "
                "Not Disturb access to the HA app on that phone: Settings > "
                "Apps > Special app access > Do Not Disturb access",
                entry["key"], level)
        elif level is not None:
            log.info("alarm volume peg confirmed on %s (now %s)",
                     entry["key"], level)
    except asyncio.CancelledError:
        raise
    except Exception as exc:  # noqa: BLE001 — diagnostic only
        log.info("could not confirm the volume peg on %s: %s",
                 entry["key"], exc)


def _ring_payload(entry: dict) -> dict:
    return {
        "title": "Here I am!",
        "message": f"{entry['spoken']} is over here.",
        "data": {
            "tag": _RING_TAG,
            "channel": "alarm_stream",   # sound plays on the alarm stream;
                                         # WHICH sound is the phone-side
                                         # channel setting (pick a musical one)
            "ttl": 0,
            "priority": "high",
            # Auto-dismiss once the ring window is over, so a phone found
            # later (or with Node-RED down) never shows a stale alert.
            "timeout": int(config.FIND_PHONE_REPEATS
                           * config.FIND_PHONE_INTERVAL_S) + 5,
            "actions": [{"action": "FIND_PHONE_FOUND", "title": "Found It"}],
        },
    }


def _clear_payload() -> dict:
    return {"message": "clear_notification",
            "data": {"tag": _RING_TAG, "ttl": 0, "priority": "high"}}


async def _ring_loop(entry: dict, prior_volume: int | None) -> None:
    """Re-post the ring notification (same tag -> sound replays, single
    notification entry) after the first one ring_and_reply already sent, then
    always hand the alarm volume back."""
    cancelled = False
    try:
        for _ in range(max(0, config.FIND_PHONE_REPEATS - 1)):
            await asyncio.sleep(config.FIND_PHONE_INTERVAL_S)
            await _post(entry["service"], _ring_payload(entry))
        # Let the LAST tone finish before handing the volume back — Android
        # applies a stream-volume change to audio already playing, so an
        # immediate restore would drop the final ring mid-phrase.
        if prior_volume is not None:
            await asyncio.sleep(config.FIND_PHONE_INTERVAL_S)
    except asyncio.CancelledError:
        cancelled = True
    except Exception as exc:  # noqa: BLE001 — phone unreachable mid-loop
        log.warning("find-phone ring loop died: %s", exc)
    # Shielded so a cancel mid-teardown can't strand the volume at max or
    # leave the alert on screen.
    if prior_volume is not None:
        try:
            await asyncio.shield(
                asyncio.create_task(_set_alarm_volume(entry, prior_volume)))
            log.info("alarm volume restored to %s on %s",
                     prior_volume, entry["key"])
            _forget_peg()
        except Exception as exc:  # noqa: BLE001 — best-effort restore
            log.warning("alarm volume restore FAILED on %s (left at max): %s",
                        entry["key"], exc)
    if cancelled:
        # Stopped via voice or /phone/found — take the alert off the phone.
        try:
            await asyncio.shield(
                asyncio.create_task(_post(entry["service"], _clear_payload())))
        except Exception as exc:  # noqa: BLE001 — best-effort tidy-up
            log.warning("find-phone clear failed: %s", exc)
        raise asyncio.CancelledError


def stop() -> bool:
    """Cancel the active ring loop (it clears the phone's notification on
    the way out). True if something was ringing."""
    global _ring_task, _diag_task
    task, _ring_task = _ring_task, None
    diag, _diag_task = _diag_task, None
    if diag and not diag.done():
        diag.cancel()   # its readback would race our own restore
    if task and not task.done():
        task.cancel()
        return True
    return False


async def ring_and_reply(entry: dict) -> dict:
    """Peg the alarm volume (when restorable), send the first ring
    synchronously (raises on HA failure so the app can speak a can't-reach),
    then keep ringing in the background."""
    stop()
    prior = await _alarm_volume(entry)
    if prior is not None:
        await _set_alarm_volume(entry, config.FIND_PHONE_MAX_VOLUME)
        _remember_peg(entry, prior)   # journal BEFORE the ring window
        log.info("alarm volume pegged on %s (was %s)", entry["key"], prior)
        global _diag_task
        _diag_task = asyncio.create_task(_warn_if_peg_ignored(entry, prior))
    await _post(entry["service"], _ring_payload(entry))
    global _ring_task
    _ring_task = asyncio.create_task(_ring_loop(entry, prior))
    log.info("ringing %s (%s)", entry["key"], entry["service"])
    return {"response": f"Ringing {entry['spoken']} — say found it, "
                        "or tap the notification, to stop.",
            "ok": True,
            "phone": {"key": entry["key"], "spoken": entry["spoken"]}}


async def handle(parsed: dict) -> dict:
    """Ring the named owner's phone, stop an active ring, or ask whose when
    the owner is the speaker ("my phone") — the app stashes a pending op on
    needs_owner. Raises on HA/notify failure; the app catches and speaks a
    can't-reach."""
    if parsed.get("phone_action") == "stop":
        if stop():
            return {"response": "Okay, stopped.", "ok": True, "stopped": True}
        return {"response": "The phone wasn't ringing.", "ok": True,
                "stopped": False}
    owner = (parsed.get("phone_owner") or "").strip().lower()
    if owner in _SELF:
        whose = " or ".join(e["spoken"].removesuffix(" phone")
                            for e in _phones().values())
        return {"response": f"Whose phone — {whose}?",
                "ok": False, "needs_owner": True}
    entry = resolve(owner)
    if not entry:
        return {"response": f"I only know {_spoken_choices()}.", "ok": False}
    return await ring_and_reply(entry)
