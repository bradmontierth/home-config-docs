"""Home-control intent — curated voice buttons pressed via HA.

The voice layer is deliberately dumb: a fixed alias table
(home_commands.json, hot-reloaded on mtime so alias edits never need a
container restart) fuzzy-matched against the parsed phrase, then one HA
`button/press` call on the matching button.voice_* entity. All real behavior
lives in Node-RED behind those buttons (tab "Voice Buttons"); worst case of
any mismatch is a flow Brad wrote running at an odd time — locks, garage,
and alarm have no buttons at all.

Below-threshold matches return None and the app answers "I don't control
that." — deliberately NO ask fallback, so a control phrase can never turn
into a web search. Matching is plain full-string fuzz.ratio (WRatio's
partial/token heuristics scored "turn on the sprinklers" ≈ "close the sink
blind" above any usable threshold): fuzzy only absorbs ASR noise and small
filler, paraphrases belong in the alias table. A wrong action is worse than
a miss, so prefer misses.
"""

from __future__ import annotations

import json
import logging
import re
import time
from pathlib import Path

import httpx
from rapidfuzz import fuzz

from . import config
from .weather import _token  # same mounted ha_token; avoid a third copy

log = logging.getLogger("orchestrator.home_control")

_THRESHOLD = 80

# Politeness/filler that ASR or habit prepends/appends; stripped before
# scoring so "could you close the blinds please" scores as "close the blinds".
_LEAD_FILLER = re.compile(
    r"^(?:(?:uh|um|hey|ok|okay|so|please|can you|could you|would you|"
    r"will you|go ahead and)\s+)+")
_TAIL_FILLER = re.compile(r"(?:\s+(?:please|for me|now|thanks|thank you))+$")


def _clean(query: str) -> str:
    q = _LEAD_FILLER.sub("", query)
    return _TAIL_FILLER.sub("", q).strip()

# Words that pin the phrase to one specific blind. When exactly one pin is
# present, only that blind's commands (plus the non-blind commands) are
# eligible — so "close the left blind" can never fuzzy-drift onto the right
# blind, whose aliases differ by one short word.
_PIN_WORDS = {
    "left": "blind_left",
    "right": "blind_right",
    "sink": "blind_sink",
    "sliding": "blind_slider",
    "slider": "blind_slider",
    "big": "blind_slider",
    "small": "blind_small",
    "little": "blind_small",
}

_commands_cache: tuple[float, dict] | None = None  # (mtime, parsed json)


def _commands() -> dict:
    global _commands_cache
    path = Path(config.HOME_COMMANDS_FILE)
    mtime = path.stat().st_mtime
    if _commands_cache is None or _commands_cache[0] != mtime:
        _commands_cache = (mtime, json.loads(path.read_text()))
        log.info("home commands loaded: %d", len(_commands_cache[1]))
    return _commands_cache[1]


def _match(query: str) -> tuple[str, dict, float] | None:
    """Best (key, entry, score) over all aliases, or None below threshold."""
    commands = _commands()
    query = _clean(query)
    words = set(re.findall(r"[a-z']+", query))
    pins = {_PIN_WORDS[w] for w in words if w in _PIN_WORDS}
    if len(pins) == 1:
        pin = next(iter(pins))
        commands = {k: v for k, v in commands.items()
                    if not k.startswith("blind") or k.startswith(pin)}

    best: tuple[str, dict, float] | None = None
    for key, entry in commands.items():
        score = max(fuzz.ratio(query, a.lower()) for a in entry["aliases"])
        if best is None or score > best[2]:
            best = (key, entry, score)
    if best and best[2] >= _THRESHOLD:
        return best
    log.info("no home command match for %r (best=%s %.0f)",
             query, best[0] if best else None, best[2] if best else 0)
    return None


async def _press(entity: str) -> None:
    async with httpx.AsyncClient(timeout=5) as client:
        r = await client.post(
            f"{config.HA_URL}/api/services/button/press",
            headers={"Authorization": f"Bearer {_token()}"},
            json={"entity_id": entity})
        r.raise_for_status()


async def handle(parsed: dict, command: str) -> dict | None:
    """Match and press. Returns the spoken confirmation, or None on a miss
    (the app speaks the refusal; nothing to follow up on, so no remember())."""
    query = (parsed.get("query") or command or "").strip().lower()
    if not query:
        return None
    matched = _match(query)
    if not matched:
        return None
    key, entry, score = matched
    started = time.monotonic()
    await _press(entry["entity"])
    log.info("home control %r -> %s (score %.0f, press %.0fms)",
             query, key, score, (time.monotonic() - started) * 1000)
    # Optimistic confirmation — the service call returns before the blinds
    # finish moving, and that's correct.
    return {"response": entry["confirm"], "ok": True}
