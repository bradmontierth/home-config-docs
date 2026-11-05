"""Sunrise and sunset, computed from the house's coordinates.

No service and no library: the NOAA solar-position equations are a couple of
dozen lines and good to about a minute at our latitude, which is finer than
anyone asking "when's sunset" cares about. HOME_LAT/HOME_LON are already in
the container for places.py.

Times are for the standard -0.833° horizon (refraction + the sun's radius),
the same definition HA's sun.sun and every weather app use, so the answer
matches the phone.
"""

from __future__ import annotations

import math
import re
from datetime import date, datetime, timedelta, timezone

from . import config

KINDS = ("sunrise", "sunset")
_ZENITH = 90.833


def _event_utc_minutes(day: date, lat: float, lon: float, rising: bool,
                       guess_min: float) -> float | None:
    """Minutes after 00:00 UTC on `day` of the event, refined from a guess."""
    jd = day.toordinal() + 1721424.5 + guess_min / 1440.0
    t = (jd - 2451545.0) / 36525.0
    l0 = (280.46646 + t * (36000.76983 + t * 0.0003032)) % 360
    m = 357.52911 + t * (35999.05029 - 0.0001537 * t)
    e = 0.016708634 - t * (0.000042037 + 0.0000001267 * t)
    mr = math.radians(m)
    c = (math.sin(mr) * (1.914602 - t * (0.004817 + 0.000014 * t))
         + math.sin(2 * mr) * (0.019993 - 0.000101 * t)
         + math.sin(3 * mr) * 0.000289)
    omega = 125.04 - 1934.136 * t
    app_long = l0 + c - 0.00569 - 0.00478 * math.sin(math.radians(omega))
    obliq = (23 + (26 + (21.448 - t * (46.815 + t * (0.00059 - t * 0.001813))) / 60) / 60
             + 0.00256 * math.cos(math.radians(omega)))
    decl = math.asin(math.sin(math.radians(obliq)) * math.sin(math.radians(app_long)))
    y = math.tan(math.radians(obliq) / 2) ** 2
    l0r = math.radians(l0)
    eq_time = 4 * math.degrees(
        y * math.sin(2 * l0r) - 2 * e * math.sin(mr)
        + 4 * e * y * math.sin(mr) * math.cos(2 * l0r)
        - 0.5 * y * y * math.sin(4 * l0r) - 1.25 * e * e * math.sin(2 * mr))
    latr = math.radians(lat)
    cos_ha = (math.cos(math.radians(_ZENITH)) / (math.cos(latr) * math.cos(decl))
              - math.tan(latr) * math.tan(decl))
    if not -1 <= cos_ha <= 1:
        return None  # polar day/night; can't happen in Utah
    ha = math.degrees(math.acos(cos_ha))
    noon = 720 - 4 * lon - eq_time
    return noon - 4 * ha if rising else noon + 4 * ha


def event(day: date, kind: str, tz, lat: float | None = None,
          lon: float | None = None) -> datetime | None:
    """Local datetime of `kind` ("sunrise"/"sunset") on local calendar `day`."""
    lat = config.HOME_LAT if lat is None else lat
    lon = config.HOME_LON if lon is None else lon
    rising = kind == "sunrise"
    # Minutes are counted from 00:00 UTC on the local date and may run past
    # 1440 (a Mountain sunset is after midnight UTC) — that's fine, the
    # timedelta below carries it into the next UTC day.
    minutes = 720.0
    for _ in range(2):  # second pass re-evaluates the sun at the event itself
        m = _event_utc_minutes(day, lat, lon, rising, minutes)
        if m is None:
            return None
        minutes = m
    utc = (datetime(day.year, day.month, day.day, tzinfo=timezone.utc)
           + timedelta(minutes=minutes))
    # Round to the minute: it's spoken as "6:23 PM", and truncating would
    # read up to a minute early.
    utc = (utc + timedelta(seconds=30)).replace(second=0, microsecond=0)
    return utc.astimezone(tz)


# Spoken shapes. Matched on the cleaned, apostrophe-free utterance and anchored
# end to end, so "what time is sunset in Hawaii" misses and reaches the
# classifier rather than getting our sunset.
_DAY = r"(?:\s+(?P<day>today|tonight|this evening|this morning|tomorrow(?: morning| night| evening)?|yesterday))?"
_SUN = re.compile(
    r"^(?:(?:do you know|tell me|hey)\s+)*"
    r"(?:(?:what\s+time|when)\s+(?:is|was|will\s+be|does|did|will)\s+(?:the\s+)?"
    r"(?:(?P<n1>sunset|sundown|sunrise|sun\s*up)"
    r"|sun\s+(?P<v1>set|go\s+down|come\s+up|rise|come\s+out))"
    r"|(?:whats|what\s+is|what\s+was)\s+(?:the\s+)?(?:time\s+(?:of|for)\s+)?(?:the\s+)?"
    r"(?:(?:todays|tomorrows|tonights)\s+)?(?P<n2>sunset|sundown|sunrise)(?:\s+time)?"
    r"|(?:the\s+)?(?:(?:todays|tomorrows|tonights)\s+)?(?P<n3>sunset|sundown|sunrise)(?:\s+time)?)"
    rf"{_DAY}$")
_SET_WORDS = ("sunset", "sundown", "set", "go down")


def fast_parse(text: str) -> tuple[str, str] | None:
    """Cleaned utterance -> (kind, day) or None."""
    text = re.sub(r"\s+(?:right now|now|again|please)$", "", text.replace("'", ""))
    m = _SUN.fullmatch(text)
    if not m:
        return None
    word = re.sub(r"\s+", " ", m.group("n1") or m.group("v1") or m.group("n2") or m.group("n3"))
    kind = "sunset" if word in _SET_WORDS else "sunrise"
    said = (m.group("day") or "") + " " + text
    if "tomorrow" in said:
        day = "tomorrow"
    elif "yesterday" in said:
        day = "yesterday"
    else:
        day = "today"
    return kind, day
