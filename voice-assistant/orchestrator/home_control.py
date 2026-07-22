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
import shutil
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

# The copy baked into the image alongside this module — the versioned seed.
_SEED_FILE = Path(__file__).with_name("home_commands.json")


def _path() -> Path:
    """Live table path; seeded from the repo copy on first use (the live file
    sits in /data so the phone editor can write it — a single-file :ro bind
    mount would go stale whenever a git operation swapped the host inode)."""
    path = Path(config.HOME_COMMANDS_FILE)
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(_SEED_FILE, path)
        log.info("seeded home commands at %s", path)
    return path


def _commands() -> dict:
    global _commands_cache
    path = _path()
    mtime = path.stat().st_mtime
    if _commands_cache is None or _commands_cache[0] != mtime:
        _commands_cache = (mtime, json.loads(path.read_text()))
        log.info("home commands loaded: %d", len(_commands_cache[1]))
    return _commands_cache[1]


def _save(commands: dict) -> None:
    path = _path()
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(commands, indent=2, ensure_ascii=False) + "\n")
    tmp.replace(path)
    global _commands_cache
    _commands_cache = None  # force reload on next read


def snapshot() -> dict:
    """Deep copy of the live table for the editor UI."""
    return json.loads(json.dumps(_commands()))


def _best(query: str) -> tuple[str, dict, float] | None:
    """Best (key, entry, score) for an already-cleaned query, no threshold."""
    commands = _commands()
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
    return best


def _match(query: str) -> tuple[str, dict, float] | None:
    """Best (key, entry, score) over all aliases, or None below threshold."""
    query = _clean(query)
    best = _best(query) if query else None
    if best and best[2] >= _THRESHOLD:
        return best
    if best:
        log.info("no home command match for %r (best=%s %.0f)",
                 query, best[0], best[2])
    return None


def evaluate(query: str) -> dict:
    """Score a phrase without pressing anything — the editor's phrase tester.
    Reports the best candidate even when it misses, so a failed phrase can be
    added as an alias of the right command in one tap."""
    cleaned = _clean((query or "").strip().lower())
    best = _best(cleaned) if cleaned else None
    out = {"query": cleaned, "matched": False, "threshold": _THRESHOLD}
    if best:
        key, entry, score = best
        out.update(command=key, score=round(score),
                   confirm=entry["confirm"],
                   matched=score >= _THRESHOLD)
    return out


def add_alias(command: str, alias: str) -> dict:
    """Add an alias to a command and persist. Raises ValueError on anything
    invalid — unknown command, empty alias, or the alias already belonging to
    any command (a phrase must map to exactly one button)."""
    alias = " ".join((alias or "").lower().split())
    if not alias:
        raise ValueError("Alias is empty.")
    commands = snapshot()
    if command not in commands:
        raise ValueError(f"Unknown command {command!r}.")
    for key, entry in commands.items():
        if alias in entry["aliases"]:
            raise ValueError(f'"{alias}" is already an alias of {key}.')
    commands[command]["aliases"].append(alias)
    _save(commands)
    log.info("alias added: %r -> %s", alias, command)
    return commands[command]


def remove_alias(command: str, alias: str) -> dict:
    """Remove an alias and persist. A command must keep at least one alias."""
    commands = snapshot()
    if command not in commands:
        raise ValueError(f"Unknown command {command!r}.")
    entry = commands[command]
    if alias not in entry["aliases"]:
        raise ValueError(f'"{alias}" is not an alias of {command}.')
    if len(entry["aliases"]) == 1:
        raise ValueError("Can't remove a command's last alias.")
    entry["aliases"].remove(alias)
    _save(commands)
    log.info("alias removed: %r from %s", alias, command)
    return entry


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
