"""The deterministic clock path: phrase matching and spoken rendering.

The point of the intent is that a time question never reaches a model, so the
tests that matter are the two ends of that promise — the phrases people
actually say all match, and the phrases that only LOOK like clock questions
("what time does Costco close") all miss and stay with the classifier.
"""

import unittest
from datetime import datetime
from zoneinfo import ZoneInfo

from . import clock, intent

# A Tuesday, so a weekday answer can't accidentally pass by matching the date.
NOW = datetime(2026, 1, 13, 15, 42, tzinfo=ZoneInfo("America/Denver"))


class FastPathTest(unittest.TestCase):
    def _kind(self, text: str) -> tuple[str, str] | None:
        parsed = intent.fast_parse(text)
        if parsed is None:
            return None
        self.assertEqual(parsed["intent"], "time_query")
        return parsed["time_kind"], parsed["time_day"]

    def test_time_phrasings(self):
        for text in ("what time is it", "What time is it?", "what's the time",
                     "whats the time", "what time is it right now",
                     "do you know what time it is", "tell me the time",
                     "hey what time is it", "can you tell me what time it is",
                     "what's the current time", "what time do you have"):
            with self.subTest(text=text):
                self.assertEqual(self._kind(text), ("time", "today"))

    def test_day_and_date_are_the_same_question(self):
        # Brad's framing: people say "day" and "date" interchangeably, so both
        # get the full "Tuesday, January 13, 2026".
        for text in ("what's the date", "what's today's date", "what day is it",
                     "what day is it today", "what's today", "what date is it",
                     "what is the day", "do you know what day it is",
                     "what's the date again"):
            with self.subTest(text=text):
                self.assertEqual(self._kind(text), ("date", "today"))

    def test_day_of_the_week_asks_for_only_the_weekday(self):
        for text in ("what day of the week is it",
                     "what's the day of the week",
                     "do you know what day of the week it is"):
            with self.subTest(text=text):
                self.assertEqual(self._kind(text), ("weekday", "today"))

    def test_other_days(self):
        for text in ("what's tomorrow's date", "what day is tomorrow",
                     "what's the date tomorrow"):
            with self.subTest(text=text):
                self.assertEqual(self._kind(text), ("date", "tomorrow"))
        for text in ("what was yesterday's date", "what day was yesterday",
                     "what date was yesterday"):
            with self.subTest(text=text):
                self.assertEqual(self._kind(text), ("date", "yesterday"))
        self.assertEqual(self._kind("what day of the week is tomorrow"),
                         ("weekday", "tomorrow"))

    def test_month_and_year(self):
        for text in ("what month is it", "what's the month", "what month is this"):
            with self.subTest(text=text):
                self.assertEqual(self._kind(text), ("month", "today"))
        for text in ("what year is it", "what year are we in", "what's the year"):
            with self.subTest(text=text):
                self.assertEqual(self._kind(text), ("year", "today"))

    def test_lookalikes_are_never_answered_from_the_clock(self):
        # Every one of these contains "what time"/"what day"/"what year" and
        # means something else. A partial match here would be a wrong answer.
        # ("what's the weather today" has its own fast path — it just must not
        # be this one.)
        for text in ("what time does costco close",
                     "what time is it in london",
                     "what time is it in new york right now",
                     "what time does the game start",
                     "how much time is left on the timer",
                     "what time should I put the chicken in",
                     "what day are we leaving",
                     "what day is the party",
                     "what day of the week does the trash go out",
                     "what's the weather today",
                     "what year did the beatles break up"):
            with self.subTest(text=text):
                self.assertIsNone(intent.fast_parse_clock(text))


class ValidationTest(unittest.TestCase):
    def test_unreadable_slots_fall_back_to_the_common_question(self):
        parsed = intent.validate({"intent": "time_query", "time_kind": "hour",
                                  "time_day": "next week"})
        self.assertEqual((parsed["time_kind"], parsed["time_day"]),
                         ("time", "today"))

    def test_slots_are_dropped_on_every_other_intent(self):
        parsed = intent.validate({"intent": "ask", "time_kind": "date",
                                  "time_day": "tomorrow"})
        self.assertIsNone(parsed["time_kind"])
        self.assertIsNone(parsed["time_day"])


class AnswerTest(unittest.TestCase):
    def _say(self, text: str) -> str:
        parsed = intent.fast_parse(text)
        self.assertIsNotNone(parsed, text)
        return clock.answer(parsed, now=NOW)

    def test_time(self):
        self.assertEqual(self._say("what time is it"), "It's 3:42 PM.")

    def test_on_the_hour_drops_the_minutes(self):
        on_hour = NOW.replace(minute=0)
        self.assertEqual(clock.answer({"time_kind": "time"}, now=on_hour),
                         "It's 3 PM.")

    def test_date(self):
        self.assertEqual(self._say("what day is it"),
                         "It's Tuesday, January 13, 2026.")

    def test_weekday(self):
        self.assertEqual(self._say("what day of the week is it"),
                         "It's Tuesday.")

    def test_other_days_get_their_own_frame(self):
        self.assertEqual(self._say("what's tomorrow's date"),
                         "Tomorrow is Wednesday, January 14, 2026.")
        self.assertEqual(self._say("what day was yesterday"),
                         "Yesterday was Monday, January 12, 2026.")
        self.assertEqual(self._say("what day of the week is tomorrow"),
                         "Tomorrow is Wednesday.")

    def test_month_and_year(self):
        self.assertEqual(self._say("what month is it"), "It's January.")
        self.assertEqual(self._say("what year is it"), "It's 2026.")

    def test_crossing_a_month_boundary(self):
        eve = NOW.replace(month=1, day=31)
        self.assertEqual(clock.answer({"time_kind": "date", "time_day": "tomorrow"},
                                      now=eve),
                         "Tomorrow is Sunday, February 1, 2026.")

    def test_handle_returns_a_speakable_result(self):
        result = clock.handle(intent.validate({"intent": "time_query"}))
        self.assertTrue(result["ok"])
        self.assertTrue(result["response"].startswith("It's "))


if __name__ == "__main__":
    unittest.main()
