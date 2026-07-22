"""Fast business-hours answers from Google Places API (New).

One Text Search call returns the nearest named business plus current/regular
hours. Results are cached for 24 hours and an in-process daily counter stays
below the Google Cloud SearchText hard quota. Any configuration, resolution,
quota, or API failure returns None so app.py can use the slower ask path.
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx
from rapidfuzz import fuzz

from . import config

log = logging.getLogger("orchestrator.places")

_URL = "https://places.googleapis.com/v1/places:searchText"
_FIELD_MASK = ("places.displayName,places.formattedAddress,"
               "places.regularOpeningHours,places.currentOpeningHours")
_KEY_CACHE: str | None = None
_CACHE: dict[str, tuple[float, dict | None]] = {}
_MATCH_THRESHOLD = 80
_BUDGET_DAY: date | None = None
_BUDGET_COUNT = 0
_BUDGET_LOCK = asyncio.Lock()


def _read_key() -> str:
    global _KEY_CACHE
    if _KEY_CACHE:
        return _KEY_CACHE
    if not config.GOOGLE_PLACES_KEY_FILE:
        raise RuntimeError("GOOGLE_PLACES_KEY_FILE not configured")
    raw = Path(config.GOOGLE_PLACES_KEY_FILE).read_text(encoding="utf-8")
    for line in raw.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export "):].strip()
        if "=" in line:
            name, _, value = line.partition("=")
            if name.strip() == "GOOGLE_PLACES_KEY":
                value = value.strip().strip('"').strip("'")
                if value:
                    _KEY_CACHE = value
                    return value
    text = raw.strip()
    if text and "\n" not in text and "=" not in text:
        _KEY_CACHE = text
        return text
    raise RuntimeError("Google Places key file missing GOOGLE_PLACES_KEY")


def _normalized(query: str) -> str:
    return re.sub(r"\s+", " ", query.strip().casefold())


async def _reserve_call() -> int | None:
    """Reserve one application-side call for the local calendar day."""
    global _BUDGET_DAY, _BUDGET_COUNT
    today = datetime.now(ZoneInfo(config.ASK_TIMEZONE)).date()
    async with _BUDGET_LOCK:
        if _BUDGET_DAY != today:
            _BUDGET_DAY, _BUDGET_COUNT = today, 0
        if _BUDGET_COUNT >= config.PLACES_DAILY_LIMIT:
            log.warning("Places client daily limit reached: %d/%d",
                        _BUDGET_COUNT, config.PLACES_DAILY_LIMIT)
            return None
        _BUDGET_COUNT += 1
        return _BUDGET_COUNT


async def _search(query: str) -> dict | None:
    key = _normalized(query)
    cached = _CACHE.get(key)
    if cached and cached[0] > time.monotonic():
        log.info("Places cache hit for %r", query)
        return cached[1]
    if not config.HOME_LAT or not config.HOME_LON:
        raise RuntimeError("HOME_LAT/HOME_LON not configured")
    call_number = await _reserve_call()
    if call_number is None:
        return None
    body = {
        "textQuery": query,
        "locationBias": {"circle": {
            "center": {"latitude": config.HOME_LAT,
                       "longitude": config.HOME_LON},
            "radius": config.PLACES_LOCATION_RADIUS_M,
        }},
        "maxResultCount": 1,
        "regionCode": "US",
    }
    headers = {
        "Content-Type": "application/json",
        "X-Goog-Api-Key": _read_key(),
        "X-Goog-FieldMask": _FIELD_MASK,
    }
    async with httpx.AsyncClient(timeout=8) as client:
        response = await client.post(_URL, json=body, headers=headers)
    if response.status_code in (403, 429):
        log.warning("Places request rejected status=%d call=%d/%d",
                    response.status_code, call_number, config.PLACES_DAILY_LIMIT)
        return None
    response.raise_for_status()
    places = response.json().get("places") or []
    if not places:
        log.info("Places found no result for %r", query)
        _CACHE[key] = (time.monotonic() + config.PLACES_CACHE_TTL_S, None)
        return None
    place = places[0]
    display_name = place.get("displayName", {}).get("text", "")
    score = fuzz.WRatio(key, _normalized(display_name)) if display_name else 0
    if score < _MATCH_THRESHOLD:
        # Text Search can aggressively spell-correct nonsense (for example,
        # "blorbcorp" -> "Labcorp"). Never turn that into a confident answer.
        log.info("Places rejected weak match %r -> %r score=%.0f",
                 query, display_name, score)
        _CACHE[key] = (time.monotonic() + config.PLACES_CACHE_TTL_S, None)
        return None
    _CACHE[key] = (time.monotonic() + config.PLACES_CACHE_TTL_S, place)
    log.info("Places lookup %r -> %s score=%.0f call=%d/%d", query,
             display_name, score, call_number, config.PLACES_DAILY_LIMIT)
    return place


def _timestamp(raw: str | None, tz: ZoneInfo) -> datetime | None:
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00")).astimezone(tz)
    except (TypeError, ValueError):
        return None


def _point_date(point: dict) -> date | None:
    value = point.get("date") or {}
    try:
        return date(int(value["year"]), int(value["month"]), int(value["day"]))
    except (KeyError, TypeError, ValueError):
        return None


def _google_day(value: date) -> int:
    """Places uses Sunday=0; Python uses Monday=0."""
    return (value.weekday() + 1) % 7


def _periods_for(hours: dict, target: date) -> list[dict]:
    periods = []
    for period in hours.get("periods") or []:
        opened = period.get("open") or {}
        opened_date = _point_date(opened)
        if opened_date == target or (
                opened_date is None and opened.get("day") == _google_day(target)):
            periods.append(period)
    return periods


def _point_datetime(point: dict, target: date, tz: ZoneInfo) -> datetime | None:
    actual_date = _point_date(point)
    if actual_date is None:
        day = point.get("day")
        if not isinstance(day, int):
            return None
        delta = (day - _google_day(target)) % 7
        actual_date = target + timedelta(days=delta)
    try:
        return datetime(actual_date.year, actual_date.month, actual_date.day,
                        int(point.get("hour", 0)), int(point.get("minute", 0)),
                        tzinfo=tz)
    except (TypeError, ValueError):
        return None


def _time(value: datetime) -> str:
    return value.strftime("%-I:%M %p").replace(":00 ", " ")


def _relative_day(target: date, today: date) -> str:
    if target == today:
        return "today"
    if target == today + timedelta(days=1):
        return "tomorrow"
    return target.strftime("%A")


def _at(value: datetime, today: date, *, tonight: bool = False) -> str:
    if value.date() == today:
        return f"at {_time(value)}" + (
            " tonight" if tonight and value.hour >= 17 else " today")
    return f"at {_time(value)} {_relative_day(value.date(), today)}"


def _is_24_hours(period: dict) -> bool:
    opened = period.get("open") or {}
    return (opened.get("hour", 0) == 0 and opened.get("minute", 0) == 0
            and not period.get("close"))


def _answer(place: dict, action: str, now: datetime | None = None) -> str | None:
    tz = ZoneInfo(config.ASK_TIMEZONE)
    now = now.astimezone(tz) if now else datetime.now(tz)
    today = now.date()
    name = place.get("displayName", {}).get("text")
    if not name:
        return None
    current = place.get("currentOpeningHours") or {}
    regular = place.get("regularOpeningHours") or {}
    hours = current or regular
    if not hours:
        return None

    if action == "now":
        if "openNow" not in current:
            return None
        if current["openNow"]:
            closing = _timestamp(current.get("nextCloseTime"), tz)
            if closing:
                day = "" if closing.date() == today else f" {_relative_day(closing.date(), today)}"
                return f"Yes, {name} is open until {_time(closing)}{day}."
            return f"Yes, {name} is open 24 hours."
        opening = _timestamp(current.get("nextOpenTime"), tz)
        if opening:
            day = _relative_day(opening.date(), today)
            suffix = " today" if day == "today" else f" {day}"
            return f"No, {name} is closed — it opens at {_time(opening)}{suffix}."
        return f"No, {name} is closed."

    periods = _periods_for(hours, today)
    if action in ("close", "today") and not periods:
        return f"{name} is closed all day today."
    if periods and any(_is_24_hours(period) for period in periods):
        return f"{name} is open 24 hours today."

    if action == "close":
        closes = [value for period in periods
                  if (value := _point_datetime(period.get("close") or {}, today, tz))]
        if not closes:
            return None
        closing = max(closes)
        return f"{name} closes {_at(closing, today, tonight=True)}."

    if action == "open":
        opens = [value for period in periods
                 if (value := _point_datetime(period.get("open") or {}, today, tz))]
        if current.get("openNow") and opens:
            return f"{name} opened at {_time(min(opens))} this morning."
        future = [value for value in opens if value > now]
        opening = min(future) if future else _timestamp(current.get("nextOpenTime"), tz)
        if opening:
            day = _relative_day(opening.date(), today)
            suffix = " today" if day == "today" else f" {day}"
            return f"{name} opens at {_time(opening)}{suffix}."
        if not periods:
            return f"{name} is closed all day today."
        return None

    # action == today (also the safe default)
    spans = []
    for period in periods:
        opened = _point_datetime(period.get("open") or {}, today, tz)
        closed = _point_datetime(period.get("close") or {}, today, tz)
        if opened and closed:
            end_day = "" if closed.date() == today else f" {_relative_day(closed.date(), today)}"
            spans.append(f"{_time(opened)} to {_time(closed)}{end_day}")
    if not spans:
        return None
    return f"{name} is open " + " and ".join(spans) + " today."


async def handle(parsed: dict) -> dict | None:
    """Answer a business-hours intent, or None for the ask fallback."""
    query = (parsed.get("query") or "").strip()
    if not query:
        return None
    place = await _search(query)
    if not place:
        return None
    action = parsed.get("hours_when") or "today"
    spoken = _answer(place, action)
    if not spoken:
        return None
    return {"response": spoken, "ok": True, "hours_when": action}
