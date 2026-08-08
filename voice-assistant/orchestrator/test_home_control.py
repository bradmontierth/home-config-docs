import asyncio
import json
import os
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

from . import config, home_control


def _handle(query: str, sat: str | None = None):
    return asyncio.run(home_control.handle({"query": query}, query, sat))


class HomeControlMatchTest(unittest.TestCase):
    """Alias matching only — the HA press is mocked out."""

    def setUp(self):
        patcher = patch.object(home_control, "_press", new=AsyncMock())
        self.press = patcher.start()
        self.addCleanup(patcher.stop)

    def _pressed(self):
        return self.press.await_args.args[0] if self.press.await_args else None

    def test_canonical_aliases_hit_their_buttons(self):
        cases = {
            "close the blinds": "button.voice_blinds_all_close",
            "open the kitchen blinds": "button.voice_blinds_all_open",
            "fix the glare": "button.voice_blind_glare_close",
            "close the kitchen sink": "button.voice_blind_sink_close",
            "close the sliding door": "button.voice_blind_slider_close",
            "close the big one": "button.voice_blind_slider_close",
            "close the small blinds": "button.voice_blind_small_close",
            "brighten the lights": "button.voice_kitchen_brighten",
            "set the mood for dinner": "button.voice_dinner_mood",
            "back to normal": "button.voice_lights_normal",
        }
        for phrase, entity in cases.items():
            with self.subTest(phrase=phrase):
                self.press.reset_mock()
                result = _handle(phrase)
                self.assertIsNotNone(result, phrase)
                self.assertTrue(result["ok"])
                self.assertEqual(self._pressed(), entity)

    def test_paraphrases(self):
        cases = {
            "shut the blinds": "button.voice_blinds_all_close",
            "make it brighter in here": "button.voice_kitchen_brighten",
            "close the little blinds": "button.voice_blind_small_close",
            "dinner mode": "button.voice_dinner_mood",
            "reset the lights": "button.voice_lights_normal",
            "could you close the blinds": "button.voice_blinds_all_close",
            "fix the glare please": "button.voice_blind_glare_close",
            "closed the blinds": "button.voice_blinds_all_close",
        }
        for phrase, entity in cases.items():
            with self.subTest(phrase=phrase):
                self.press.reset_mock()
                result = _handle(phrase)
                self.assertIsNotNone(result, phrase)
                self.assertEqual(self._pressed(), entity)

    def test_left_right_never_cross(self):
        self.press.reset_mock()
        _handle("close the left blind")
        self.assertEqual(self._pressed(), "button.voice_blind_left_close")
        self.press.reset_mock()
        _handle("close the right blind")
        self.assertEqual(self._pressed(), "button.voice_blind_right_close")
        self.press.reset_mock()
        _handle("open the left blind")
        self.assertEqual(self._pressed(), "button.voice_blind_left_open")

    def test_excluded_domains_miss(self):
        for phrase in ("open the garage", "unlock the front door",
                       "disarm the alarm", "open the garage door"):
            with self.subTest(phrase=phrase):
                self.press.reset_mock()
                self.assertIsNone(_handle(phrase), phrase)
                self.press.assert_not_awaited()

    def test_questions_and_noise_miss(self):
        for phrase in ("are the blinds closed", "what a nice dinner we had",
                       "turn on the sprinklers"):
            with self.subTest(phrase=phrase):
                self.press.reset_mock()
                self.assertIsNone(_handle(phrase), phrase)
                self.press.assert_not_awaited()

    def test_empty_query_misses(self):
        self.assertIsNone(asyncio.run(home_control.handle({"query": None}, "")))
        self.press.assert_not_awaited()

    def test_confirmation_is_spoken_verbatim(self):
        result = _handle("fix the glare")
        self.assertEqual(result["response"], "Closing the blinds to fix the glare.")

    def test_room_local_commands_win_in_their_own_room(self):
        """The phrase that means two different things: in the master bath it
        is the one blind in there, in the kitchen it is the four in there."""
        for phrase in ("close the blinds", "close the blind", "shut the blind"):
            with self.subTest(phrase=phrase, sat="master"):
                self.press.reset_mock()
                _handle(phrase, "master")
                self.assertEqual(self._pressed(),
                                 "button.voice_blind_bath_close")
        self.press.reset_mock()
        _handle("open the blinds", "master")
        self.assertEqual(self._pressed(), "button.voice_blind_bath_open")

    def test_room_local_commands_are_invisible_elsewhere(self):
        self.press.reset_mock()
        _handle("close the blinds", "kitchen")
        self.assertEqual(self._pressed(), "button.voice_blinds_all_close")
        self.press.reset_mock()
        self.assertIsNone(_handle("keep the lights on", "kitchen"))
        self.press.assert_not_awaited()

    def test_bath_satellite_can_still_reach_house_wide_commands(self):
        """Scoping is a first look, not a cage: nothing local matches these,
        so the master satellite falls through to the house-wide table."""
        self.press.reset_mock()
        _handle("close the kitchen blinds", "master")
        self.assertEqual(self._pressed(), "button.voice_blinds_all_close")
        self.press.reset_mock()
        _handle("back to normal", "master")
        self.assertEqual(self._pressed(), "button.voice_lights_normal")

    def test_lights_hold_from_the_master_satellite(self):
        for phrase in ("keep the lights on", "leave the lights on",
                       "don't turn the lights off"):
            with self.subTest(phrase=phrase):
                self.press.reset_mock()
                result = _handle(phrase, "master")
                self.assertIsNotNone(result, phrase)
                self.assertEqual(self._pressed(),
                                 "button.voice_master_lights_hold")

    def test_naming_a_room_overrides_the_room_you_are_standing_in(self):
        self.press.reset_mock()
        _handle("close the bathroom blind", "kitchen")
        self.assertEqual(self._pressed(), "button.voice_blind_bath_close")
        self.press.reset_mock()
        _handle("close the kitchen blinds", "master")
        self.assertEqual(self._pressed(), "button.voice_blinds_all_close")


class HomeCommandsEditTest(unittest.TestCase):
    """Editor mutations against a temp copy — the repo file is never written."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.file = os.path.join(tmp.name, "home_commands.json")
        patcher = patch.object(config, "HOME_COMMANDS_FILE", self.file)
        patcher.start()
        self.addCleanup(patcher.stop)
        home_control._commands_cache = None
        self.addCleanup(setattr, home_control, "_commands_cache", None)

    def test_seeds_from_repo_copy(self):
        commands = home_control._commands()
        self.assertTrue(os.path.exists(self.file))
        self.assertIn("blinds_all_close", commands)

    def test_add_alias_persists_and_matches(self):
        home_control.add_alias("blinds_all_close", "Darken the Kitchen")
        on_disk = json.loads(open(self.file).read())
        self.assertIn("darken the kitchen", on_disk["blinds_all_close"]["aliases"])
        verdict = home_control.evaluate("darken the kitchen")
        self.assertTrue(verdict["matched"])
        self.assertEqual(verdict["command"], "blinds_all_close")

    def test_add_alias_rejects_bad_input(self):
        with self.assertRaises(ValueError):
            home_control.add_alias("blinds_all_close", "   ")
        with self.assertRaises(ValueError):
            home_control.add_alias("no_such_command", "whatever")
        # alias already owned by another command
        with self.assertRaises(ValueError):
            home_control.add_alias("blinds_all_close", "fix the glare")

    def test_remove_alias(self):
        home_control.add_alias("dinner_mood", "cozy dinner")
        home_control.remove_alias("dinner_mood", "cozy dinner")
        on_disk = json.loads(open(self.file).read())
        self.assertNotIn("cozy dinner", on_disk["dinner_mood"]["aliases"])
        with self.assertRaises(ValueError):
            home_control.remove_alias("dinner_mood", "cozy dinner")

    def test_cannot_remove_last_alias(self):
        entry = home_control.snapshot()["blind_glare_close"]
        for alias in entry["aliases"][:-1]:
            home_control.remove_alias("blind_glare_close", alias)
        with self.assertRaises(ValueError):
            home_control.remove_alias("blind_glare_close",
                                      entry["aliases"][-1])

    def test_evaluate_reports_near_miss(self):
        verdict = home_control.evaluate("unlock the front door")
        self.assertFalse(verdict["matched"])
        self.assertIn("command", verdict)  # closest candidate still reported
        self.assertLess(verdict["score"], verdict["threshold"])


if __name__ == "__main__":
    unittest.main()
