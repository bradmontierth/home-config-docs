"""Moonrise, moonset, and the moon's phase, computed locally.

Unlike sun.py this leans on a library: the moon's orbit needs a few hundred
perturbation terms to land within a minute, which is not worth hand-rolling.
astronomy-engine is pure Python with no data files and no network, accurate to
about a minute for rise/set (checked against USNO tables in test_moon.py).

The moon rises ~50 minutes later each day, so on roughly one day a month it
never rises (or never sets) on that calendar date. Answers always say when the
next one is instead of just "it doesn't".
"""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta, timezone

import astronomy

from . import config

KINDS = ("moonrise", "moonset", "moon_phase", "full_moon")

# (upper bound of phase angle in degrees, name). The four principal phases are
# instants; ±10° (about 20 hours) around each is what people call "a full moon".
_PHASES = (
    (10, "a new moon"), (80, "a waxing crescent"), (100, "a first quarter moon"),
    (170, "a waxing gibbous"), (190, "a full moon"), (260, "a waning gibbous"),
    (280, "a last quarter moon"), (350, "a waning crescent"), (360, "a new moon"),
)
_QUARTERS = ("new moon", "first quarter moon", "full moon", "last quarter moon")


def _observer(lat: float | None, lon: float | None) -> astronomy.Observer:
    return astronomy.Observer(config.HOME_LAT if lat is None else lat,
                              config.HOME_LON if lon is None else lon, 0)


def _atime(dt: datetime) -> astronomy.Time:
    u = dt.astimezone(timezone.utc)
    return astronomy.Time.Make(u.year, u.month, u.day, u.hour, u.minute,
                               u.second + u.microsecond / 1e6)


def _local(t: astronomy.Time, tz) -> datetime:
    dt = t.Utc().replace(tzinfo=timezone.utc)
    # Round to the minute, same as sun.py: it's spoken as "3:12 PM".
    dt = (dt + timedelta(seconds=30)).replace(second=0, microsecond=0)
    return dt.astimezone(tz)


def _midnight(day: date, tz) -> datetime:
    return datetime(day.year, day.month, day.day, tzinfo=tz)


def next_event(after: datetime, kind: str, lat: float | None = None,
               lon: float | None = None, days: float = 3) -> datetime | None:
    """First moonrise/moonset strictly after `after` (aware), in after's tz."""
    direction = (astronomy.Direction.Rise if kind == "moonrise"
                 else astronomy.Direction.Set)
    hit = astronomy.SearchRiseSet(astronomy.Body.Moon, _observer(lat, lon),
                                  direction, _atime(after), days)
    return None if hit is None else _local(hit, after.tzinfo)


def event(day: date, kind: str, tz, lat: float | None = None,
          lon: float | None = None) -> datetime | None:
    """Local moonrise/moonset on calendar `day`, or None if there isn't one."""
    start, end = _midnight(day, tz), _midnight(day + timedelta(days=1), tz)
    # Search past the day's end and then cut: the library's window edge can
    # miss an event a few minutes before it (a 23:54 moonrise came back None).
    at = next_event(start, kind, lat, lon, days=2)
    return at if at is not None and at < end else None


def phase(when: datetime) -> tuple[str, int]:
    """("a waxing gibbous", 92) — name and percent illuminated."""
    t = _atime(when)
    angle = astronomy.MoonPhase(t)
    name = next(n for top, n in _PHASES if angle < top)
    lit = astronomy.Illumination(astronomy.Body.Moon, t).phase_fraction
    return name, round(lit * 100)


def next_quarter(after: datetime, quarter: int) -> datetime:
    """Next instant of quarter 0=new, 1=first, 2=full, 3=last, in after's tz."""
    q = astronomy.SearchMoonQuarter(_atime(after))
    while q.quarter != quarter:
        q = astronomy.NextMoonQuarter(q)
    return _local(q.time, after.tzinfo)


# Spoken shapes, matched on the cleaned apostrophe-free utterance and anchored
# end to end like sun.py ("moonrise in tokyo" misses and reaches the model).
# ASR splits and possessives vary: "moonrise", "moon rise", "moons rise".
_DAY = r"(?:\s+(?P<day>today|tonight|this evening|this morning|tomorrow(?: morning| night| evening)?|yesterday))?"
_EV = (r"(?P<{}>moon\s*rise|moons\s+rise|moon\s*set|moons\s+set"
       r"|moon\s+(?:rise|set|come\s+up|come\s+out|go\s+down|be\s+up))")
_RISESET = re.compile(
    r"^(?:(?:do you know|tell me|hey)\s+)*(?:"
    rf"(?:(?:what\s+time|when)\s+(?:is|was|will|does|did)|whens)\s+(?:the\s+)?{_EV.format('e1')}"
    rf"|(?:whats|what\s+is|what\s+was)\s+(?:the\s+)?(?:time\s+(?:of|for)\s+)?(?:the\s+)?"
    rf"(?:(?:todays|tomorrows|tonights)\s+)?{_EV.format('e2')}(?:\s+time)?"
    rf"|(?:the\s+)?(?:(?:todays|tomorrows|tonights)\s+)?{_EV.format('e3')}(?:\s+time)?)"
    rf"{_DAY}$")
_PHASE = re.compile(
    r"^(?:(?:do you know|tell me|hey)\s+)*(?:"
    r"(?:whats|what\s+is)\s+the\s+(?:phase\s+of\s+the\s+moon|moon\s+phase|moons\s+phase)"
    r"|what\s+phase\s+is\s+the\s+moon(?:\s+in)?"
    r"|how\s+full\s+is\s+the\s+moon"
    r"|is\s+(?:it|the\s+moon|there)\s+(?:a\s+)?full(?:\s+moon)?"
    r"|moon\s+phase)"
    r"(?:\s+(?:today|tonight|right now|now))?$")
_FULL = re.compile(
    r"^(?:(?:do you know|tell me|hey)\s+)*(?:"
    r"(?:when|what\s+day)\s+(?:is|will\s+be|s)\s+the\s+(?:next\s+)?full\s+moon"
    r"|whens\s+the\s+(?:next\s+)?full\s+moon"
    r"|when\s+is\s+it\s+(?:a\s+)?full\s+moon(?:\s+again)?"
    r"|next\s+full\s+moon)$")


def fast_parse(text: str) -> tuple[str, str] | None:
    """Cleaned utterance -> (kind, day) or None."""
    text = re.sub(r"\s+(?:again|please)$", "", text.replace("'", ""))
    if _FULL.fullmatch(text):
        return "full_moon", "today"
    if _PHASE.fullmatch(text):
        return "moon_phase", "today"
    m = _RISESET.fullmatch(re.sub(r"\s+(?:right now|now)$", "", text))
    if not m:
        return None
    ev = m.group("e1") or m.group("e2") or m.group("e3")
    kind = "moonset" if ("set" in ev or "go down" in ev) else "moonrise"
    said = (m.group("day") or "") + " " + text
    day = ("tomorrow" if "tomorrow" in said
           else "yesterday" if "yesterday" in said else "today")
    return kind, day


# ---- spoken answers -------------------------------------------------------

def _spoken_time(dt: datetime) -> str:
    return dt.strftime("%-I:%M %p").replace(":00 ", " ")


def _when(at: datetime, now: datetime) -> str:
    """"at 3:12 PM", "at 11:54 PM tonight", "at 12:04 AM tomorrow", "at 1:15 PM Thursday"."""
    delta = (at.date() - now.date()).days
    if delta == 0:
        tail = " tonight" if at.hour >= 17 else ""
    else:
        tail = " tomorrow" if delta == 1 else f" {at:%A}"
    return f"at {_spoken_time(at)}{tail}"


def _phase_sentence(when: datetime) -> str:
    name, lit = phase(when)
    if name in ("a new moon", "a full moon"):
        return f"It's {name}."
    return f"It's {name}, {lit} percent lit."


def answer(kind: str, day: str, now: datetime) -> str:
    if kind == "moon_phase":
        name, _ = phase(now)
        q = astronomy.SearchMoonQuarter(_atime(now))
        nxt = _local(q.time, now.tzinfo)
        label = _QUARTERS[q.quarter]
        days = (nxt.date() - now.date()).days
        on = ("today" if days == 0 else "tomorrow" if days == 1
              else f"on {nxt:%A}" if days < 7 else f"on {nxt:%B} {nxt.day}")
        return f"{_phase_sentence(now)} The next {label} is {on}."
    if kind == "full_moon":
        nxt = next_quarter(_midnight(now.date(), now.tzinfo), 2)
        if nxt.date() == now.date():
            if nxt > now:
                return f"Today's the full moon. It's exactly full at {_spoken_time(nxt)}."
            return f"Today's the full moon. It was exactly full at {_spoken_time(nxt)}."
        return f"The next full moon is {nxt:%A}, {nxt:%B} {nxt.day}."

    name = "Moonrise" if kind == "moonrise" else "Moonset"
    verb = "rise" if kind == "moonrise" else "set"
    offset = {"today": 0, "tomorrow": 1, "yesterday": -1}.get(day, 0)
    target = (now + timedelta(days=offset)).date()
    at = event(target, kind, now.tzinfo)
    if day == "yesterday":
        if at is None:
            return f"The moon didn't {verb} yesterday."
        return f"{name} yesterday was at {_spoken_time(at)}."
    if day == "tomorrow":
        if at is None:
            nxt = next_event(_midnight(target, now.tzinfo), kind)
            return (f"The moon doesn't {verb} tomorrow. "
                    f"Next {kind} is {_when(nxt, now)}. {_phase_sentence(nxt)}")
        return f"{name} tomorrow is at {_spoken_time(at)}. {_phase_sentence(at)}"
    if at is not None and at > now:
        return f"{name} is at {_spoken_time(at)}. {_phase_sentence(at)}"
    nxt = next_event(now, kind)
    if at is None:
        lead = f"The moon doesn't {verb} today."
    else:
        lead = f"{name} was at {_spoken_time(at)} today."
    return f"{lead} Next {kind} is {_when(nxt, now)}. {_phase_sentence(nxt)}"
