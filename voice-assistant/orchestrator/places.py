"""Fast nearby-place and business-hours answers from Google Places API (New).

One Text Search call returns nearby matching businesses, coordinates, and
current/regular hours. Results are cached until the next hours transition (at
most 24 hours), and an in-process daily counter stays below the Google Cloud
SearchText hard quota. Any configuration, resolution, quota, or API failure
returns None so app.py can use the slower ask path.
"""

from __future__ import annotations

import asyncio
import logging
import math
import re
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx
from rapidfuzz import fuzz

from . import config

log = logging.getLogger("orchestrator.places")

_URL = "https://places.googleapis.com/v1/places:searchText"
_FIELD_MASK = ("places.id,places.displayName,places.formattedAddress,places.location,"
               "places.regularOpeningHours,places.currentOpeningHours")
_KEY_CACHE: str | None = None
_CACHE: dict[str, tuple[float, list[dict] | None]] = {}
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


def _result_cache_ttl(raw_places: list[dict],
                      now: datetime | None = None) -> float:
    """Never cache openNow beyond Google's next open/close transition."""
    now = now or datetime.now(timezone.utc)
    transitions = []
    for place in raw_places:
        current = place.get("currentOpeningHours") or {}
        for field in ("nextOpenTime", "nextCloseTime"):
            raw = current.get(field)
            if not raw:
                continue
            try:
                transition = datetime.fromisoformat(raw.replace("Z", "+00:00"))
            except (TypeError, ValueError):
                continue
            seconds = (transition - now).total_seconds()
            if seconds > 0:
                transitions.append(seconds)
    if not transitions:
        return config.PLACES_CACHE_TTL_S
    # A small grace window prevents a request racing the exact minute a store
    # changes state while still making the next question refresh its openNow.
    return min(config.PLACES_CACHE_TTL_S, max(60.0, min(transitions) + 60.0))


def _canonical_name(value: str) -> str:
    """Normalize harmless storefront naming differences such as leading The."""
    normalized = _normalized(value)
    return normalized[4:] if normalized.startswith("the ") else normalized


def _name_tier(query: str, display_name: str) -> int:
    """Prefer the real store over same-address departments/service desks."""
    query_name = _canonical_name(query)
    candidate = _canonical_name(display_name)
    if candidate == query_name:
        return 0
    if candidate.startswith(query_name + " "):
        return 1
    return 2


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


async def _search(query: str) -> list[dict] | None:
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
        # Fetch Google's full first page once, then enforce the exact radius,
        # brand confidence, ordering, and eight-card display limit locally.
        "maxResultCount": 20,
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
    _CACHE[key] = (time.monotonic() + _result_cache_ttl(places), places)
    log.info("Places lookup %r -> %d raw result(s) call=%d/%d", query,
             len(places), call_number, config.PLACES_DAILY_LIMIT)
    return places


def _distance_m(home_lat: float, home_lon: float,
                place_lat: float, place_lon: float) -> float:
    """Great-circle distance, sufficient for local nearest-place ordering."""
    earth_m = 6_371_008.8
    phi1, phi2 = math.radians(home_lat), math.radians(place_lat)
    d_phi = math.radians(place_lat - home_lat)
    d_lambda = math.radians(place_lon - home_lon)
    value = (math.sin(d_phi / 2) ** 2
             + math.cos(phi1) * math.cos(phi2) * math.sin(d_lambda / 2) ** 2)
    return earth_m * 2 * math.atan2(math.sqrt(value), math.sqrt(1 - value))


def _matching_nearby(query: str, raw_places: list[dict]) -> list[tuple[dict, float]]:
    """Return strong name matches inside the configured circle, nearest first."""
    query_key = _normalized(query)
    candidates: list[tuple[dict, float, int]] = []
    weak: list[tuple[str, float]] = []
    for place in raw_places:
        name = place.get("displayName", {}).get("text", "")
        score = fuzz.WRatio(query_key, _normalized(name)) if name else 0
        if score < _MATCH_THRESHOLD:
            weak.append((name, score))
            continue
        location = place.get("location") or {}
        lat, lon = location.get("latitude"), location.get("longitude")
        if not isinstance(lat, (int, float)) or not isinstance(lon, (int, float)):
            continue
        distance = _distance_m(config.HOME_LAT, config.HOME_LON, lat, lon)
        if distance <= config.PLACES_LOCATION_RADIUS_M:
            candidates.append((place, distance, _name_tier(query, name)))
    if not candidates and weak:
        log.info("Places rejected weak matches for %r: %s", query, weak[:3])
    if not candidates:
        return []

    # If Google returned real chain storefronts, suppress departments such as
    # "Garden Center at The Home Depot" at the same coordinates. If a chain's
    # official display name is extended ("Chipotle Mexican Grill"), tier 1 is
    # still retained because it is the best available form.
    best_tier = min(item[2] for item in candidates)
    candidates = [item for item in candidates if item[2] == best_tier]
    candidates.sort(key=lambda item: item[1])
    nearby: list[tuple[dict, float]] = []
    seen: set[tuple] = set()
    for place, distance, _ in candidates:
        location = place.get("location") or {}
        identity = (
            _canonical_name(place.get("displayName", {}).get("text", "")),
            round(float(location.get("latitude")), 5),
            round(float(location.get("longitude")), 5),
        )
        if identity in seen:
            continue
        seen.add(identity)
        nearby.append((place, distance))
    return nearby[:config.PLACES_MAX_RESULTS]


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


def _schedule(place: dict) -> list[dict[str, str]]:
    """Normalize Google's locale-ready weekday descriptions for the kiosk."""
    regular = place.get("regularOpeningHours") or {}
    rows = []
    for description in regular.get("weekdayDescriptions") or []:
        day, separator, hours = description.partition(":")
        if separator:
            rows.append({"day": day.strip(), "hours": hours.strip()})
    return rows


def _status(place: dict, now: datetime) -> tuple[str, str]:
    current = place.get("currentOpeningHours") or {}
    tz = ZoneInfo(config.ASK_TIMEZONE)
    if current.get("openNow") is True:
        closing = _timestamp(current.get("nextCloseTime"), tz)
        return "Open", f"Until {_time(closing)}" if closing else "Open now"
    if current.get("openNow") is False:
        opening = _timestamp(current.get("nextOpenTime"), tz)
        if opening:
            relative = _relative_day(opening.date(), now.date())
            suffix = "" if relative == "today" else f" {relative}"
            return "Closed", f"Opens {_time(opening)}{suffix}"
        return "Closed", "Closed now"
    return "Hours unavailable", ""


def _public_place(place: dict, distance_m: float, index: int,
                  now: datetime) -> dict:
    location = place.get("location") or {}
    status, status_detail = _status(place, now)
    current = place.get("currentOpeningHours") or {}
    return {
        "id": place.get("id") or f"place-{index}",
        "number": index + 1,
        "name": place.get("displayName", {}).get("text") or "Location",
        "address": place.get("formattedAddress") or "Address unavailable",
        "latitude": location.get("latitude"),
        "longitude": location.get("longitude"),
        "distance_miles": round(distance_m / 1609.344, 1),
        "distance_kind": "straight_line",
        "status": status,
        "status_detail": status_detail,
        "special_hours": bool(current.get("specialDays")),
        "schedule": _schedule(place),
    }


def _location_answer(query: str, places: list[dict]) -> str:
    count = len(places)
    if not count:
        return f"I couldn't find any {query} locations within 10 miles."
    closest = places[0]
    miles = closest["distance_miles"]
    distance = "less than a tenth of a mile" if miles == 0 else f"{miles:g} miles"
    street = closest["address"].split(",", 1)[0]
    if count == 1:
        return f"I found one {query} within 10 miles. It's {distance} away, at {street}."
    return (f"I found {count} {query} locations within 10 miles. "
            f"The closest is {distance} away, at {street}.")


async def handle(parsed: dict) -> dict | None:
    """Answer a nearby-place intent and return its dashboard evidence payload."""
    query = (parsed.get("query") or "").strip()
    if not query:
        return None
    raw_places = await _search(query)
    if not raw_places:
        return None

    nearby = _matching_nearby(query, raw_places)
    # Text Search can aggressively spell-correct nonsense (for example,
    # "blorbcorp" -> "Labcorp"). Never turn that into a confident answer.
    strong_match = any(
        fuzz.WRatio(
            _normalized(query),
            _normalized(place.get("displayName", {}).get("text", "")),
        ) >= _MATCH_THRESHOLD
        for place in raw_places
    )
    if not nearby and not strong_match:
        return None

    now = datetime.now(ZoneInfo(config.ASK_TIMEZONE))
    public_places = [
        _public_place(place, distance, index, now)
        for index, (place, distance) in enumerate(nearby)
    ]
    action = parsed.get("hours_when") or "location"
    if parsed.get("intent") == "business_hours":
        if not nearby:
            spoken = f"I couldn't find any {query} locations within 10 miles."
        else:
            spoken = _answer(nearby[0][0], action)
            if not spoken:
                return None
    else:
        spoken = _location_answer(query, public_places)

    radius_miles = round(config.PLACES_LOCATION_RADIUS_M / 1609.344, 1)
    view = {
        "query": query,
        "action": action,
        "summary": spoken,
        "radius_miles": radius_miles,
        "home": {"latitude": config.HOME_LAT, "longitude": config.HOME_LON},
        "places": public_places,
        "selected_id": public_places[0]["id"] if public_places else None,
        "updated_at": now.isoformat(),
    }
    return {
        "response": spoken,
        "ok": True,
        "hours_when": action,
        "places_view": view,
    }
