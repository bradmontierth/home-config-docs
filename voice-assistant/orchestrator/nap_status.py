"""nap_status: "did Claire nap today", "when did Simon go down", "is the baby asleep".

Reads today's naps from Lookout's nap detector (GET /api/naps on the Optiplex, bearer
token from LOOKOUT_TOKEN_FILE) and speaks them. The detector watches Claire's crib, the
basement pack-and-play (also Claire) and Simon's bed; see lookout app/naps.py.

kinds from Lookout: "nap" (asleep), "rest" (in bed but never still enough to count as
asleep: Simon's quiet time), "bedtime" (ignored here). A nap still going is "open": no
awake/out time yet.
"""
from __future__ import annotations

import logging
import re
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx

from . import config

log = logging.getLogger("orchestrator.nap_status")

KIDS = ("claire", "simon")
NAP_KIDS = KIDS + ("both",)
_PRONOUN = {"claire": ("she", "her"), "simon": ("he", "his")}
_NAME = {"claire": "Claire", "simon": "Simon"}

# Spoken forms -> kid. ASR writes Claire as Clare/Clair; "the baby" is Claire.
_KID_WORDS = {
    "claire": "claire", "clare": "claire", "clair": "claire", "the baby": "claire", "baby": "claire",
    "simon": "simon", "simon's": "simon",
    "the kids": "both", "kids": "both", "both kids": "both", "both of the kids": "both",
    "the children": "both", "both of them": "both", "either kid": "both", "everyone": "both",
}
_KID = r"(?P<kid>claire|clare|clair|the baby|baby|simon|the kids|kids|both kids|both of the kids|the children|both of them|either kid)"
_TAIL = r"(?:\s+(?:today|this afternoon|this morning|yet|at all|so far|right now|now))*"
_NAP_PATTERNS = tuple(re.compile(rf"^{body}{_TAIL}$") for body in (
    rf"(?:did|has|have)\s+{_KID}\s+(?:take\s+a\s+|have\s+a\s+|had\s+a\s+)?(?:nap|napped|sleep|slept|go\s+down|gone\s+down|fall\s+asleep|fallen\s+asleep)",
    # A bare "when did Claire wake up" may mean this morning: only "...from her nap" counts.
    rf"(?:when|what\s+time)\s+did\s+{_KID}\s+(?:nap|go\s+down|go\s+to\s+sleep|fall\s+asleep|"
    rf"(?:wake|get)\s+up\s+from\s+(?:her|his|their|the)\s+naps?)",
    rf"how\s+long\s+did\s+{_KID}\s+(?:nap|sleep)(?:\s+for)?",
    rf"(?:is|are)\s+{_KID}\s+(?:still\s+)?(?:asleep|napping|sleeping|down|in\s+bed)",
    rf"(?:did|has|have)\s+{_KID}\s+(?:napped|slept)",
    rf"(?:how(?:'s|\s+is|\s+was)|what\s+about|tell\s+me\s+about)\s+{_KID}(?:'s)?\s+nap",
    rf"{_KID}(?:'s)?\s+nap(?:\s+time)?",
    rf"(?:any|were\s+there\s+any)\s+naps",
    rf"did\s+(?:anyone|anybody|everyone)\s+nap",
))


# The loose net under the exact patterns above, so nobody has to remember a phrasing
# ("what time did the kids sleep today", "when did claire sleep", "who had a nap today",
# "did simon nap tonight" all missed them). A question opener + a nap/daytime-sleep word
# + (a kid, or who/anyone) is a nap question. Overnight sleep ("last night", "bedtime"),
# a bare "wake up" (could be this morning) and commands (play/set/show/tell/remind open
# with a non-question word) stay out.
_LOOSE_OPEN = re.compile(
    r"^(?:did|didn't|has|hasn't|have|haven't|had|when|what|how|is|isn't|are|was|were|who|"
    r"which|any|tell me|do you know|i wonder|"
    # Asked as a request (2026-11-20 miss: "see if the kids take a nap today").
    r"(?:can|could) you (?:see|check|tell me)|see if|check if|check whether|find out)\b")
# "map"/"nab": ASR's usual mishearings of nap ("did simon map today", 2026-11-06).
_LOOSE_NAP = re.compile(r"\b(?:naps?|napped|napping|naptime|map|nab)\b")
_LOOSE_SLEEP = re.compile(
    r"\b(?:sleep|slept|sleeping|asleep|go(?:ne)? down|went down|fall asleep|fell asleep|"
    r"go to sleep|went to sleep)\b")
_LOOSE_NIGHT = re.compile(r"\b(?:last night|overnight|bedtime|bed time|wake up|woke up|"
                          r"this morning|tomorrow|timer|alarm|sleep(?:ing)? over|sleepover)\b")
_LOOSE_KID = re.compile(r"\b(?:" + "|".join(sorted(
    (re.escape(k) + r"(?:'?s)?" for k in _KID_WORDS), key=len, reverse=True)) + r")\b")
_LOOSE_ANYONE = re.compile(r"\b(?:who|anyone|anybody|everyone|either|both)\b")


def _loose_kid(text: str) -> str | None:
    if not _LOOSE_OPEN.match(text):
        return None
    nap = bool(_LOOSE_NAP.search(text))
    if not nap and (not _LOOSE_SLEEP.search(text) or _LOOSE_NIGHT.search(text)):
        return None
    if nap and re.search(r"\b(?:timer|alarm|music|song|playlist)\b", text):
        return None
    found = {_KID_WORDS[re.sub(r"'?s$", "", m.group(0)) if m.group(0) not in _KID_WORDS
                        else m.group(0)] for m in _LOOSE_KID.finditer(text)}
    if len(found) == 1:
        return found.pop()
    if found or _LOOSE_ANYONE.search(text):
        return "both"
    return None


def fast_kid(text: str) -> str | None:
    """A nap question -> "claire" / "simon" / "both", or None. `text` is already cleaned
    (lowercase, punctuation stripped) by intent._fast_clean."""
    for pattern in _NAP_PATTERNS:
        m = pattern.fullmatch(text)
        if m:
            return _KID_WORDS.get((m.groupdict().get("kid") or "").strip(), "both")
    return _loose_kid(text)


def _local(ts: float) -> datetime:
    # The household timezone, not the container's (it runs on UTC).
    return datetime.fromtimestamp(ts, ZoneInfo(config.ASK_TIMEZONE))


def _clock(ts: float) -> str:
    lt = _local(ts)
    return f"{lt.hour % 12 or 12}:{lt.minute:02d}"


def _duration(seconds: float) -> str:
    m = int(round(seconds / 60))
    if m < 50:
        return f"about {max(m, 1)} minute{'s' if m != 1 else ''}"
    q = round(m / 15) / 4          # nearest quarter hour
    whole = int(q)
    frac = q - whole
    hours = "an hour" if whole == 1 else f"{whole} hours"
    if frac == 0:
        return f"about {hours}"
    if frac == 0.5:
        return f"about {hours} and a half"
    return f"about {hours} and {15 if frac == 0.25 else 45} minutes"


def _one_kid(kid: str, naps: list[dict], now: float) -> str:
    name, (he, his) = _NAME[kid], _PRONOUN[kid]
    mine = [n for n in naps if n.get("kid", "").lower() == kid and n.get("kind") != "bedtime"]
    live = next((n for n in mine if n.get("open")), None)
    if live and live.get("kind") == "nap" and live.get("asleep") and not live.get("awake"):
        return (f"{name} is napping now. {he.capitalize()} fell asleep at {_clock(live['asleep'])}, "
                f"{_duration(now - live['asleep'])} ago.")
    if live:
        return f"{name} is in bed now, since {_clock(live['in_bed'])}, but {he} isn't asleep yet."
    slept = [n for n in mine if n.get("kind") == "nap" and n.get("asleep") and n.get("awake")]
    if len(slept) == 1:
        n = slept[0]
        return (f"Yes, {name} napped from {_clock(n['asleep'])} to {_clock(n['awake'])}, "
                f"{_duration(n['awake'] - n['asleep'])}.")
    if slept:
        parts = [f"{_clock(n['asleep'])} to {_clock(n['awake'])}" for n in slept]
        total = sum(n["awake"] - n["asleep"] for n in slept)
        return (f"Yes, {name} napped {'twice' if len(slept) == 2 else f'{len(slept)} times'}: "
                f"{', and '.join(parts)}. That's {_duration(total)} in all.")
    rest = [n for n in mine if n.get("kind") == "rest" and n.get("out")]
    if rest:
        n = rest[-1]
        return (f"{name} had quiet time in bed from {_clock(n['in_bed'])} to {_clock(n['out'])}, "
                f"but {he} didn't fall asleep.")
    late = _local(now).hour >= 19
    return f"{name} didn't nap today." if late else f"{name} hasn't napped yet today."


def answer(naps: list[dict], kid: str, now: float | None = None) -> str:
    now = time.time() if now is None else now
    kids = KIDS if kid == "both" else (kid,)
    return " ".join(_one_kid(k, naps, now) for k in kids)


def _token() -> str:
    return Path(config.LOOKOUT_TOKEN_FILE).read_text(encoding="utf-8").strip()


async def _fetch() -> list[dict]:
    async with httpx.AsyncClient(timeout=6) as client:
        r = await client.get(f"{config.LOOKOUT_URL}/api/naps",
                             headers={"Authorization": f"Bearer {_token()}"})
        r.raise_for_status()
        return r.json().get("naps", [])


async def handle(parsed: dict) -> dict:
    """Answer a nap_status turn. Raises if Lookout can't be reached; the caller refuses."""
    kid = parsed.get("nap_kid") if parsed.get("nap_kid") in NAP_KIDS else "both"
    naps = await _fetch()
    text = answer(naps, kid)
    log.info("nap_status kid=%s naps=%d -> %s", kid, len(naps), text)
    return {"response": text, "ok": True, "nap_status": {"kid": kid, "naps": len(naps)}}
