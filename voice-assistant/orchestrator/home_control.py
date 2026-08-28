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

An entry may carry `"sats": ["master", ...]` to make it a room-local command,
visible only from those satellites. Standing in the master bath, "close the
blinds" has to mean the one blind in that room — not the four in the kitchen,
whose aliases differ from it by a single letter. Scoping is what lets the same
natural phrase mean the local thing in each room instead of forcing one of
them into an awkward paraphrase.

The kitchen and family room are one open space with two microphones, and
"close the blinds" is scoped per microphone there too: the kitchen mic means
the four kitchen blinds, the family-room mic the two family-room ones, and
"close ALL the blinds" (either mic) means all six. Fair warning from the
arbitration record (30 days to 2026-08-25): when both mics hear a wake the
kitchen wins 82% of the time — it runs the smaller stage-1 hop — so which mic
"responded" is a latency race, not a proximity signal. Naming the room is the
reliable form until /verify's loudness columns (turns.wake_rms_db) have enough
paired data to attribute the room by which mic heard you louder.
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
#
# The second group is the command verb: "show me pac man", "give me pac man",
# "make it pac man" and "put on pac man" are all the same request, and only
# one of them was ever curated as an alias (live 2026-08-27, Simon's room:
# five "show me <effect>" turns in a row died as unclear/Piano Man while
# "give me <effect>" worked all week). Stripping the verb from BOTH the phrase
# and the alias makes the effect name the thing that scores, whichever verb
# the speaker reaches for. "show me" cannot collide with show_camera /
# place_search ("show me Simon", "show me Home Depot"): the camera check runs
# before this table is consulted, and a bare "simon" / "home depot" is no
# alias of anything.
_LEAD_FILLER = re.compile(
    r"^(?:(?:uh|um|hey|ok|okay|so|please|can you|could you|would you|"
    r"will you|go ahead and|"
    r"show me|give me|make it|set it to|put on|put it on|switch to|"
    r"switch it to|change it to)\s+)+")
_TAIL_FILLER = re.compile(r"(?:\s+(?:please|for me|now|thanks|thank you))+$")


def _fold(text: str) -> str:
    """Apostrophe-blind lowercase: "it's", "its" and the ASR artefact "it s"
    all fold to the same thing, so an alias written either way matches."""
    text = text.lower().replace("\u2019", "'").replace("'", "")
    return re.sub(r"\b(it|that|what|there|let|he|she|who) s\b", r"\1s", text)


def _clean(query: str) -> str:
    q = _LEAD_FILLER.sub("", _fold(query))
    return " ".join(_TAIL_FILLER.sub("", q).split())

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
    "bath": "blind_bath",
    "bathroom": "blind_bath",
}

# Words that name one appliance family and rule out its room-mate: "turn on
# the fan" and "turn on the lights" differ by one noun and sit right at the
# threshold, and the wrong one is a real action in a kid's bedroom. When a
# phrase says "fan", the lights commands are out, and the other way round.
#
# The family room adds "the cans" (four BR30 recessed lights) next to its
# ceiling fan: fuzz.ratio("turn on the fan", "turn on the cans") is 90, so an
# ASR slip on either noun would swap a light for a fan. Same cure.
_EXCLUDE_WORDS = {
    "fan": ("simon_lights", "claire_lights", "fr_cans"),
    "fans": ("fr_cans",),
    "light": ("simon_fan", "claire_fan", "fr_fan"),
    "lights": ("simon_fan", "claire_fan", "fr_fan"),
    "can": ("fr_fan",),
    "cans": ("fr_fan",),
}

# "All" pins a phrase to the house-wide form of a room-split command. Without
# it, "close ALL the blinds" from the kitchen scores 89 against the kitchen's
# own "close the blinds" — and the room-local pool is asked first and wins
# outright, so the six-blind command could never be reached from the two rooms
# it exists for. With one of these words present the local-first pass is
# skipped and the whole reachable table is scored at once, where an exact
# alias (100) beats the near-miss (89). A room's own commands are still in
# that pool, so "turn off all the lights" in Simon's room stays his.
_ALL_WORDS = {"all", "every", "everywhere"}

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
        commands = json.loads(path.read_text())
        # Image updates may add a command while the live /data table already
        # exists (and may contain phone-added aliases). Seed only missing keys;
        # never overwrite a live entry.
        seed = json.loads(_SEED_FILE.read_text())
        missing = {key: value for key, value in seed.items() if key not in commands}
        if missing:
            commands.update(missing)
        # One-time Simon rollout correction from live ASR: "fun" is reliably
        # decoded as "font" on this microphone. Existing /data tables predate
        # these seed aliases, so add just this migration without generally
        # resurrecting aliases a user intentionally removed in the editor.
        fun = commands.get("simon_fun_color") or {}
        fun_aliases = fun.get("aliases") or []
        new_fun_aliases = [alias for alias in ("set a fun color", "set a font color")
                           if alias not in fun_aliases]
        if new_fun_aliases:
            fun_aliases.extend(new_fun_aliases)
            missing["simon_fun_color.aliases"] = new_fun_aliases
        # One-time 2026-08-25 split of the kitchen blinds: blinds_all_* used
        # to be the unscoped "close the blinds" for the four kitchen blinds.
        # It is now the six-blind (kitchen + family room) command, and the
        # per-room phrases moved to blinds_kitchen_* / blinds_family_*. A live
        # table still carrying the old shape (unscoped, with the bare phrase)
        # would keep answering "close all the blinds" with the kitchen four,
        # so replace those two entries with the seed's — nothing was ever
        # phone-added to them (checked before the split).
        for key in ("blinds_all_close", "blinds_all_open"):
            entry = commands.get(key) or {}
            if key in seed and not entry.get("sats"):
                commands[key] = seed[key]
                missing[key] = "resplit"
        if missing:
            tmp = path.with_name(path.name + ".tmp")
            tmp.write_text(json.dumps(commands, indent=2, ensure_ascii=False) + "\n")
            tmp.replace(path)
            mtime = path.stat().st_mtime
            log.info("seeded new home commands: %s", sorted(missing))
        _commands_cache = (mtime, commands)
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


# Naming a room overrides the room you are standing in. Without this, "close
# the kitchen blinds" said in the master bath scores 80 against the bath
# blind's own "close the blinds" and shuts the wrong one; with it, the named
# room wins and the kitchen blinds are also reachable by name from anywhere.
_ROOM_WORDS = {
    "kitchen": "kitchen",
    # The family room shares the kitchen's open space and display, but it is
    # its own satellite and its own pair of blinds: "close the family room
    # blinds" from the kitchen mic must reach them by name.
    "family": "familyroom",
    "bath": "master",
    "bathroom": "master",
    "shower": "master",
    "closet": "master",
    # The kids' rooms, so "close Claire's blind" works from the kitchen and
    # a bare "close the blind" in her room still means hers.
    "simon": "simon",
    "simon's": "simon",
    "simons": "simon",
    "claire": "claire",
    "claire's": "claire",
    "claires": "claire",
}


# Rooms named by a person's name. Unlike "kitchen", whose aliases literally
# contain the word, these are stripped from the phrase once the room is
# known: "close Claire's blind" from the kitchen scores as "close blind"
# against her room's own "close the blind".
_KID_ROOMS = {"simon", "claire"}


def _strip_kid_room(query: str, room: str) -> str:
    words = [w for w in re.findall(r"[a-z']+", query) if _ROOM_WORDS.get(w) != room]
    while len(words) >= 2 and words[-1] == "room" and words[-2] in ("in", "the"):
        words = words[:-2]
    if words and words[-1] == "room":
        words = words[:-1]
    return " ".join(words)


def _named_room(query: str) -> str | None:
    """The room the phrase names, if it names exactly one."""
    rooms = {_ROOM_WORDS[w] for w in re.findall(r"[a-z']+", query)
             if w in _ROOM_WORDS}
    return next(iter(rooms)) if len(rooms) == 1 else None


def _visibility(entry: dict) -> set[str] | None:
    """Satellites an entry may fire from; None means anywhere."""
    sats = entry.get("sats")
    return set(sats) if sats else None


def _local(commands: dict, sat: str | None) -> dict:
    """Only the commands scoped to this satellite's own room."""
    if not sat:
        return {}
    return {k: v for k, v in commands.items()
            if sat in (_visibility(v) or ())}


def _house(commands: dict, sat: str | None) -> dict:
    """Unscoped commands, plus this satellite's own — i.e. everything that is
    allowed to fire from here at all."""
    return {k: v for k, v in commands.items()
            if _visibility(v) is None or sat in _visibility(v)}


def _best(query: str, commands: dict) -> tuple[str, dict, float] | None:
    """Best (key, entry, score) for an already-cleaned query, no threshold."""
    words = set(re.findall(r"[a-z']+", query))
    pins = {_PIN_WORDS[w] for w in words if w in _PIN_WORDS}
    if len(pins) == 1:
        pin = next(iter(pins))
        commands = {k: v for k, v in commands.items()
                    if not k.startswith("blind") or k.startswith(pin)}
    excluded = tuple(p for w in words for p in _EXCLUDE_WORDS.get(w, ()))
    if excluded:
        commands = {k: v for k, v in commands.items()
                    if not k.startswith(excluded)}

    best: tuple[str, dict, float] | None = None
    for key, entry in commands.items():
        # Score the alias both as written and verb-stripped, the way the
        # query was: "pac man" must hit "give me pac man" at 100, not 64.
        score = max(fuzz.ratio(query, x) for a in entry["aliases"]
                    for x in (_fold(a), _clean(a)))
        if best is None or score > best[2]:
            best = (key, entry, score)
    return best


def _match(query: str, sat: str | None = None) -> tuple[str, dict, float] | None:
    """Best (key, entry, score) over all aliases, or None below threshold.

    The room is asked first and wins outright on a hit, so a room-local
    phrase can never lose a three-point fuzzy race to a near-identical
    command somewhere else in the house. Only when nothing local matches does
    the house-wide table get a look. The room is whichever one the phrase
    names, falling back to the one the speaker is standing in.
    """
    query = _clean(query)
    if not query:
        return None
    commands = _commands()
    named = _named_room(query)
    if named in _KID_ROOMS:
        query = _strip_kid_room(query, named)
    # A caller with no room at all reads as the kitchen, as everywhere else
    # in the app (the pre-rooms behaviour, and the phone tester's room).
    sat = named or sat or config.DEFAULT_SAT
    words = set(re.findall(r"[a-z']+", query))
    pools = ((_house(commands, sat),) if words & _ALL_WORDS
             else (_local(commands, sat), _house(commands, sat)))
    for pool in pools:
        if not pool:
            continue
        best = _best(query, pool)
        if best and best[2] >= _THRESHOLD:
            return best
    best = _best(query, _house(commands, sat))
    if best:
        log.info("no home command match for %r (sat=%s best=%s %.0f)",
                 query, sat, best[0], best[2])
    return None


def has_exact_match(query: str, sat: str | None = None) -> bool:
    """Whether this phrase is an explicit reachable alias for this room.

    Used as a pre-classifier deterministic fast path. Unlike fuzzy matching,
    an exact curated alias is safe to promote to home_control immediately: it
    cannot make an unrelated phrase operate a device.
    """
    query = _clean((query or "").strip().lower())
    if not query:
        return False
    commands = _commands()
    sat = _named_room(query) or sat or config.DEFAULT_SAT
    for entry in _house(commands, sat).values():
        for alias in entry["aliases"]:
            if query in (_fold(alias), _clean(alias)):
                return True
    return False


def fuzzy_match(query: str, sat: str | None = None) -> str | None:
    """Key of the room-scoped command this phrase would press, or None.

    The classifier's "none" rescue: a curated alias that clears the same
    threshold the home_control path uses is safe to promote, because a press
    is bounded by the button list and a miss presses nothing."""
    best = _match(query, sat)
    return best[0] if best else None


def evaluate(query: str) -> dict:
    """Score a phrase without pressing anything — the editor's phrase tester.
    Reports the best candidate even when it misses, so a failed phrase can be
    added as an alias of the right command in one tap.

    Scores against the whole table regardless of `sats`: the tester runs from
    a phone with no room of its own, and hiding room-local commands from it
    would make them impossible to tune.
    """
    cleaned = _clean((query or "").strip().lower())
    best = _best(cleaned, _commands()) if cleaned else None
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
    a command reachable from the same room (a phrase must map to exactly one
    button *from wherever it can be said*). Two room-local commands in
    different rooms may share a phrase: that is the whole point of scoping —
    "close the blinds" means the bath blind in the bath and the kitchen ones
    in the kitchen."""
    alias = " ".join((alias or "").lower().split())
    if not alias:
        raise ValueError("Alias is empty.")
    commands = snapshot()
    if command not in commands:
        raise ValueError(f"Unknown command {command!r}.")
    mine = _visibility(commands[command])
    for key, entry in commands.items():
        if key == command or alias not in entry["aliases"]:
            continue
        theirs = _visibility(entry)
        if mine is None or theirs is None or (mine & theirs):
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


async def handle(parsed: dict, command: str,
                 sat: str | None = None) -> dict | None:
    """Match and press. Returns the spoken confirmation, or None on a miss
    (the app speaks the refusal; nothing to follow up on, so no remember())."""
    query = (parsed.get("query") or command or "").strip().lower()
    if not query:
        return None
    matched = _match(query, sat)
    if not matched:
        return None
    key, entry, score = matched
    if entry.get("disabled"):
        log.warning("home control %s blocked by safety interlock", key)
        return {
            "response": entry.get("disabled_response") or "That control is disabled.",
            "ok": False,
        }
    started = time.monotonic()
    await _press(entry["entity"])
    log.info("home control %r -> %s (sat=%s score %.0f, press %.0fms)",
             query, key, sat, score, (time.monotonic() - started) * 1000)
    # Optimistic confirmation — the service call returns before the blinds
    # finish moving, and that's correct.
    return {"response": entry["confirm"], "ok": True}
