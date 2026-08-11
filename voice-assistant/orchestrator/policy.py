"""Fail-closed usage policy for child-room voice satellites."""

from __future__ import annotations

import json
import logging
import shutil
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx

from . import config
from .weather import _token

log = logging.getLogger("orchestrator.policy")

_SEED_FILE = Path(__file__).with_name("satellite_policies.json")
_table_cache: tuple[float, dict] | None = None
_state_cache: dict[str, tuple[float, str | None, str | None]] = {}


def _path() -> Path:
    path = Path(config.SATELLITE_POLICIES_FILE)
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(_SEED_FILE, path)
        log.info("seeded satellite policies at %s", path)
    return path


def _table() -> dict:
    global _table_cache
    path = _path()
    mtime = path.stat().st_mtime
    if _table_cache is None or _table_cache[0] != mtime:
        _table_cache = (mtime, json.loads(path.read_text()))
        log.info("satellite policies loaded: %s", sorted(_table_cache[1]))
    return _table_cache[1]


def _minutes(value: str) -> int:
    hour, minute = value.split(":", 1)
    return int(hour) * 60 + int(minute)


def _in_quiet_hours(rule: dict) -> bool:
    now = datetime.now(ZoneInfo(rule.get("timezone", "America/Denver")))
    current = now.hour * 60 + now.minute
    start = _minutes(rule.get("quiet_start", "20:00"))
    end = _minutes(rule.get("quiet_end", "07:00"))
    if start > end:
        return current >= start or current < end
    return start <= current < end


async def _guard_state(entity: str) -> tuple[str | None, str | None]:
    cached = _state_cache.get(entity)
    if cached and time.monotonic() - cached[0] < config.SATELLITE_POLICY_CACHE_S:
        return cached[1], cached[2]
    state = error = None
    try:
        async with httpx.AsyncClient(timeout=2) as client:
            response = await client.get(
                f"{config.HA_URL}/api/states/{entity}",
                headers={"Authorization": f"Bearer {_token()}"},
            )
            response.raise_for_status()
            state = str(response.json().get("state", "")).lower()
    except Exception as exc:  # noqa: BLE001 - caller applies fail_closed
        error = type(exc).__name__
        log.warning("policy guard read failed entity=%s: %s", entity, exc)
    _state_cache[entity] = (time.monotonic(), state, error)
    return state, error


async def evaluate(sat: str | None) -> dict:
    """Return a stable JSON policy decision. Unlisted satellites stay allowed."""
    rule = _table().get(sat or "")
    if not rule:
        return {"allowed": True, "reason": "no_policy", "sat": sat}
    if _in_quiet_hours(rule):
        return {"allowed": False, "reason": "quiet_hours", "sat": sat}
    entity = rule.get("guard_entity")
    if not entity:
        return {"allowed": True, "reason": "daytime", "sat": sat}
    state, error = await _guard_state(entity)
    if error:
        allowed = not bool(rule.get("fail_closed", True))
        return {
            "allowed": allowed,
            "reason": "guard_unavailable" if not allowed else "guard_unavailable_open",
            "sat": sat,
        }
    blocking = str(rule.get("guard_blocking_state", "on")).lower()
    if state == blocking:
        return {"allowed": False, "reason": "guard_entity", "sat": sat}
    return {"allowed": True, "reason": "daytime", "sat": sat}
