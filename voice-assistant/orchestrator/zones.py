"""Per-satellite reply routing.

Most satellites answer out of their own speaker: the orchestrator renders the
reply to a WAV and hands back an `audio_url` the satellite fetches and plays.

A satellite listed in satellite_zones.json does NOT do that. Its spoken reply
is published to the whole-home audio zone instead, over the same
voice/broadcast -> Node-RED "Voice Broadcast" -> Amp Speakers chain the
intercom already uses. That chain owns the snapcast workarounds (tail padding,
amp standby wake) and re-renders the text itself, so the orchestrator skips
its own TTS entirely on this path.

The satellite needs no code change to cooperate: assistant.py only plays a
reply when the response carries an `audio_url` (assistant.py:811), so omitting
it is the whole opt-out. Its local wake chime is a separate play_file() call
and is unaffected -- which is exactly the master closet arrangement: ding on
the little USB speaker, answer out the master bath speakers.

Hot-reloaded on mtime, same as home_commands.json / broadcast_rooms.json.
"""

from __future__ import annotations

import json
import logging
import shutil
import time
from pathlib import Path

from rapidfuzz import fuzz

from . import config

log = logging.getLogger("orchestrator.zones")

_cache: tuple[float, dict] | None = None  # (mtime, parsed json)

_SEED_FILE = Path(__file__).with_name("satellite_zones.json")


def _path() -> Path:
    """Live table path, seeded from the repo copy on first use."""
    path = Path(config.SATELLITE_ZONES_FILE)
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(_SEED_FILE, path)
        log.info("seeded satellite zones at %s", path)
    return path


def _table() -> dict:
    global _cache
    path = _path()
    mtime = path.stat().st_mtime
    if _cache is None or _cache[0] != mtime:
        _cache = (mtime, json.loads(path.read_text()))
        log.info("satellite zones loaded: %s", sorted(_cache[1]))
    return _cache[1]


def mute_ms_for(text: str) -> int:
    """How long the satellite should ignore its mic after a zone reply.

    Lead only. We do NOT mute through the speech: the mic reopens as the reply
    starts, hears it, and is_echo() discards it. That makes the follow-up
    window self-timing instead of estimated, and leaves barge-in possible.
    """
    return config.ZONE_MUTE_LEAD_MS


# What each zone-routed satellite last said, so we can recognise it coming
# back in through the mic. Tiny and per-satellite; no eviction needed beyond
# the freshness window, since the key set is the satellite list.
_last_reply: dict[str, tuple[float, str]] = {}


def note_reply(sat: str | None, text: str) -> None:
    if sat and text:
        _last_reply[sat] = (time.time(), text)


def is_echo(sat: str | None, transcript: str) -> bool:
    """True if `transcript` is this satellite hearing its own last reply."""
    if not sat or not transcript:
        return False
    entry = _last_reply.get(sat)
    if not entry:
        return False
    said_at, said = entry
    if time.time() - said_at > config.ZONE_ECHO_WINDOW_S:
        return False
    score = fuzz.ratio(transcript.strip().lower(), said.strip().lower())
    if score >= config.ZONE_ECHO_THRESHOLD:
        log.info("echo drop sat=%s score=%.0f %r", sat, score, transcript)
        return True
    return False


def route_for(sat: str | None) -> dict | None:
    """Reply route for a satellite id, or None to keep the default
    answer-on-the-satellite behaviour. A malformed table must never take the
    voice assistant down, so any parse error degrades to 'no routing'."""
    if not sat:
        return None
    try:
        entry = _table().get(sat)
    except Exception as exc:  # noqa: BLE001 — bad JSON edited by hand
        log.warning("satellite zones unreadable, replies stay local: %s", exc)
        return None
    if not entry or not entry.get("rooms"):
        return None
    return entry
