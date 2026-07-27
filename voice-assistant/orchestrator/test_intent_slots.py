"""Deterministic halves of the timer slot-fill path.

The LLM classifier can't be unit-tested, but the two pieces that back it up
can: the truncated-timer rescue (which fires when the classifier says
"unclear") and the spoken-duration reader (which lets the common answer skip
the classifier entirely). Both exist because of a live failure on 2026-07-26 —
"set the timer for" was answered with "Sorry, I didn't catch that", and the
"Eight minutes." that followed was dropped as background chatter.
"""

import unittest

from . import intent


class TruncatedTimerTest(unittest.TestCase):
    """Timer commands that stop before their duration."""

    def test_catches_the_live_failure(self):
        # The exact transcript from the 23:55 turn that started all this.
        self.assertTrue(intent.is_truncated_timer("set the timer for"))

    def test_common_phrasings(self):
        for text in ("set a timer", "set a timer for", "set the timer",
                     "start a timer", "start a timer for", "set timer",
                     "make a timer", "put on a timer", "set an timer",
                     "please set a timer for", "can you set a timer",
                     "could you start a timer for"):
            with self.subTest(text=text):
                self.assertTrue(intent.is_truncated_timer(text))

    def test_labelled_timers(self):
        for text in ("set a chicken timer", "set a chicken timer for",
                     "start the rice timer for", "set a hard boiled egg timer"):
            with self.subTest(text=text):
                self.assertTrue(intent.is_truncated_timer(text))

    def test_punctuation_and_case(self):
        for text in ("Set a timer.", "SET THE TIMER FOR", "set a timer!",
                     "  set a timer for  "):
            with self.subTest(text=text):
                self.assertTrue(intent.is_truncated_timer(text))

    def test_complete_commands_are_left_alone(self):
        # Anything with a duration must take the normal path — the rescue only
        # exists for commands that genuinely have no duration in them.
        for text in ("set a timer for eight minutes", "set a timer for 8 minutes",
                     "set a chicken timer for 12 minutes",
                     "set a timer for an hour"):
            with self.subTest(text=text):
                self.assertFalse(intent.is_truncated_timer(text))

    def test_other_commands_are_left_alone(self):
        for text in ("cancel the timer", "how long on the timer",
                     "what timers do I have", "set the mood for dinner",
                     "set a reminder", "add a timer to the shopping list",
                     "start the music", ""):
            with self.subTest(text=text):
                self.assertFalse(intent.is_truncated_timer(text))


class SpokenDurationTest(unittest.TestCase):
    """The fast path for "for how long?" answers."""

    def test_the_live_answer(self):
        self.assertEqual(intent.spoken_duration("Eight minutes."), 480)

    def test_word_numbers(self):
        cases = {
            "five minutes": 300, "twenty minutes": 1200,
            "forty five minutes": 2700, "twenty five minutes": 1500,
            "ninety seconds": 90, "one hour": 3600, "two hours": 7200,
            "a minute": 60, "an hour": 3600,
        }
        for text, want in cases.items():
            with self.subTest(text=text):
                self.assertEqual(intent.spoken_duration(text), want)

    def test_digits(self):
        cases = {"8 minutes": 480, "45 min": 2700, "90 seconds": 90,
                 "3 hrs": 10800, "10 mins": 600, "30 secs": 30}
        for text, want in cases.items():
            with self.subTest(text=text):
                self.assertEqual(intent.spoken_duration(text), want)

    def test_halves(self):
        # "half an hour" is 30 minutes: the "an" is an article, not a count.
        cases = {
            "half an hour": 1800, "an hour and a half": 5400,
            "two and a half minutes": 150, "a minute and a half": 90,
            "half a minute": 30, "one and a half hours": 5400,
        }
        for text, want in cases.items():
            with self.subTest(text=text):
                self.assertEqual(intent.spoken_duration(text), want)

    def test_compound(self):
        cases = {"one hour ten minutes": 4200,
                 "an hour and twenty minutes": 4800,
                 "2 minutes 30 seconds": 150}
        for text, want in cases.items():
            with self.subTest(text=text):
                self.assertEqual(intent.spoken_duration(text), want)

    def test_conversational_padding(self):
        cases = {"about twenty minutes": 1200, "um, ten minutes": 600,
                 "make it five minutes": 300, "for 8 minutes": 480,
                 "maybe around 20 minutes": 1200}
        for text, want in cases.items():
            with self.subTest(text=text):
                self.assertEqual(intent.spoken_duration(text), want)

    def test_bare_number_declines(self):
        # No unit means the unit would be a guess. The parser sees the original
        # command too, so it is better placed to decide — hand it over.
        for text in ("eight", "20", "twenty five"):
            with self.subTest(text=text):
                self.assertIsNone(intent.spoken_duration(text))

    def test_non_durations_decline(self):
        # Everything here must fall through to the LLM rather than be
        # misread as a duration.
        for text in ("never mind", "actually what's the weather",
                     "for the chicken", "hang on", "play some music",
                     "eight minutes for the chicken", "", "   ",
                     "no thanks", "until the pasta is done"):
            with self.subTest(text=text):
                self.assertIsNone(intent.spoken_duration(text))


if __name__ == "__main__":
    unittest.main()
