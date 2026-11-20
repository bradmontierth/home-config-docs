"""Christmas tree + star colors — "set the star to red", "make the tree green".

The tree is one WLED controller ("Tree Audio Reactive", 409 LEDs) with the
body on LEDs 0-299 and the star on 300-408. Node-RED's Christmas Tree tab
loads random presets from Pico / TV events, and those presets do NOT keep a
stable segment id for the star (it is id 1 in most, id 2 in Meteor/Off/White,
id 19 in TV). So this module reads the live segment layout and classifies by
position: a segment starting at or past STAR_START is star, the rest is tree.
Only the asked-for part changes; the other keeps whatever it was doing.

Same shape as chaos.py: a closed grammar, colors from chaos_colors.json, one
direct call to the device (the orchestrator container reaches the IoT VLAN).
"""
from __future__ import annotations

import colorsys
import logging
import os
import re

import httpx

from .chaos import COLORS, _LOOKUP

log = logging.getLogger("orchestrator.tree")

WLED_URL = os.environ.get("TREE_WLED_URL", "http://192.168.30.11")
STAR_START = 300

_PARTS = r"(?:the\s+)?(?:christmas\s+)?(?:tree|star)"
_REQUEST = re.compile(
    r"^(?:(?:please|can you|could you|hey)\s+)?"
    r"(?:set|make|turn|change|switch|color|colour|paint)\s+"
    r"(?P<body>.*?)\s*(?:please)?[.!?]*$")
# One clause: "the star to red", "the tree green", "the color of the star to
# gold", "the star's color to blue", "the tree lights to white".
_CLAUSE = re.compile(
    r"^(?:the\s+)?(?:colou?r\s+of\s+(?:the\s+)?)?(?:christmas\s+)?"
    r"(?P<part>tree|star)(?:'s|s)?(?:\s+(?:lights?|colou?r))?"
    r"(?:\s+(?:to|into)(?:\s+be)?)?(?:\s+(?:the\s+)?colou?r)?"
    r"(?:\s+(?P<color>.+))?$")
_NOT_COLORS = {"on", "off", "up", "down", "brighter", "dimmer", "back"}
_SPLIT = re.compile(rf"\s+and\s+(?={_PARTS}\b)")


def parse(command: str) -> dict[str, str | None] | None:
    """{"tree": color, "star": color} for the parts named, or None if unrelated.

    A named part with an unknown color maps to None (the caller asks again).
    "the tree and star to red" — a clause with no color takes the next one's.
    """
    text = " ".join((command or "").lower().replace(",", " ").split())
    m = _REQUEST.fullmatch(text)
    if not m:
        return None
    clauses = _SPLIT.split(m["body"])
    parsed = []
    for clause in clauses:
        c = _CLAUSE.fullmatch(clause.strip())
        if not c:
            return None
        parsed.append([c["part"], c["color"]])
    if parsed[-1][1] is None:
        return None  # "turn the tree" / "set the star" — no color at all
    if any(c and c.split()[0] in _NOT_COLORS for _, c in parsed):
        return None  # "turn the tree off" is power, not a color
    for i in range(len(parsed) - 2, -1, -1):
        if parsed[i][1] is None:
            parsed[i][1] = parsed[i + 1][1]
    out: dict[str, str | None] = {}
    for part, color in parsed:
        key = re.sub(r"^the |[\s-]", "", color.strip())
        out[part] = _LOOKUP.get(key)
    return out


def rgbw(name: str) -> list[int]:
    hue, sat, val = COLORS[name]
    if sat == 0:  # white: use the strip's real white emitter
        return [0, 0, 0, round(255 * val / 100)]
    r, g, b = colorsys.hsv_to_rgb(hue / 360, sat / 100, val / 100)
    return [round(r * 255), round(g * 255), round(b * 255), 0]


def payload(state: dict, colors: dict[str, str]) -> dict:
    """The /json/state body for the asked-for parts, given the live state."""
    master_on = bool(state.get("on")) and state.get("bri", 0) > 0
    segs = []
    for seg in state.get("seg") or []:
        part = "star" if seg.get("start", 0) >= STAR_START else "tree"
        if part in colors:
            segs.append({"id": seg["id"], "on": True, "fx": 0,
                         "col": [rgbw(colors[part])]})
        elif not master_on:
            # The tree was off: light only what was asked for.
            segs.append({"id": seg["id"], "on": False})
    body: dict = {"seg": segs}
    if not master_on:
        body.update({"on": True, "bri": max(int(state.get("bri") or 0), 128)})
    return body


def _spoken(colors: dict[str, str]) -> str:
    if len(colors) == 2 and colors["tree"] == colors["star"]:
        return f"Setting the tree and star to {colors['tree']}."
    bits = [f"the {p} to {colors[p]}" for p in ("tree", "star") if p in colors]
    return "Setting " + " and ".join(bits) + "."


async def handle(colors: dict[str, str | None]) -> dict:
    if not colors or None in colors.values():
        return {"ok": False,
                "response": "I didn't catch the color. Try set the star to red."}
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            r = await client.get(f"{WLED_URL}/json/state")
            r.raise_for_status()
            body = payload(r.json(), colors)
            r = await client.post(f"{WLED_URL}/json/state", json=body)
            r.raise_for_status()
    except httpx.HTTPError as exc:
        log.warning("tree color failed: %s", exc)
        return {"ok": False,
                "response": "The tree isn't answering. Is it plugged in?"}
    log.info("tree color %s -> %s", colors, body)
    return {"ok": True, "response": _spoken(colors)}
