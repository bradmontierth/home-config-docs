"""Numeric thermostat setpoints — "set the temperature to 72".

The second home command that carries a number (covers.py is the first). The
kid-room mini splits already have curated buttons for the relative asks
("it's hot in here", "turn on the AC") — those stay buttons, behaviour in
Node-RED. A spoken degree is a value, and a button per degree is not a table
anyone would maintain, so this module owns a closed map of rooms to climate
entities and makes one `climate.set_temperature` call.

Same narrow exception as covers.py: closed entity list, one clamped number,
no logic that can drift from a flow — except one decision the call cannot
avoid: a mini split that is OFF (or in dry / fan-only, which hold no setpoint)
has no mode to apply a temperature to. Rather than refuse ("set it to 72" in
a hot room is the clearest request there is), pick the mode from where the
room is: cool when the room is warmer than the ask, heat when it is colder.
Already conditioning (cool / heat / auto): keep the mode, move the setpoint.

Parsing lives in intent.fast_parse_climate_setpoint, next to the cover
grammar; this module owns the targets, the room resolution and the call.
"""

from __future__ import annotations

import logging

import httpx

from . import config
from .weather import _token  # same mounted ha_token; avoid a third copy

log = logging.getLogger("orchestrator.climate")

# Room key -> climate entity. `sats` scopes exactly as covers/home_control do:
# the bare "set the temperature to 72" is the mini split in the room you are
# standing in, and a named room ("set Claire's room to 70") wins from anywhere.
# Rooms without an entry (kitchen, family room, master closet) resolve to
# nothing and the phrase falls through to the classifier untouched.
_TARGETS: dict[str, dict] = {
    "simon": {
        "entity": "climate.hvac100_my_heat_pump",   # "simonhvac2" mini split
        "spoken": "Simon's room",
        "sats": ["simon"],
    },
    "claire": {
        "entity": "climate.clairehvac2_my_heat_pump",
        "spoken": "Claire's room",
        "sats": ["claire"],
    },
}

# Spoken words that name a room with a mini split. Mirrors covers/home_control
# (kid names + possessives); "simons room" arrives apostrophe-less from ASR.
_ROOM_WORDS = {
    "simon": "simon", "simon's": "simon", "simons": "simon",
    "claire": "claire", "claire's": "claire", "claires": "claire",
}

# Device range if HA does not report one (both kid units report 61–79 °F).
_FALLBACK_MIN = 61.0
_FALLBACK_MAX = 79.0

# Modes that hold a setpoint. dry / fan_only accept set_temperature on this
# integration but do nothing with it, so they are treated like off.
_CONDITIONING = {"cool", "heat", "auto", "heat_cool"}


def named_room(phrase: str) -> str | None:
    """The room a phrase names, if it names exactly one with a mini split."""
    rooms = {_ROOM_WORDS[w] for w in (phrase or "").lower().split()
             if w in _ROOM_WORDS}
    return next(iter(rooms)) if len(rooms) == 1 else None


def resolve(phrase: str, sat: str | None = None) -> str | None:
    """Target key for the room this phrase means, or None.

    A room named in the phrase beats the room the speaker is standing in;
    with no name, the satellite's own room — and only if it has a mini split.
    """
    room = named_room(phrase) or sat
    entry = _TARGETS.get(room or "")
    if not entry:
        return None
    return room


def entity(target: str) -> str | None:
    return (_TARGETS.get(target) or {}).get("entity")


def spoken(target: str) -> str:
    return (_TARGETS.get(target) or {}).get("spoken") or "the room"


async def _state(entity_id: str) -> dict:
    async with httpx.AsyncClient(timeout=5) as client:
        r = await client.get(
            f"{config.HA_URL}/api/states/{entity_id}",
            headers={"Authorization": f"Bearer {_token()}"})
        r.raise_for_status()
        return r.json()


async def _set_temperature(entity_id: str, data: dict) -> None:
    async with httpx.AsyncClient(timeout=8) as client:
        r = await client.post(
            f"{config.HA_URL}/api/services/climate/set_temperature",
            headers={"Authorization": f"Bearer {_token()}"},
            json={"entity_id": entity_id, **data})
        r.raise_for_status()


def decide(state: dict, setpoint: float) -> tuple[dict, float, str | None]:
    """The service data for a requested setpoint given the entity's state.

    Returns (data, applied_setpoint, mode_turned_on). Pure, so the mode rule
    is testable without HA: conditioning keeps its mode; anything else is
    switched on in the direction of the ask (warmer room -> cool).
    """
    attrs = state.get("attributes") or {}
    lo = _num(attrs.get("min_temp"), _FALLBACK_MIN)
    hi = _num(attrs.get("max_temp"), _FALLBACK_MAX)
    applied = max(lo, min(hi, float(setpoint)))
    data: dict = {"temperature": applied}
    mode = str(state.get("state") or "").lower()
    turned_on = None
    if mode not in _CONDITIONING:
        cur = _num(attrs.get("current_temperature"), None)
        # No reading at all: cooling is the ask nine months a year here.
        turned_on = "heat" if (cur is not None and cur < applied) else "cool"
        data["hvac_mode"] = turned_on
    return data, applied, turned_on


def _num(value, default):
    try:
        out = float(value)
    except (TypeError, ValueError):
        return default
    return out if out == out else default  # NaN guard


def _say_degrees(value: float) -> str:
    return str(int(value)) if float(value).is_integer() else f"{value:g}"


async def handle(parsed: dict) -> dict | None:
    """Set a curated room's mini split to a spoken temperature. None means
    "nothing I control", answered exactly like a home_control miss."""
    target = parsed.get("climate_target")
    setpoint = parsed.get("climate_setpoint")
    entity_id = entity(target or "")
    if not entity_id or not isinstance(setpoint, (int, float)) \
            or isinstance(setpoint, bool):
        return None
    state = await _state(entity_id)
    data, applied, turned_on = decide(state, float(setpoint))
    await _set_temperature(entity_id, data)
    log.info("climate %s -> %s (%s) asked=%s mode_on=%s",
             target, data, entity_id, setpoint, turned_on)
    said = _say_degrees(applied)
    if turned_on == "cool":
        response = f"Turning on the AC and setting it to {said}."
    elif turned_on == "heat":
        response = f"Turning on the heat and setting it to {said}."
    else:
        response = f"Setting the temperature to {said}."
    if applied != float(setpoint):
        # Clamped: say the number that actually took, and why.
        edge = "as high" if applied < float(setpoint) else "as low"
        response = response[:-1] + f" — that's {edge} as it goes."
    return {"response": response, "ok": True,
            "climate": {"target": target, "entity": entity_id,
                        "setpoint": applied, "hvac_mode": turned_on}}
