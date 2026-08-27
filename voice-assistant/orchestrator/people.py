"""Who a list item is FOR when it isn't the speaker — "remind brad to roast
coffee", "put call the plumber on adrienne's to-do list".

Reads phones.json, the one place the household's names and nicknames already
live (find_phone uses the same table), so a new person or alias is a table
edit. Deterministic on purpose: the classifier decides that a command is a
reminder; whose it is comes from the words, which have one meaning.

Two things come out of `target_in`: the roster key, and the command rewritten
back into first person ("remind me to roast coffee"). The rewrite matters —
the companion's extractor only files a reminder when the text says "remind
me"/"reminder", and it would otherwise store "Brad to roast coffee" as the
reminder's own text.
"""
from __future__ import annotations

import json
import logging
import os
import re

from rapidfuzz import fuzz

from . import config

log = logging.getLogger("orchestrator.people")

_THRESHOLD = 80.0
_SELF = {"me", "my", "myself", "us", "our", "ourselves", "mine", "everyone"}

# "remind brad to…" / "please remind mom that…" / "set a reminder for dad to…"
_LEAD_RE = re.compile(
    r"^\s*(?:please\s+)?(?:can\s+you\s+|could\s+you\s+|would\s+you\s+)?"
    r"(?:remind\s+(?P<who>[a-z']+)|(?:set|add|make|create)\s+(?:a|an)?\s*reminder\s+for\s+(?P<who2>[a-z']+))\b",
    re.I)
# "…on brad's list" / "…to adrienne's to-dos"
_POSS_RE = re.compile(r"\b(?P<who>[a-z]+)(?:'s|s')\s+(?=(?:to.?dos?|todos?|reminders?|list)\b)", re.I)

_cache: tuple[float, dict] | None = None


def _roster() -> dict:
    global _cache
    path = config.PHONES_FILE
    mtime = os.stat(path).st_mtime
    if _cache is None or _cache[0] != mtime:
        with open(path) as fh:
            _cache = (mtime, json.load(fh))
    return _cache[1]


def resolve(name: str | None) -> str | None:
    """Roster key for a spoken name/nickname ("brad", "mom", "adrian"), or None."""
    q = re.sub(r"[^a-z]", "", (name or "").lower())
    if not q or q in _SELF:
        return None
    best: tuple[str, float] | None = None
    for key, entry in _roster().items():
        score = max(fuzz.ratio(q, a) for a in entry.get("aliases", [key]))
        if best is None or score > best[1]:
            best = (key, score)
    return best[0] if best and best[1] >= _THRESHOLD else None


def target_in(command: str) -> tuple[str | None, str]:
    """(roster key or None, command rewritten to first person)."""
    m = _LEAD_RE.match(command)
    if m:
        who = m.group("who") or m.group("who2")
        key = resolve(who)
        if key:
            start, end = m.span("who" if m.group("who") else "who2")
            rewritten = command[:start] + "me" + command[end:]
            log.info("list target %r -> %s: %r", who, key, rewritten)
            return key, rewritten
    m = _POSS_RE.search(command)
    if m:
        key = resolve(m.group("who"))
        if key:
            rewritten = command[:m.start()] + "my " + command[m.end():]
            log.info("list target %r -> %s: %r", m.group("who"), key, rewritten)
            return key, rewritten
    return None, command
