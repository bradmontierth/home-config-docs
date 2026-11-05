"""Sunrise/sunset: the NOAA calc against known times, the fast path, and the
spoken answers. Reference times are HA's sun.sun (astral) for the house."""

import unittest
from datetime import date, datetime
from zoneinfo import ZoneInfo

from . import clock, intent, sun

TZ = ZoneInfo("America/Denver")
LAT, LON = 40.49425468871391, -112.01087070183728


def _at(d: date, kind: str) -> str:
    return sun.event(d, kind, TZ, LAT, LON).strftime("%H:%M")


class CalcTest(unittest.TestCase):
    def test_matches_ha_sun_entity(self):
        # HA 2026-10-25: sunset 18:33:08, sunrise 10-26 07:51:31.
        self.assertEqual(_at(date(2026, 10, 25), "sunset"), "18:33")
        self.assertIn(_at(date(2026, 10, 26), "sunrise"), ("07:51", "07:52"))

    def test_dst_and_winter(self):
        # MST in December, MDT in June; the evening event crosses UTC midnight.
        self.assertEqual(sun.event(date(2026, 12, 21), "sunset", TZ, LAT, LON).utcoffset().total_seconds(), -7 * 3600)
        self.assertTrue("17:00" <= _at(date(2026, 12, 21), "sunset") <= "17:10")
        self.assertTrue("20:55" <= _at(date(2026, 6, 21), "sunset") <= "21:05")


class FastPathTest(unittest.TestCase):
    def _hit(self, text):
        parsed = intent.fast_parse(text)
        if parsed is None:
            return None
        self.assertEqual(parsed["intent"], "time_query")
        return parsed["time_kind"], parsed["time_day"]

    def test_phrasings(self):
        cases = {
            "what time is the sunset": ("sunset", "today"),
            "What time is sunset?": ("sunset", "today"),
            "when is sunset tonight": ("sunset", "today"),
            "when does the sun go down": ("sunset", "today"),
            "what time does the sun set": ("sunset", "today"),
            "what's the sunset time": ("sunset", "today"),
            "sunset": ("sunset", "today"),
            "what time is sunrise tomorrow": ("sunrise", "tomorrow"),
            "when does the sun come up tomorrow": ("sunrise", "tomorrow"),
            "what's tomorrow's sunrise": ("sunrise", "tomorrow"),
            "when is sunrise": ("sunrise", "today"),
            "what time was sunset yesterday": ("sunset", "yesterday"),
        }
        for text, want in cases.items():
            with self.subTest(text=text):
                self.assertEqual(self._hit(text), want)

    def test_other_places_miss(self):
        for text in ("what time is sunset in hawaii", "when is sunset in maui",
                     "sunset boulevard"):
            with self.subTest(text=text):
                self.assertIsNone(sun.fast_parse(text))


class AnswerTest(unittest.TestCase):
    def setUp(self):
        self._lat, self._lon = sun.config.HOME_LAT, sun.config.HOME_LON
        sun.config.HOME_LAT, sun.config.HOME_LON = LAT, LON

    def tearDown(self):
        sun.config.HOME_LAT, sun.config.HOME_LON = self._lat, self._lon

    def _say(self, kind, day, now):
        return clock.answer({"time_kind": kind, "time_day": day}, now=now)

    def test_upcoming_today(self):
        now = datetime(2026, 10, 25, 15, 0, tzinfo=TZ)
        self.assertEqual(self._say("sunset", "today", now), "Sunset is at 6:33 PM.")

    def test_already_passed_adds_tomorrow(self):
        now = datetime(2026, 10, 25, 20, 0, tzinfo=TZ)
        self.assertRegex(self._say("sunset", "today", now),
                         r"^Sunset was at 6:33 PM today\. Tomorrow it's at 6:3\d PM\.$")

    def test_tomorrow(self):
        now = datetime(2026, 10, 25, 20, 0, tzinfo=TZ)
        self.assertRegex(self._say("sunrise", "tomorrow", now), r"^Sunrise tomorrow is at 7:5\d AM\.$")


if __name__ == "__main__":
    unittest.main()
