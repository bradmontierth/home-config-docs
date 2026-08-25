"""Partial blind positions — the one home command that carries a number.

Every other home command is a curated button press with no payload (see
home_control.py). A position can't work that way: "80 percent" is a value, and
a button per value is not a table anyone would maintain. So this module owns a
small curated map of spoken blind names to cover entities and makes one
`cover.set_cover_position` call against it.

That is a deliberate exception to "all real behavior lives in Node-RED", and a
narrow one: the entity list here is closed, the only argument is a number
clamped to 0-100, and there is no logic behind it to get out of sync with a
flow. Anything that needs behavior (the glare pair, brighten staging) stays a
button.

DIRECTION IS THE VERB, not the number. HA's position is openness -- 100 is
fully up -- but nobody says "close the blind to 20 percent" meaning "leave it
80 percent open". So:

    "open the blinds to 80"   -> position 80   (mostly up)
    "close the blinds to 80"  -> position 20   (mostly down)
    "set the blinds to 80"    -> position 80   (bare verb = openness, as HA
                                                and the dashboard show it)

Parsing lives in intent.fast_parse_cover_level, next to the other deterministic
grammars; this module owns only the targets and the call.
"""

from __future__ import annotations

import logging

import httpx

from . import config
from .weather import _token  # same mounted ha_token; avoid a third copy

log = logging.getLogger("orchestrator.covers")

# Spoken name -> entities. `sats` scopes an entry to the rooms it belongs to,
# exactly as home_commands.json does: standing in the master bath, "the blind"
# is the one over the tub, not the four in the kitchen.
_TARGETS: dict[str, dict] = {
    "blind_left": {
        "entities": ["cover.kitchen_left_shade"],
        "aliases": ["left blind", "left shade", "left kitchen blind",
                    "kitchen left blind", "left window"],
        "spoken": "left blind",
    },
    "blind_right": {
        "entities": ["cover.kitchen_right_shade"],
        "aliases": ["right blind", "right shade", "right kitchen blind",
                    "kitchen right blind", "right window"],
        "spoken": "right blind",
    },
    "blind_sink": {
        "entities": ["cover.sink_shade"],
        "aliases": ["sink blind", "sink shade", "sink", "kitchen sink",
                    "kitchen sink blind", "blind above the sink",
                    "blind over the sink", "window over the sink"],
        "spoken": "sink blind",
    },
    "blind_slider": {
        "entities": ["cover.kitchen_sliding_kitchen_door"],
        "aliases": ["sliding door blind", "sliding door", "slider",
                    "slider blind", "big blind", "big one", "door blind"],
        "spoken": "sliding door blind",
    },
    "blind_small": {
        "entities": ["cover.kitchen_left_shade", "cover.kitchen_right_shade"],
        "aliases": ["small blinds", "little blinds", "two small blinds",
                    "left and right blinds"],
        "spoken": "small blinds",
    },
    "blinds_all": {
        "entities": ["cover.kitchen_left_shade", "cover.kitchen_right_shade",
                     "cover.sink_shade", "cover.kitchen_sliding_kitchen_door"],
        "aliases": ["blinds", "blind", "shades", "kitchen blinds",
                    "all the blinds", "all the kitchen blinds",
                    "all of the blinds", "kitchen shades", "windows"],
        "spoken": "kitchen blinds",
    },
    "blind_bath": {
        "entities": ["cover.upstairs_bath_blind"],
        "aliases": ["blind", "blinds", "shade", "bath blind", "bathroom blind",
                    "bath shade", "bathroom shade"],
        "spoken": "bathroom blind",
        "sats": ["master"],
    },
    "blind_simon": {
        "entities": ["cover.boys_room_baby_blind"],
        "aliases": ["blind", "blinds", "shade", "my blind", "my shade"],
        "spoken": "blind",
        "sats": ["simon"],
    },
    "blind_claire": {
        "entities": ["cover.adrienne_office_bali_shades_windowshade"],
        "aliases": ["blind", "blinds", "shade", "my blind", "my shade"],
        "spoken": "blind",
        "sats": ["claire"],
    },
}

# Rooms named out loud, so the kitchen blinds stay reachable from anywhere and
# a named room beats the room you are standing in. Mirrors home_control.
_ROOM_WORDS = {
    "kitchen": "kitchen", "bath": "master", "bathroom": "master",
    "shower": "master", "closet": "master",
    "simon": "simon", "simon's": "simon", "simons": "simon",
    "claire": "claire", "claire's": "claire", "claires": "claire",
}


_KID_ROOMS = {"simon", "claire"}


def _strip_kid_room(phrase: str, room: str) -> str:
    """Drop the words that name a kid's room ("in Claire's room", "Simon's")
    so what is left is the blind's own name."""
    words = [w for w in phrase.split() if _ROOM_WORDS.get(w) != room]
    # "the blind in [Claire's] room" -> "the blind"
    while len(words) >= 2 and words[-1] == "room" and words[-2] in ("in", "the"):
        words = words[:-2]
    if words and words[-1] == "room":
        words = words[:-1]
    return " ".join(words)


def _visible(entry: dict, sat: str | None) -> bool:
    sats = entry.get("sats")
    return not sats or sat in sats


def named_room(phrase: str) -> str | None:
    """The room a phrase names, if it names exactly one."""
    rooms = {_ROOM_WORDS[w] for w in phrase.split() if w in _ROOM_WORDS}
    return next(iter(rooms)) if len(rooms) == 1 else None


def resolve(phrase: str, sat: str | None = None) -> str | None:
    """Target key for a spoken blind name, or None.

    Exact alias match only -- no fuzzy scoring. home_control can afford fuzz
    because a miss there just presses nothing; here a near-miss would move a
    different window to a position nobody asked for. Room-local aliases are
    tried first so "the blind" means the local one.
    """
    phrase = " ".join((phrase or "").lower().split())
    if not phrase:
        return None
    for prefix in ("the ", "a ", "an ", "my ", "our "):
        if phrase.startswith(prefix):
            phrase = phrase[len(prefix):]
            break
    room = named_room(phrase)
    if room and room != "kitchen":
        sat = room
    # A named room is not part of the blind's name: "the kitchen blinds" and
    # "the blinds" are the same target.
    if room == "kitchen":
        phrase = " ".join(w for w in phrase.split() if _ROOM_WORDS.get(w) != "kitchen")
        sat = None
    # Likewise a kid's name: "Claire's blind" and, in her room, "the blind"
    # are the same window.
    if room in _KID_ROOMS:
        phrase = _strip_kid_room(phrase, room)
    local = {k: v for k, v in _TARGETS.items() if v.get("sats") and _visible(v, sat)}
    house = {k: v for k, v in _TARGETS.items() if not v.get("sats")}
    for pool in (local, house):
        for key, entry in pool.items():
            if phrase in entry["aliases"]:
                return key
    return None


def spoken(target: str) -> str:
    entry = _TARGETS.get(target) or {}
    return entry.get("spoken") or "blind"


def entities(target: str) -> list[str]:
    return list((_TARGETS.get(target) or {}).get("entities") or [])


async def _set_position(entity_ids: list[str], position: int) -> None:
    async with httpx.AsyncClient(timeout=8) as client:
        r = await client.post(
            f"{config.HA_URL}/api/services/cover/set_cover_position",
            headers={"Authorization": f"Bearer {_token()}"},
            json={"entity_id": entity_ids, "position": position})
        r.raise_for_status()


async def handle(parsed: dict) -> dict | None:
    """Move a curated blind to a position. None means "nothing I control",
    which the app answers exactly as it answers a home_control miss."""
    target = parsed.get("cover_target")
    position = parsed.get("cover_position")
    targets = entities(target)
    if not targets or not isinstance(position, int):
        return None
    await _set_position(targets, position)
    log.info("cover %s -> %s%% (%s)", target, position, ", ".join(targets))
    # "...percent open" rather than a bare number: asking to close a blind 80
    # percent gets position 20, and "setting it to 20 percent" sounds like a
    # mishear. Stating the convention makes every phrasing land the same way.
    #
    # Optimistic, like home_control: the service call returns long before the
    # blind stops moving, and waiting would only make the reply late.
    return {"response": f"Setting the {spoken(target)} to {position} percent open.",
            "ok": True}
