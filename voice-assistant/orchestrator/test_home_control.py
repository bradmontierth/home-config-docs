import asyncio
import unittest
from unittest.mock import AsyncMock, patch

from . import home_control


def _handle(query: str):
    return asyncio.run(home_control.handle({"query": query}, query))


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


if __name__ == "__main__":
    unittest.main()
