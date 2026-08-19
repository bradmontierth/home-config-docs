"""Deterministic halves of the timer slot-fill path.

The LLM classifier can't be unit-tested, but the two pieces that back it up
can: the truncated-timer rescue (which fires when the classifier says
"unclear") and the spoken-duration reader (which lets the common answer skip
the classifier entirely). Both exist because of a live failure on 2026-07-26 —
"set the timer for" was answered with "Sorry, I didn't catch that", and the
"Eight minutes." that followed was dropped as background chatter.
"""

import asyncio
import unittest
from unittest.mock import AsyncMock, patch

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


class TruncatedAddTest(unittest.TestCase):
    """Add/reminder commands that stop before naming what to add."""

    def test_catches_the_live_failure(self):
        # Adrienne, 2026-07-27: "remind me to", then a pause to compose it.
        self.assertEqual(intent.is_truncated_add("remind me to"), "set_reminder")

    def test_reminder_phrasings(self):
        for text in ("remind me", "remind me to", "remind me that",
                     "set a reminder", "set a reminder to", "set a reminder for",
                     "set me a reminder", "add a reminder", "make a reminder",
                     "create a reminder to", "set reminder",
                     "can you remind me to", "please remind me"):
            with self.subTest(text=text):
                self.assertEqual(intent.is_truncated_add(text), "set_reminder")

    def test_list_phrasings(self):
        for text in ("add to my to-do list", "add to the shopping list",
                     "add something to my todo list", "put on the shopping list",
                     "add to the list", "add a todo", "add a to-do",
                     "add an item to my list", "add a new todo",
                     "put something on the grocery list"):
            with self.subTest(text=text):
                self.assertEqual(intent.is_truncated_add(text), "add_items")

    def test_punctuation_and_case(self):
        for text in ("Remind me to.", "REMIND ME TO", "  remind me to  ",
                     "Add to my to-do list!"):
            with self.subTest(text=text):
                self.assertIsNotNone(intent.is_truncated_add(text))

    def test_commands_that_name_something_are_left_alone(self):
        # The rescue exists only for commands with no content in them at all.
        for text in ("remind me to call mom", "remind me to take the roast out at 5",
                     "add eggs to the shopping list", "add milk",
                     "put paper towels on the list", "add a todo to call the plumber",
                     "set a reminder to water the plants"):
            with self.subTest(text=text):
                self.assertIsNone(intent.is_truncated_add(text))

    def test_other_commands_are_left_alone(self):
        for text in ("set a timer for", "cancel the timer", "show my to-dos",
                     "clear the shopping list", "what's on the list",
                     "add a timer to the shopping list", "play some music", ""):
            with self.subTest(text=text):
                self.assertIsNone(intent.is_truncated_add(text))


class ListScopeTest(unittest.TestCase):
    """Whose list "show my to-dos" means."""

    def test_possessive_asks_for_their_own(self):
        for text in ("show my to-dos", "what's on my to-do list",
                     "read me my todos", "what are mine"):
            with self.subTest(text=text):
                self.assertTrue(intent.wants_own_list(text))

    def test_household_phrasing_is_not_possessive(self):
        # "show me the list" has "me" but not "my" — it is the house's list.
        for text in ("show the to-do list", "what's on our to-do list",
                     "show me the to-dos", "read the todo list", "show todos"):
            with self.subTest(text=text):
                self.assertFalse(intent.wants_own_list(text))


class PrivateFlagTest(unittest.TestCase):
    """The opt-out that keeps a reminder off the kitchen display."""

    def test_explicit_phrasings(self):
        for text in ("remind me privately to call the lawyer",
                     "remind me in private to book the trip",
                     "add a private reminder to cancel the subscription",
                     "make a private note to check the balance"):
            with self.subTest(text=text):
                self.assertTrue(intent.wants_private(text))

    def test_private_as_ordinary_content_does_not_trigger(self):
        # A privacy switch that fires by accident is worse than one you have to
        # say plainly — "private" inside the content itself must not count.
        for text in ("remind me to book the private school tour",
                     "remind me to call the private investigator",
                     "add private jet tickets to the shopping list",
                     "remind me to call mom"):
            with self.subTest(text=text):
                self.assertFalse(intent.wants_private(text))


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


class MusicVolumeTest(unittest.TestCase):
    """Absolute music volume must survive the classifier's relative guess."""

    def test_reads_spoken_and_digit_levels(self):
        cases = {
            "volume eighty": 80,
            "set the volume to 80": 80,
            "music volume at forty five percent": 45,
            "volume zero": 0,
            "volume 100": 100,
        }
        for text, want in cases.items():
            with self.subTest(text=text):
                self.assertEqual(intent.spoken_music_volume(text), want)

    def test_rejects_missing_and_out_of_range_levels(self):
        for text in ("turn up the music", "volume", "volume 101", "eighty"):
            with self.subTest(text=text):
                self.assertIsNone(intent.spoken_music_volume(text))

    def test_live_phrase_overrides_volume_up_classifier_result(self):
        raw = '{"intent":"music_control","music_action":"volume_up"}'
        with patch.object(intent.clients, "parse_intent_raw",
                          new=AsyncMock(return_value=raw)):
            parsed = asyncio.run(intent.parse("volume eighty"))
        self.assertEqual(parsed["music_action"], "volume_set")
        self.assertEqual(parsed["music_volume"], 80)

    def test_number_does_not_override_a_non_music_intent(self):
        raw = '{"intent":"ask","query":"what is volume 80"}'
        with patch.object(intent.clients, "parse_intent_raw",
                          new=AsyncMock(return_value=raw)):
            parsed = asyncio.run(intent.parse("what is volume 80"))
        self.assertEqual(parsed["intent"], "ask")
        self.assertIsNone(parsed["music_volume"])


class DeterministicIntentTest(unittest.TestCase):
    """Narrow complete commands that do not need semantic classification."""

    def test_unlabelled_timer_forms(self):
        cases = {
            "set a timer for eight minutes": 480,
            "start the timer for 1 hour 10 minutes": 4200,
            "please set timer for about twenty minutes": 1200,
            "could you start a timer for 90 seconds please": 90,
        }
        for text, seconds in cases.items():
            with self.subTest(text=text):
                parsed = intent.fast_parse(text)
                self.assertEqual(parsed["intent"], "set_timer")
                self.assertEqual(parsed["duration_seconds"], seconds)
                self.assertIsNone(parsed["label"])
                self.assertEqual(parsed["sound_theme"], "marimba")

    def test_labelled_timer_forms(self):
        cases = {
            "set a pasta timer for eight minutes": ("pasta", 480, "steam_whistle"),
            "set a chicken timer for 12 minutes": ("chicken", 720, "cluck"),
            "start the roasting timer for twenty minutes": (
                "roasting", 1200, "oven_ding"),
            "set a timer called tofu for ten minutes": ("tofu", 600, "marimba"),
            "set our coffee timer for 90 seconds": ("coffee", 90, "steam_whistle"),
            # Not food, no table entry: still fast, just rings the default.
            "set a dad work timer for 30 minutes": ("dad work", 1800, "marimba"),
            "please set a claire timer for five minutes": (
                "claire", 300, "marimba"),
        }
        for text, (label, seconds, theme) in cases.items():
            with self.subTest(text=text):
                parsed = intent.fast_parse(text)
                self.assertEqual(parsed["intent"], "set_timer")
                self.assertEqual(parsed["label"], label)
                self.assertEqual(parsed["duration_seconds"], seconds)
                self.assertEqual(parsed["sound_theme"], theme)

    def test_labelled_timer_fast_path_fails_closed(self):
        """Every one of these must still reach the classifier."""
        for text in (
            # A duration is not a name.
            "set a 10 minute timer for the pasta",
            "set a five minute timer for pasta",
            # Label too long, or opening with a command verb.
            "set a really long complicated dinner party timer for ten minutes",
            "set a stop timer for ten minutes",
            # Duration unreadable -> the LLM gets the whole utterance.
            "set a pasta timer for a while",
            "set a pasta timer for eight",
            # Adjustments and other timer verbs are untouched.
            "add eight minutes to the pasta timer",
            "change the pasta timer to ten minutes",
        ):
            with self.subTest(text=text):
                self.assertIsNone(intent.fast_parse(text))

    def test_timer_fast_path_fails_closed(self):
        for text in (
            "set a timer for eight",
            "set a timer for",
            "add eight minutes to the timer",
            "remind me in eight minutes",
        ):
            with self.subTest(text=text):
                self.assertIsNone(intent.fast_parse(text))

    def test_timer_rename_forms(self):
        cases = {
            "rename the timer to Pasta Timer": (None, "pasta"),
            "rename the chicken timer to pasta": ("chicken", "pasta"),
            "change the timer to be called pasta timer": (None, "pasta"),
            "call the timer dinner rolls": (None, "dinner rolls"),
        }
        for text, (old_label, new_label) in cases.items():
            with self.subTest(text=text):
                parsed = intent.fast_parse_timer_rename(text)
                self.assertEqual(parsed["intent"], "timer_rename")
                self.assertEqual(parsed["label"], old_label)
                self.assertEqual(parsed["new_label"], new_label)

    def test_incomplete_timer_rename_arms_a_name_slot(self):
        for text in ("rename the timer to",
                     "change the timer to be called",
                     "change the timer to be a"):
            with self.subTest(text=text):
                parsed = intent.fast_parse_timer_rename(text)
                self.assertEqual(parsed["intent"], "timer_rename")
                self.assertIsNone(parsed["new_label"])

    def test_ambiguous_timer_changes_are_not_renames(self):
        for text in ("change the timer to ten minutes",
                     "add pasta to the timer", "rename my timer someday"):
            with self.subTest(text=text):
                self.assertIsNone(intent.fast_parse_timer_rename(text))

    def test_music_transport_and_query(self):
        cases = {
            "pause the music": ("music_control", "pause"),
            "keep playing": ("music_control", "resume"),
            "stop music": ("music_control", "stop"),
            "skip this song": ("music_control", "next"),
            "go back a song": ("music_control", "previous"),
            "turn it up": ("music_control", "volume_up"),
            "music quieter": ("music_control", "volume_down"),
            "normal volume": ("music_control", "volume_normal"),
            "what's playing": ("music_query", None),
            "who sings this": ("music_query", None),
        }
        for text, (kind, action) in cases.items():
            with self.subTest(text=text):
                parsed = intent.fast_parse(text)
                self.assertEqual(parsed["intent"], kind)
                self.assertEqual(parsed["music_action"], action)

    def test_absolute_music_volume_is_complete_and_strict(self):
        cases = {"volume eighty": 80, "set the volume to 45": 45,
                 "music volume at 100 percent": 100}
        for text, level in cases.items():
            with self.subTest(text=text):
                parsed = intent.fast_parse(text)
                self.assertEqual(parsed["music_action"], "volume_set")
                self.assertEqual(parsed["music_volume"], level)
        for text in ("volume 80 in the kitchen", "what is volume 80",
                     "turn the volume toward 80", "volume 101"):
            with self.subTest(text=text):
                self.assertIsNone(intent.fast_parse(text))

    def test_music_search_and_ambiguous_bare_stop_still_use_classifier(self):
        for text in ("play Raffi", "play some music", "stop", "go back", "next"):
            with self.subTest(text=text):
                self.assertIsNone(intent.fast_parse(text))

    def test_local_weather_forms(self):
        cases = {
            "what's the weather": "now",
            "how hot is it outside": "now",
            "what's the forecast": "today",
            "weather today": "today",
            "forecast for tomorrow": "tomorrow",
            "will it rain tonight": "tonight",
            "what is the weather saturday": "saturday",
        }
        for text, when in cases.items():
            with self.subTest(text=text):
                parsed = intent.fast_parse(text)
                self.assertEqual(parsed["intent"], "weather")
                self.assertEqual(parsed["weather_when"], when)

    def test_named_weather_forms(self):
        cases = {
            "weather in Chicago": ("chicago", "now"),
            "what's the weather in Park City today": ("park city", "today"),
            "forecast for Paris tomorrow": ("paris", "tomorrow"),
            "will it snow in Alta on saturday": ("alta", "saturday"),
        }
        for text, (location, when) in cases.items():
            with self.subTest(text=text):
                parsed = intent.fast_parse(text)
                self.assertEqual(parsed["intent"], "weather")
                self.assertEqual(parsed["weather_location"], location)
                self.assertEqual(parsed["weather_when"], when)

    def test_uncertain_weather_falls_through(self):
        for text in ("weather next month", "is it going to be nice",
                     "what about tomorrow", "forecast for here"):
            with self.subTest(text=text):
                self.assertIsNone(intent.fast_parse(text))

if __name__ == "__main__":
    unittest.main()
