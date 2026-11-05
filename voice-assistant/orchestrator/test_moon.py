"""Moonrise/moonset/phase: the calc against reference times, the fast path,
and the spoken answers. Reference rise/set times are PyEphem 4.x for the house
with the USNO convention (upper limb, -34' horizon, no extra refraction) — an
independent ephemeris that agreed with astronomy-engine to the second."""

import unittest
from datetime import date, datetime

from zoneinfo import ZoneInfo

from . import clock, intent, moon

TZ = ZoneInfo("America/Denver")
LAT, LON = 40.49425468871391, -112.01087070183728


def _at(d: date, kind: str) -> str:
    return moon.event(d, kind, TZ, LAT, LON).strftime("%H:%M")


class CalcTest(unittest.TestCase):
    def test_matches_reference(self):
        cases = [  # (day, kind, local HH:MM rounded)
            (date(2026, 11, 4), "moonrise", "02:15"), (date(2026, 11, 4), "moonset", "15:00"),
            (date(2026, 11, 20), "moonrise", "14:33"), (date(2026, 11, 20), "moonset", "02:45"),
            (date(2026, 12, 24), "moonrise", "17:56"), (date(2026, 12, 24), "moonset", "08:44"),
            (date(2027, 3, 10), "moonrise", "07:33"), (date(2027, 3, 10), "moonset", "21:19"),
            (date(2027, 6, 18), "moonrise", "21:18"), (date(2027, 6, 18), "moonset", "05:16"),
        ]
        for d, kind, want in cases:
            with self.subTest(d=d, kind=kind):
                self.assertEqual(_at(d, kind), want)

    def test_some_day_each_month_has_no_moonrise(self):
        missing = [d for d in (date(2026, 11, 1 + i) for i in range(30))
                   if moon.event(d, "moonrise", TZ, LAT, LON) is None]
        # Nov 2 (rose 23:54 on the 1st, next 00:5x on the 3rd) and Nov 30.
        self.assertEqual(missing, [date(2026, 11, 2), date(2026, 11, 30)])

    def test_phase_and_quarters(self):
        name, lit = moon.phase(datetime(2026, 11, 4, 12, 0, tzinfo=TZ))
        self.assertEqual(name, "a waning crescent")
        self.assertTrue(15 <= lit <= 30)
        full = moon.next_quarter(datetime(2026, 11, 4, tzinfo=TZ), 2)
        self.assertEqual(full.strftime("%m-%d %H:%M"), "11-24 07:54")  # ephem 07:53:29; within a minute


class FastPathTest(unittest.TestCase):
    def _hit(self, text):
        parsed = intent.fast_parse(text)
        if parsed is None:
            return None
        self.assertEqual(parsed["intent"], "time_query")
        return parsed["time_kind"], parsed["time_day"]

    def test_phrasings(self):
        cases = {
            "when does the moon rise": ("moonrise", "today"),
            "What time is moonrise?": ("moonrise", "today"),
            "what time is the moon rise tonight": ("moonrise", "today"),
            "when's moonrise tomorrow": ("moonrise", "tomorrow"),
            "when will the moon come up": ("moonrise", "today"),
            "what time does the moon set": ("moonset", "today"),
            "when does the moon go down tomorrow": ("moonset", "tomorrow"),
            "what's tonight's moonrise": ("moonrise", "today"),
            "moonset": ("moonset", "today"),
            "what phase is the moon": ("moon_phase", "today"),
            "what's the phase of the moon tonight": ("moon_phase", "today"),
            "is it a full moon tonight": ("moon_phase", "today"),
            "when is the next full moon": ("full_moon", "today"),
            "when's the full moon": ("full_moon", "today"),
        }
        for text, want in cases.items():
            with self.subTest(text=text):
                self.assertEqual(self._hit(text), want)

    def test_sun_still_wins_and_elsewhere_misses(self):
        self.assertEqual(self._hit("when is sunset"), ("sunset", "today"))
        for text in ("when is moonrise in tokyo", "fly me to the moon",
                     "when did we land on the moon"):
            with self.subTest(text=text):
                self.assertIsNone(moon.fast_parse(text))


class AnswerTest(unittest.TestCase):
    def setUp(self):
        self._lat, self._lon = moon.config.HOME_LAT, moon.config.HOME_LON
        moon.config.HOME_LAT, moon.config.HOME_LON = LAT, LON

    def tearDown(self):
        moon.config.HOME_LAT, moon.config.HOME_LON = self._lat, self._lon

    def _say(self, kind, day, now):
        return clock.answer({"time_kind": kind, "time_day": day}, now=now)

    def test_upcoming_today(self):
        now = datetime(2026, 11, 20, 9, 0, tzinfo=TZ)
        self.assertRegex(self._say("moonrise", "today", now),
                         r"^Moonrise is at 2:33 PM\. It's a waxing gibbous, \d+ percent lit\.$")

    def test_passed_today_gives_next(self):
        now = datetime(2026, 11, 4, 18, 0, tzinfo=TZ)
        self.assertRegex(self._say("moonrise", "today", now),
                         r"^Moonrise was at 2:15 AM today\. Next moonrise is at 3:\d\d AM tomorrow\. "
                         r"It's a waning crescent, \d+ percent lit\.$")

    def test_no_event_today(self):
        missing = next(d for d in (date(2026, 11, 1 + i) for i in range(30))
                       if moon.event(d, "moonrise", TZ, LAT, LON) is None)
        now = datetime(missing.year, missing.month, missing.day, 8, 0, tzinfo=TZ)
        self.assertRegex(self._say("moonrise", "today", now),
                         r"^The moon doesn't rise today\. Next moonrise is at \d+:\d\d AM tomorrow\. ")

    def test_full_moon(self):
        self.assertEqual(self._say("full_moon", "today", datetime(2026, 11, 4, 12, tzinfo=TZ)),
                         "The next full moon is Tuesday, November 24.")
        self.assertEqual(self._say("full_moon", "today", datetime(2026, 11, 24, 20, tzinfo=TZ)),
                         "Today's the full moon. It was exactly full at 7:54 AM.")

    def test_phase(self):
        self.assertRegex(self._say("moon_phase", "today", datetime(2026, 11, 4, 12, tzinfo=TZ)),
                         r"^It's a waning crescent, \d+ percent lit\. The next new moon is on Monday\.$")


if __name__ == "__main__":
    unittest.main()
