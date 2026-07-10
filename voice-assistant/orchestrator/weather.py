"""Weather intent — current conditions + forecast from Home Assistant.

Current temperature/humidity/wind come from the local weather-station sensors
(on-site, accurate); the condition word and the forecast come from the met.no
weather entity the kitchen dashboard already displays. Plain REST — a couple of
state GETs or one get_forecasts service call per question, ~200ms total, so no
filler audio is needed.

handle() returns None when it can't answer (HA down, day outside the 6-day
met.no window) — the app falls back to the ask path, same shape as sports.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx

from . import config

log = logging.getLogger("orchestrator.weather")

_TOKEN: str | None = None

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


async def handle(parsed: dict) -> dict | None:
    """Answer a weather_query intent, or None to fall back to ask (HA down,
    day out of the forecast window, unrecognized `when`)."""
    when = parsed.get("weather_when") or "now"
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            text = await _current(client) if when == "now" \
                else await _forecast_answer(client, when)
    except Exception as exc:  # noqa: BLE001 — never fatal; ask is the fallback
        log.warning("weather lookup failed (%s): %s", when, exc)
        return None
    if not text:
        return None
    return {"response": text, "ok": True, "weather_when": when}
