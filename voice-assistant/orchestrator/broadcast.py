"""Broadcast intent — relay a spoken message to the whole-home audio rooms.

"tell simon to come eat dinner" -> the intent parser rewrites the message into
direct speech ("Simon, come eat dinner") and names a target; this module only
resolves the target to canonical room keys and publishes ONE MQTT message
(voice/broadcast, via HA's mqtt.publish so no broker client is needed here).
Delivery lives entirely in Node-RED (tab "Voice Broadcast" -> Amp Speakers
subflow), which owns the hard-won snapcast workarounds: TTS render, 2s tail
padding (snapserver cuts the tail), and the amp standby wake chime.

Target resolution follows the home_control pattern: a hot-reloaded alias
table (broadcast_rooms.json, mtime-checked) fuzzy-matched with plain
fuzz.ratio. Fallback differs from home_control on purpose: a broadcast should
err toward being HEARD, so no target -> all rooms, and an unknown target
also goes to all rooms but the confirmation says so out loud.
"""

from __future__ import annotations

import json
import logging
import shutil
from pathlib import Path

import httpx
from rapidfuzz import fuzz

from . import config
from .weather import _token  # same mounted ha_token as weather/home_control

log = logging.getLogger("orchestrator.broadcast")

_THRESHOLD = 80

_rooms_cache: tuple[float, dict] | None = None  # (mtime, parsed json)

_SEED_FILE = Path(__file__).with_name("broadcast_rooms.json")


def _path() -> Path:
    """Live table path; seeded from the repo copy on first use (same /data
    pattern as home_commands.json so the phone editor can grow into it)."""
    path = Path(config.BROADCAST_ROOMS_FILE)
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(_SEED_FILE, path)
        log.info("seeded broadcast rooms at %s", path)
    return path


def _rooms() -> dict:
    global _rooms_cache
    path = _path()
    mtime = path.stat().st_mtime
    if _rooms_cache is None or _rooms_cache[0] != mtime:
        _rooms_cache = (mtime, json.loads(path.read_text()))
        log.info("broadcast rooms loaded: %d", len(_rooms_cache[1]))
    return _rooms_cache[1]


def resolve(target: str | None) -> tuple[list[str] | str, str, bool]:
    """(rooms, spoken_name, matched) for a parsed target phrase.

    rooms is a list of canonical keys or the string "all". No target means
    all rooms (matched=True — that's the designed default, not a miss);
    an unrecognized target ALSO returns all rooms but matched=False so the
    confirmation can say the fallback happened.
    """
    table = _rooms()
    if not target:
        return "all", table.get("all", {}).get("spoken", "all the speakers"), True

    query = " ".join(target.lower().split())
    best: tuple[str, dict, float] | None = None
    for key, entry in table.items():
        score = max(fuzz.ratio(query, a.lower()) for a in entry["aliases"])
        if best is None or score > best[2]:
            best = (key, entry, score)
    if best and best[2] >= _THRESHOLD:
        key, entry, score = best
        log.info("broadcast target %r -> %s (score %.0f)", target, key, score)
        return entry["rooms"], entry["spoken"], True

    if best:
        log.info("no broadcast room match for %r (best=%s %.0f) -> all",
                 target, best[0], best[2])
    return "all", table.get("all", {}).get("spoken", "all the speakers"), False


async def _publish(payload: dict) -> None:
    """Publish via HA's mqtt.publish — HA and Node-RED share the broker."""
    async with httpx.AsyncClient(timeout=5) as client:
        r = await client.post(
            f"{config.HA_URL}/api/services/mqtt/publish",
            headers={"Authorization": f"Bearer {_token()}"},
            json={"topic": config.BROADCAST_TOPIC,
                  "payload": json.dumps(payload)})
        r.raise_for_status()


def rooms_list() -> list[dict]:
    """Table as an ordered list for UI chip builders (phone app). "all"
    first, then table order; aliases omitted — they're a voice concern."""
    table = _rooms()
    keys = ["all"] + [k for k in table if k != "all"]
    return [{"key": k, "spoken": table[k]["spoken"], "rooms": table[k]["rooms"]}
            for k in keys if k in table]


async def send(rooms: list[str] | str, message: str,
               volume: int | None = None) -> dict:
    """Direct send with explicit canonical room keys — the phone-app path,
    no fuzzy resolution. Raises ValueError on unknown keys/empty message."""
    message = (message or "").strip()
    if not message:
        raise ValueError("empty message")
    table = _rooms()
    if rooms != "all":
        if not isinstance(rooms, list) or not rooms:
            raise ValueError("rooms must be 'all' or a non-empty list")
        known = {r for k, e in table.items() if isinstance(e["rooms"], list)
                 for r in e["rooms"]}
        bad = [r for r in rooms if r not in known]
        if bad:
            raise ValueError(f"unknown rooms: {bad}")
    payload = {"rooms": rooms, "message": message}
    if volume is not None:
        payload["volume"] = volume
    elif config.BROADCAST_VOLUME is not None:
        payload["volume"] = config.BROADCAST_VOLUME
    await _publish(payload)
    log.info("broadcast sent rooms=%s volume=%s len=%d",
             rooms, payload.get("volume"), len(message))
    return payload


async def handle(parsed: dict) -> dict:
    """Resolve, publish, and return the spoken confirmation. Raises on
    HA/publish failure — the app catches and speaks a can't-reach message."""
    message = (parsed.get("query") or "").strip()
    if not message:
        return {"response": "What should I broadcast?", "ok": False}

    target = parsed.get("broadcast_target")
    rooms, spoken, matched = resolve(target)

    payload = {"rooms": rooms, "message": message}
    if config.BROADCAST_VOLUME is not None:
        payload["volume"] = config.BROADCAST_VOLUME
    await _publish(payload)

    if not matched:
        response = f"I don't know where {target} is, so I sent it everywhere."
    elif rooms == "all":
        response = "Broadcasting upstairs."
    else:
        response = f"Sent to {spoken}."
    return {"response": response, "ok": True,
            "broadcast": {"rooms": rooms, "target": target,
                          "spoken": spoken, "matched": matched}}
