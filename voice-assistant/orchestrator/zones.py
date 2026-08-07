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
from pathlib import Path

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

    The reply is rendered in Node-RED, so we estimate from the text instead of
    measuring. Errs long (see config), which costs a slightly later follow-up
    window; erring short costs a self-heard answer, which is worse.
    """
    speech_ms = len(text or "") / config.ZONE_MUTE_CHARS_PER_SEC * 1000
    total = config.ZONE_MUTE_LEAD_MS + speech_ms + config.ZONE_MUTE_MARGIN_MS
    return int(min(total, config.ZONE_MUTE_MAX_MS))


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
