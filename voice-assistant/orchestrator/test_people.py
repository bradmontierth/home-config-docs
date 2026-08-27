"""Who a list item is for, and the two deterministic backstops added the same
night (2026-08-26): the reminder rescue accepting a named person, and the
verb-less add-time command that the kitchen's echo suppressor produced."""

import unittest

from . import intent, people


class TargetTest(unittest.TestCase):
    def test_remind_named_person(self):
        key, text = people.target_in("remind brad to roast coffee today")
        self.assertEqual(key, "brad")
        self.assertEqual(text, "remind me to roast coffee today")

    def test_nicknames_and_politeness(self):
        key, text = people.target_in("please remind mom that the dentist is at five")
        self.assertEqual(key, "adrienne")
        self.assertEqual(text, "please remind me that the dentist is at five")
        key, _ = people.target_in("could you remind dad to take the bins out")
        self.assertEqual(key, "brad")

    def test_reminder_for(self):
        key, text = people.target_in("set a reminder for adrienne to call the school")
        self.assertEqual(key, "adrienne")
        self.assertEqual(text, "set a reminder for me to call the school")

    def test_possessive_list(self):
        key, text = people.target_in("add call the plumber to brad's to-do list")
        self.assertEqual(key, "brad")
        self.assertEqual(text, "add call the plumber to my to-do list")

    def test_self_and_unknown_untouched(self):
        for cmd in ("remind me to roast coffee", "remind us to leave at six",
                    "remind everyone to bring a coat", "remind the kids to brush",
                    "add eggs to the shopping list"):
            key, text = people.target_in(cmd)
            self.assertIsNone(key, cmd)
            self.assertEqual(text, cmd)

    def test_asr_spelling(self):
        # "bradley" is what Parakeet made of "brad" on 2026-08-25.
        self.assertEqual(people.resolve("bradley"), "brad")
        self.assertEqual(people.resolve("adrian"), "adrienne")
        self.assertIsNone(people.resolve("simon"))


class TruncatedNamedReminderTest(unittest.TestCase):
    def test_named_person_still_rescued(self):
        self.assertEqual(intent.is_truncated_add("remind brad to"), "set_reminder")
        self.assertEqual(intent.is_truncated_add("remind me to"), "set_reminder")
        self.assertIsNone(intent.is_truncated_add("remind brad to roast coffee"))


class ImplicitAdjustTest(unittest.TestCase):
    def test_the_live_failure(self):
        self.assertTrue(intent.is_implicit_adjust("three minutes to my call fire timer"))

    def test_other_verbless_adds(self):
        for cmd in ("two more minutes on the rice", "five minutes onto the pasta timer",
                    "an extra minute to the chicken", "ten minutes to that timer"):
            self.assertTrue(intent.is_implicit_adjust(cmd), cmd)

    def test_real_sets_are_left_alone(self):
        for cmd in ("set a call fire timer for 15 minutes",
                    "start a timer for three minutes", "make a timer to my liking",
                    "call fire timer for three minutes", "timer for five minutes",
                    "put on a three minute timer"):
            self.assertFalse(intent.is_implicit_adjust(cmd), cmd)
