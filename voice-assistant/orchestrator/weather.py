"""Weather intent — home conditions from HA, named places from OpenWeather.

Current temperature/humidity/wind come from the local weather-station sensors
(on-site, accurate); the condition word and the forecast come from the met.no
weather entity the kitchen dashboard already displays. Plain REST — a couple of
state GETs or one get_forecasts service call per question, ~200ms total, so no
filler audio is needed.

Named locations are geocoded and fetched on demand from OpenWeatherMap One Call
3.0. Geocodes are cached for a month and forecasts for ten minutes; there is no
poller or pre-cache quota use. handle() returns None when either source cannot
answer, and app.py falls back to the ask path, same shape as sports.
"""

from __future__ import annotations

import logging
import re
import time
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx

from . import config

log = logging.getLogger("orchestrator.weather")

_TOKEN: str | None = None
_OPENWEATHER_KEY: str | None = None
_GEOCODE_CACHE: dict[str, tuple[float, dict | None]] = {}
_FORECAST_CACHE: dict[tuple[float, float], tuple[float, dict | None]] = {}

DAY_NAMES = ("monday", "tuesday", "wednesday", "thursday", "friday",
             "saturday", "sunday")

# met.no condition slugs -> spoken words.
CONDITIONS = {
    "sunny": "sunny",
    "clear-night": "clear",
    "partlycloudy": "partly cloudy",
    "cloudy": "cloudy",
    "rainy": "rainy",
    "pouring": "pouring rain",
    "lightning": "stormy",
    "lightning-rainy": "stormy with rain",
    "snowy": "snowy",
    "snowy-rainy": "a wintry mix",
    "fog": "foggy",
    "windy": "windy",
    "windy-variant": "windy",
    "hail": "hailing",
}
_RAINY = {"rainy", "pouring", "lightning-rainy", "snowy-rainy", "hail"}
_LOCAL_WHEN = {"now", "today", "tonight", "tomorrow", *DAY_NAMES}
_NAMED_WEATHER_HINT = re.compile(
    r"\b(?:weather|forecast|temperature)\b.*\b(?:in|for)\s+(.+)$|"
    r"\bwill\s+it\s+(?:rain|snow)\s+in\s+(.+)$", re.IGNORECASE)


def _token() -> str:
    """HA long-lived token from HA_TOKEN_FILE (raw, or a dotenv HA_TOKEN=…
    line) — same file-mount convention as the OpenRouter key."""
    global _TOKEN
    if _TOKEN is None:
        raw = Path(config.HA_TOKEN_FILE).read_text().strip()
        for line in raw.splitlines():
            if line.startswith("HA_TOKEN="):
                raw = line.split("=", 1)[1].strip()
                break
        _TOKEN = raw
    return _TOKEN


def _openweather_key() -> str:
    global _OPENWEATHER_KEY
    if _OPENWEATHER_KEY is None:
        if not config.OPENWEATHER_KEY_FILE:
            raise RuntimeError("OPENWEATHER_KEY_FILE not configured")
        raw = Path(config.OPENWEATHER_KEY_FILE).read_text().strip()
        for line in raw.splitlines():
            if line.startswith(("OPENWEATHER_KEY=", "OPENWEATHER_API_KEY=", "APPID=")):
                raw = line.split("=", 1)[1].strip()
                break
        if not raw:
            raise RuntimeError("OpenWeatherMap key file is empty")
        _OPENWEATHER_KEY = raw
    return _OPENWEATHER_KEY


async def _state(client: httpx.AsyncClient, entity: str) -> dict:
    r = await client.get(f"{config.HA_URL}/api/states/{entity}",
                         headers={"Authorization": f"Bearer {_token()}"})
    r.raise_for_status()
    return r.json()


async def _daily_forecast(client: httpx.AsyncClient) -> list[dict]:
    r = await client.post(
        f"{config.HA_URL}/api/services/weather/get_forecasts",
        params={"return_response": "true"},
        headers={"Authorization": f"Bearer {_token()}"},
        json={"entity_id": config.WEATHER_ENTITY, "type": "daily"})
    r.raise_for_status()
    block = r.json().get("service_response", {}).get(config.WEATHER_ENTITY, {})
    return block.get("forecast") or []


def _spoken_condition(slug: str | None) -> str:
    if not slug:
        return "clear"
    return CONDITIONS.get(slug, slug.replace("-", " "))


async def _current(client: httpx.AsyncClient) -> str:
    temp = await _state(client, config.OUTDOOR_TEMP_ENTITY)
    weather = await _state(client, config.WEATHER_ENTITY)
    cond = _spoken_condition(weather.get("state"))
    text = f"It's {round(float(temp['state']))} and {cond} outside right now."
    try:
        wind = float((await _state(client, config.WIND_SPEED_ENTITY))["state"])
        if wind >= 10:
            text += f" Wind's at {round(wind)} miles an hour."
    except Exception:  # noqa: BLE001 — wind is garnish, never fail the answer
        pass
    return text


def _entry_phrase(day_word: str, entry: dict) -> str:
    cond = _spoken_condition(entry.get("condition"))
    high = entry.get("temperature")
    low = entry.get("templow")
    text = f"{day_word} will be {cond}"
    if high is not None:
        text += f" with a high of {round(high)}"
        if low is not None:
            text += f" and a low of {round(low)}"
    text += "."
    # met.no gives precipitation AMOUNT (inches), not probability.
    if (entry.get("precipitation") or 0) >= 0.05 and entry.get("condition") not in _RAINY:
        text += " Some rain is possible."
    return text


def _cache_get(cache: dict, key: object) -> dict | None | Ellipsis:
    cached = cache.get(key)
    if cached and cached[0] > time.monotonic():
        return cached[1]
    if cached:
        cache.pop(key, None)
    return Ellipsis


async def _geocode(client: httpx.AsyncClient, location: str) -> dict | None:
    key = " ".join(location.casefold().split())
    cached = _cache_get(_GEOCODE_CACHE, key)
    if cached is not Ellipsis:
        log.info("OpenWeather geocode cache hit for %r", location)
        return cached
    response = await client.get(config.OPENWEATHER_GEOCODE_URL, params={
        "q": location, "limit": 5, "appid": _openweather_key(),
    })
    response.raise_for_status()
    places = response.json() or []
    selected = places[0] if places else None
    _GEOCODE_CACHE[key] = (
        time.monotonic() + config.OPENWEATHER_GEOCODE_TTL_S, selected)
    if selected:
        log.info("OpenWeather geocode %r -> %s, %s, %s",
                 location, selected.get("name"), selected.get("state"),
                 selected.get("country"))
    else:
        log.info("OpenWeather geocode found no result for %r", location)
    return selected


async def _onecall(client: httpx.AsyncClient, place: dict) -> dict | None:
    lat, lon = float(place["lat"]), float(place["lon"])
    key = (round(lat, 4), round(lon, 4))
    cached = _cache_get(_FORECAST_CACHE, key)
    if cached is not Ellipsis:
        log.info("OpenWeather forecast cache hit for %s", key)
        return cached
    response = await client.get(config.OPENWEATHER_ONECALL_URL, params={
        "lat": lat, "lon": lon, "units": "imperial",
        "exclude": "minutely,alerts", "appid": _openweather_key(),
    })
    response.raise_for_status()
    forecast = response.json()
    _FORECAST_CACHE[key] = (
        time.monotonic() + config.OPENWEATHER_FORECAST_TTL_S, forecast)
    return forecast


def _remote_place_name(place: dict) -> str:
    name = place.get("name") or "that location"
    state, country = place.get("state"), place.get("country")
    if state:
        return f"{name}, {state}"
    if country and country != "US":
        return f"{name}, {country}"
    return name


def _remote_condition(entry: dict) -> str:
    weather = entry.get("weather") or []
    if weather and isinstance(weather[0], dict):
        return weather[0].get("description") or "clear"
    return "clear"


def _remote_current(place: dict, forecast: dict) -> str | None:
    current = forecast.get("current") or {}
    temp = current.get("temp")
    if temp is None:
        return None
    location = _remote_place_name(place)
    text = (f"In {location}, it's {round(temp)} and "
            f"{_remote_condition(current)} right now.")
    wind = current.get("wind_speed")
    if isinstance(wind, (int, float)) and wind >= 10:
        text += f" Wind's at {round(wind)} miles an hour."
    return text


def _remote_entry(place: dict, day_word: str, entry: dict,
                  *, tonight: bool = False) -> str:
    location = _remote_place_name(place)
    condition = _remote_condition(entry)
    temps = entry.get("temp") or {}
    if tonight:
        low = temps.get("night", temps.get("min"))
        text = f"In {location}, tonight will be {condition}"
        if low is not None:
            text += f" with a low of {round(low)}"
    else:
        high, low = temps.get("max"), temps.get("min")
        text = f"In {location}, {day_word} will be {condition}"
        if high is not None:
            text += f" with a high of {round(high)}"
            if low is not None:
                text += f" and a low of {round(low)}"
    text += "."
    probability = entry.get("pop")
    if isinstance(probability, (int, float)) and probability >= 0.15:
        kind = "snow" if entry.get("snow") else "rain"
        text += f" There's a {round(probability * 100)} percent chance of {kind}."
    return text


def _remote_forecast_answer(place: dict, forecast: dict,
                            when: str) -> str | None:
    try:
        tz = ZoneInfo(forecast.get("timezone") or "UTC")
    except Exception:  # noqa: BLE001 — malformed provider timezone
        tz = ZoneInfo("UTC")
    today = datetime.now(tz).date()
    by_date: dict = {}
    for entry in forecast.get("daily") or []:
        try:
            day = datetime.fromtimestamp(float(entry["dt"]), tz).date()
        except (KeyError, TypeError, ValueError, OSError):
            continue
        by_date.setdefault(day, entry)

    if when == "today":
        entry = by_date.get(today)
        return _remote_entry(place, "today", entry) if entry else None
    if when == "tonight":
        entry = by_date.get(today)
        return _remote_entry(place, "tonight", entry, tonight=True) if entry else None
    if when == "tomorrow":
        entry = by_date.get(today + timedelta(days=1))
        return _remote_entry(place, "tomorrow", entry) if entry else None
    if when in DAY_NAMES:
        for offset in range(8):
            day = today + timedelta(days=offset)
            if DAY_NAMES[day.weekday()] == when:
                entry = by_date.get(day)
                return (_remote_entry(place, when.capitalize(), entry)
                        if entry else None)
    return None


def _looks_like_named_weather(command: str | None) -> bool:
    if not command:
        return False
    match = _NAMED_WEATHER_HINT.search(command.strip().rstrip(".?!"))
    if not match:
        return False
    tail = next((group for group in match.groups() if group), "")
    return tail.strip().casefold() not in _LOCAL_WHEN


async def _remote_answer(client: httpx.AsyncClient, location: str,
                         when: str) -> tuple[str, str] | None:
    place = await _geocode(client, location)
    if not place:
        return None
    forecast = await _onecall(client, place)
    if not forecast:
        return None
    text = (_remote_current(place, forecast) if when == "now"
            else _remote_forecast_answer(place, forecast, when))
    return (text, _remote_place_name(place)) if text else None


async def _forecast_answer(client: httpx.AsyncClient, when: str) -> str | None:
    tz = ZoneInfo(config.ASK_TIMEZONE)
    today = datetime.now(tz).date()
    by_date = {}
    for entry in await _daily_forecast(client):
        try:
            d = datetime.fromisoformat(entry["datetime"]).astimezone(tz).date()
        except Exception:  # noqa: BLE001
            continue
        by_date.setdefault(d, entry)

    if when == "today":
        entry = by_date.get(today)
        return _entry_phrase("Today", entry) if entry else None
    if when == "tonight":
        entry = by_date.get(today)
        if not entry:
            return None
        low = entry.get("templow")
        cond = _spoken_condition(entry.get("condition"))
        return (f"Tonight will be {cond} with a low of {round(low)}." if low is not None
                else f"Tonight will be {cond}.")
    if when == "tomorrow":
        entry = by_date.get(today + timedelta(days=1))
        return _entry_phrase("Tomorrow", entry) if entry else None
    if when in DAY_NAMES:
        for offset in range(7):   # today counts: "saturday" asked on saturday
            d = today + timedelta(days=offset)
            if DAY_NAMES[d.weekday()] == when:
                entry = by_date.get(d)
                return _entry_phrase(when.capitalize(), entry) if entry else None
    return None


async def handle(parsed: dict, command: str | None = None) -> dict | None:
    """Answer a weather_query intent, or None to fall back to ask (HA down,
    day out of the forecast window, unrecognized `when`)."""
    when = parsed.get("weather_when") or "now"
    location = parsed.get("weather_location")
    if not location and _looks_like_named_weather(command):
        # A classifier that drops the named place must never silently answer
        # with the home sensors. Returning None invokes the smart ask fallback.
        log.warning("refusing local weather for named-location command: %r", command)
        return None
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            if location:
                remote = await _remote_answer(client, location, when)
                if not remote:
                    return None
                text, resolved_location = remote
            else:
                text = await _current(client) if when == "now" \
                    else await _forecast_answer(client, when)
                resolved_location = None
    except Exception as exc:  # noqa: BLE001 — never fatal; ask is the fallback
        log.warning("weather lookup failed (%s): %s", when, exc)
        return None
    if not text:
        return None
    result = {"response": text, "ok": True, "weather_when": when}
    if resolved_location:
        result["weather_location"] = resolved_location
    return result
