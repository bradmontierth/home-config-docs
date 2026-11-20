"""Read-only house security status — "is the house secure", "are the windows
closed", "is the garage open".

The answer is read straight from Home Assistant at question time, not from the
Node-RED globals the bedtime summary uses (DoorList / WindowList /
CurrentLockStatus / gateList). Those are copies of HA state kept by state-change
flows, and a copy cannot say "I don't know": on 2026-10-03 CurrentLockStatus was
ten days old and held "locking" for all three Nest x Yale locks, which the
bedtime check reads as not-unlocked. HA itself reports `unavailable`, and a
stuck in-between state carries its own last_changed, so the live read is the
only source that can refuse to vouch for something it cannot see.

The entity list mirrors what the bedtime tab checks (same doors, windows, gates
and locks) so "secure" means the same thing at bedtime and at the door. It is
closed and hand-maintained like covers/climate: a new contact sensor is not in
the answer until it is added here.

The reply is templated, never LLM-worded, and follows the bedtime rule:
"secure" is said only when every item in scope is positively closed or locked.
Unknown, unavailable, jammed, or an in-between state held past
TRANSITION_GRACE_S is named as something it can't confirm — never folded into
an all-clear. Read only: closing the garage or locking up by voice is a
separate decision.
"""

from __future__ import annotations

import logging
import time
from datetime import datetime

import httpx

from . import config
from .weather import _token  # same mounted ha_token; avoid another copy

log = logging.getLogger("orchestrator.home_status")

# Scopes the classifier and fast path can ask for. "all" is the leaving /
# "is the house secure" question; the rest narrow the answer to one group.
SCOPES = ("all", "doors", "garage", "locks", "windows", "gates")

# "locking" / "closing" for longer than this is not a lock in motion, it is a
# state nobody refreshed (the Nest x Yale cookie integration sat on "locking"
# for days). A real garage door travels in ~15 s; a lock bolt in ~3 s.
TRANSITION_GRACE_S = 120

# Group -> spoken nouns, ok/problem words, and items. Each item:
#   entity    primary HA entity
#   fallback  optional second entity, read only when the primary is unknown
#   kind      "contact" (off = closed), "cover" (closed/open/opening/closing),
#             "lock" (locked/unlocked/locking/unlocking/jammed), or
#             "contact_lock" (a bolt contact: off = locked)
#   name      full spoken name, with its article ("the office window")
#   short     list form, used with the group noun ("the office" -> "Two
#             windows are open: the office and the family room"); omit to list
#             full names
# Order inside a group is the order names are spoken.
_GROUPS: dict[str, dict] = {
    "garage": {
        "singular": "garage door", "plural": "garage doors",
        "ok": "closed", "bad": "open",
        "items": [
            {"entity": "cover.big_garage_relay_door",
             "fallback": "binary_sensor.big_garage_door_contact",
             "kind": "cover", "name": "the big garage door", "short": "the big one"},
            {"entity": "cover.small_garage_door_door",
             "fallback": "binary_sensor.small_garage_door_contact",
             "kind": "cover", "name": "the small garage door", "short": "the small one"},
        ],
    },
    "doors": {
        "singular": "door", "plural": "doors",
        "ok": "closed", "bad": "open",
        "items": [
            {"entity": "binary_sensor.front_door_contact",
             "kind": "contact", "name": "the front door"},
            {"entity": "binary_sensor.sliding_door_contact_contact",
             "kind": "contact", "name": "the sliding door"},
            {"entity": "binary_sensor.garage_house_door_contact",
             "kind": "contact", "name": "the door to the garage"},
            {"entity": "binary_sensor.garage_man_door_contact",
             "kind": "contact", "name": "the garage man door"},
        ],
    },
    "locks": {
        "singular": "lock", "plural": "locks",
        "ok": "locked", "bad": "unlocked",
        "items": [
            {"entity": "lock.entryway_lock", "kind": "lock",
             "name": "the entryway lock", "short": "the entryway"},
            {"entity": "lock.backyard_lock", "kind": "lock",
             "name": "the backyard lock", "short": "the backyard"},
            {"entity": "lock.garage_lock", "kind": "lock",
             "name": "the garage lock", "short": "the garage"},
            # Iris contact on the slider's bolt; Node-RED's Locks tab reads it
            # the same way (off = bolt thrown = locked).
            {"entity": "binary_sensor.sliding_door_lock_iris_contact",
             "kind": "contact_lock",
             "name": "the sliding door lock", "short": "the sliding door"},
        ],
    },
    "windows": {
        "singular": "window", "plural": "windows",
        "ok": "closed", "bad": "open",
        "items": [
            {"entity": f"binary_sensor.{key}_window_contact", "kind": "contact",
             "name": f"{short} window", "short": short}
            for key, short in (
                ("family_room", "the family room"),
                ("office", "the office"),
                ("kitchen_sink", "the kitchen sink"),
                ("master_bed", "the master bedroom"),
                ("master_bath", "the master bath"),
                ("laundry", "the laundry room"),
                ("loft", "the loft"),
                ("simon_room", "Simon's room"),
                ("adrienne_office", "Adrienne's office"),
                ("basement_family_room", "the basement family room"),
                ("guest_bedroom", "the guest bedroom"),
                ("bike_room", "the bike room"),
            )
        ],
    },
    "gates": {
        "singular": "gate", "plural": "gates",
        "ok": "closed", "bad": "open",
        "items": [
            {"entity": "binary_sensor.small_gate_contact", "kind": "contact",
             "name": "the small gate", "short": "the small one"},
            {"entity": "binary_sensor.big_fence_gate_contact", "kind": "contact",
             "name": "the big gate", "short": "the big one"},
        ],
    },
}

# Spoken order for the whole-house answer: the garage first, because the big
# door left open while loading the car is the commonest thing to catch.
_ALL_ORDER = ("garage", "doors", "locks", "windows", "gates")

_NUM = ("zero", "one", "two", "three", "four", "five", "six", "seven", "eight",
        "nine", "ten", "eleven", "twelve")

_UNKNOWN_STATES = {"", "unknown", "unavailable", "none"}


def entities() -> list[str]:
    """Every entity this module may read (primary and fallback)."""
    out = []
    for group in _GROUPS.values():
        for item in group["items"]:
            out.append(item["entity"])
            if item.get("fallback"):
                out.append(item["fallback"])
    return out


def _age_s(state: dict, now: float) -> float | None:
    raw = state.get("last_changed") or state.get("last_updated")
    if not raw:
        return None
    try:
        return now - datetime.fromisoformat(str(raw).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def classify(kind: str, state: dict | None, now: float | None = None) -> tuple[str, str | None]:
    """(status, detail) for one entity's HA state dict.

    status is "ok", "bad" (open / unlocked), "moving" (fresh in-between state;
    detail is the word to say, e.g. "closing"), "jammed", or "unknown". Pure,
    so the confirm-or-refuse rule is testable without HA.
    """
    now = time.time() if now is None else now
    if not state:
        return "unknown", None
    value = str(state.get("state") or "").strip().lower()
    if value in _UNKNOWN_STATES:
        return "unknown", None
    if kind == "contact":
        return {"off": ("ok", None), "on": ("bad", None)}.get(value, ("unknown", None))
    if kind == "contact_lock":
        return {"off": ("ok", None), "on": ("bad", None)}.get(value, ("unknown", None))
    if kind == "cover":
        if value == "closed":
            return "ok", None
        if value == "open":
            return "bad", None
    elif kind == "lock":
        if value == "locked":
            return "ok", None
        if value in ("unlocked", "open"):
            return "bad", None
        if value == "jammed":
            return "jammed", None
    if value in ("opening", "closing", "locking", "unlocking", "opening_lock"):
        age = _age_s(state, now)
        if age is not None and 0 <= age <= TRANSITION_GRACE_S:
            return "moving", value
        return "unknown", None
    return "unknown", None


def _item_status(item: dict, states: dict[str, dict], now: float) -> tuple[str, str | None]:
    status, detail = classify(item["kind"], states.get(item["entity"]), now)
    if status == "unknown" and item.get("fallback"):
        # The fallback is a plain contact on the same door: it can say open or
        # closed, but not "closing".
        fb_status, fb_detail = classify("contact", states.get(item["fallback"]), now)
        if fb_status != "unknown":
            return fb_status, fb_detail
    return status, detail


def evaluate(states: dict[str, dict], scope: str = "all",
             now: float | None = None) -> dict:
    """Group -> {"ok": [...items], "bad": [...], "moving": [(item, word)],
    "jammed": [...], "unknown": [...]} for the groups in scope."""
    now = time.time() if now is None else now
    groups = _ALL_ORDER if scope == "all" else (scope,)
    out: dict[str, dict] = {}
    for key in groups:
        buckets: dict[str, list] = {"ok": [], "bad": [], "moving": [],
                                    "jammed": [], "unknown": []}
        for item in _GROUPS[key]["items"]:
            status, detail = _item_status(item, states, now)
            buckets[status].append((item, detail) if status == "moving" else item)
        out[key] = buckets
    return out


def _join(parts: list[str]) -> str:
    if len(parts) <= 1:
        return "".join(parts)
    if len(parts) == 2:
        return f"{parts[0]} and {parts[1]}"
    return ", ".join(parts[:-1]) + f", and {parts[-1]}"


def _count(n: int) -> str:
    return _NUM[n] if 0 <= n < len(_NUM) else str(n)


def _cap(text: str) -> str:
    return text[:1].upper() + text[1:]


def _list_with_noun(items: list[dict], group: dict) -> str:
    """"the entryway, the backyard, and the garage locks" — or full names when
    the group has no short forms ("the front door and the sliding door")."""
    if all(i.get("short") for i in items):
        return f"{_join([i['short'] for i in items])} {group['plural']}"
    return _join([i["name"] for i in items])


def _problem_sentences(key: str, b: dict) -> list[str]:
    group = _GROUPS[key]
    out = []
    bad = b["bad"]
    if len(bad) == 1:
        out.append(f"{_cap(bad[0]['name'])} is {group['bad']}.")
    elif bad:
        if all(i.get("short") for i in bad):
            names = _join([i["short"] for i in bad])
        else:
            names = _join([i["name"] for i in bad])
        out.append(f"{_cap(_count(len(bad)))} {group['plural']} are {group['bad']}: {names}.")
    for item, word in b["moving"]:
        out.append(f"{_cap(item['name'])} is {word}.")
    for item in b["jammed"]:
        out.append(f"{_cap(item['name'])} is jammed.")
    unknown = b["unknown"]
    if unknown:
        if len(unknown) == len(group["items"]) and len(unknown) > 1:
            out.append(f"I can't confirm the {group['plural']}.")
        elif len(unknown) == 1:
            out.append(f"I can't confirm {unknown[0]['name']}.")
        else:
            out.append(f"I can't confirm {_list_with_noun(unknown, group)}.")
    return out


def _has_problem(b: dict) -> bool:
    return bool(b["bad"] or b["moving"] or b["jammed"] or b["unknown"])


def _group_answer(key: str, b: dict) -> str:
    """A one-group question: "are the windows closed"."""
    group = _GROUPS[key]
    total = len(group["items"])
    problems = _problem_sentences(key, b)
    ok = len(b["ok"])
    if not problems:
        if total == 1:
            return f"{_cap(group['items'][0]['name'])} is {group['ok']}."
        lead = "Both" if total == 2 else f"All {_count(total)}"
        return f"{lead} {group['plural']} are {group['ok']}."
    if ok == 1:
        rest = f"{_cap(b['ok'][0]['name'])} is {group['ok']}."
    elif ok > 1:
        rest = f"The other {_count(ok)} are {group['ok']}."
    else:
        rest = ""
    return " ".join(problems + ([rest] if rest else []))


_ROLLUP_NOUN = {"garage": "garage", "doors": "doors", "windows": "windows",
                "gates": "gates"}


def _all_answer(result: dict) -> tuple[str, bool]:
    """The whole-house answer and whether it may be called secure."""
    problems: list[str] = []
    clean_closed: list[str] = []
    locks_clean = False
    for key in _ALL_ORDER:
        b = result[key]
        if _has_problem(b):
            problems.extend(_problem_sentences(key, b))
        elif key == "locks":
            locks_clean = True
        else:
            clean_closed.append(_ROLLUP_NOUN[key])
    if not problems:
        return ("Everything's secure. The garage, doors, windows, and gates are "
                "closed, and the locks are locked."), True
    tail = []
    if clean_closed:
        verb = "is" if clean_closed == ["garage"] else "are"
        tail.append(f"the {_join(clean_closed)} {verb} closed")
    if locks_clean:
        tail.append("the locks are locked")
    if tail:
        # "Otherwise" would claim the partly-open groups' other members too;
        # name only the groups that came back entirely clean.
        problems.append(_cap(" and ".join(tail)) + ".")
    return " ".join(problems), False


def answer(states: dict[str, dict], scope: str = "all",
           now: float | None = None) -> dict:
    """Spoken answer plus a structured summary, from a {entity_id: state} map."""
    scope = scope if scope in SCOPES else "all"
    result = evaluate(states, scope, now)
    if scope == "all":
        response, secure = _all_answer(result)
    else:
        response = _group_answer(scope, result[scope])
        secure = not _has_problem(result[scope])
    summary = {
        key: {
            bucket: [(i[0]["name"], i[1]) if bucket == "moving" else i["name"]
                     for i in items]
            for bucket, items in b.items() if items and bucket != "ok"
        } | {"ok": len(b["ok"])}
        for key, b in result.items()
    }
    return {"response": response, "secure": secure, "scope": scope,
            "groups": summary}


async def _fetch_states() -> dict[str, dict]:
    """One /api/states read, filtered to our entities. A missing entity stays
    missing and classifies as unknown — a renamed sensor must not read closed."""
    wanted = set(entities())
    async with httpx.AsyncClient(timeout=5) as client:
        r = await client.get(f"{config.HA_URL}/api/states",
                             headers={"Authorization": f"Bearer {_token()}"})
        r.raise_for_status()
        return {s["entity_id"]: s for s in r.json()
                if s.get("entity_id") in wanted}


async def handle(parsed: dict) -> dict:
    """Answer a home_status turn. Raises on an HA failure; the caller refuses
    rather than guessing."""
    scope = parsed.get("status_scope") or "all"
    states = await _fetch_states()
    out = answer(states, scope)
    log.info("home_status scope=%s secure=%s groups=%s",
             out["scope"], out["secure"], out["groups"])
    return {"response": out["response"], "ok": True, "home_status": out}
