"""Local-vs-named weather routing and OpenWeather answer formatting."""

import asyncio
import unittest
from datetime import datetime
from unittest.mock import AsyncMock, patch
from zoneinfo import ZoneInfo

from . import intent, weather


class WeatherRoutingTest(unittest.TestCase):
    def setUp(self):
        weather._GEOCODE_CACHE.clear()
        weather._FORECAST_CACHE.clear()

    def test_named_location_uses_remote_provider_not_home_sensors(self):
        parsed = intent.validate({
            "intent": "weather", "weather_when": "today",
            "weather_location": "park city",
        })
        answer = ("In Park City, Utah, today will be clear.", "Park City, Utah")
        with patch.object(weather, "_remote_answer",
                          new=AsyncMock(return_value=answer)) as remote, \
             patch.object(weather, "_current", new=AsyncMock()) as local:
            result = asyncio.run(weather.handle(
                parsed, "what's the weather in Park City today"))
        remote.assert_awaited_once()
        local.assert_not_awaited()
        self.assertEqual(result["weather_location"], "Park City, Utah")
        self.assertIn("Park City, Utah", result["response"])

    def test_dropped_named_slot_can_never_return_home_weather(self):
        parsed = intent.validate({"intent": "weather", "weather_when": "today"})
        with patch.object(weather, "_current", new=AsyncMock()) as current, \
             patch.object(weather, "_forecast_answer", new=AsyncMock()) as forecast:
            result = asyncio.run(weather.handle(
                parsed, "what's the weather in Park City today"))
        self.assertIsNone(result)
        current.assert_not_awaited()
        forecast.assert_not_awaited()

    def test_local_weather_still_uses_home_assistant(self):
        parsed = intent.validate({"intent": "weather", "weather_when": "now"})
        with patch.object(weather, "_current",
                          new=AsyncMock(return_value="It's 72 and sunny.")) as current, \
             patch.object(weather, "_remote_answer", new=AsyncMock()) as remote:
            result = asyncio.run(weather.handle(parsed, "what's the weather"))
        current.assert_awaited_once()
        remote.assert_not_awaited()
        self.assertNotIn("weather_location", result)


class OpenWeatherFormattingTest(unittest.TestCase):
    def test_current_answer_names_place_and_uses_imperial_values(self):
        place = {"name": "Park City", "state": "Utah", "country": "US"}
        forecast = {"current": {
            "temp": 63.6, "wind_speed": 12.2,
            "weather": [{"description": "light rain"}],
        }}
        answer = weather._remote_current(place, forecast)
        self.assertEqual(
            answer,
            "In Park City, Utah, it's 64 and light rain right now. "
            "Wind's at 12 miles an hour.",
        )

    def test_daily_answer_uses_the_locations_timezone(self):
        tz = ZoneInfo("America/Denver")
        today = datetime.now(tz).replace(hour=12, minute=0, second=0, microsecond=0)
        place = {"name": "Park City", "state": "Utah", "country": "US"}
        forecast = {
            "timezone": "America/Denver",
            "daily": [{
                "dt": today.timestamp(),
                "temp": {"max": 70.4, "min": 45.4},
                "pop": 0.62,
                "rain": 0.2,
                "weather": [{"description": "scattered clouds"}],
            }],
        }
        answer = weather._remote_forecast_answer(place, forecast, "today")
        self.assertEqual(
            answer,
            "In Park City, Utah, today will be scattered clouds with a high "
            "of 70 and a low of 45. There's a 62 percent chance of rain.",
        )


if __name__ == "__main__":
    unittest.main()
