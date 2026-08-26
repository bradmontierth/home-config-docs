import asyncio
import json
import os
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

from . import app, config, home_control


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
            "close the blinds": "button.voice_blinds_kitchen_close",
            "open the kitchen blinds": "button.voice_blinds_kitchen_open",
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
            "shut the blinds": "button.voice_blinds_kitchen_close",
            "make it brighter in here": "button.voice_kitchen_brighten",
            "close the little blinds": "button.voice_blind_small_close",
            "dinner mode": "button.voice_dinner_mood",
            "reset the lights": "button.voice_lights_normal",
            "could you close the blinds": "button.voice_blinds_kitchen_close",
            "fix the glare please": "button.voice_blind_glare_close",
            "closed the blinds": "button.voice_blinds_kitchen_close",
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
        self.assertEqual(self._pressed(), "button.voice_blinds_kitchen_close")
        self.press.reset_mock()
        self.assertIsNone(_handle("keep the lights on", "kitchen"))
        self.press.assert_not_awaited()

    def test_bath_satellite_can_still_reach_house_wide_commands(self):
        """Scoping is a first look, not a cage: nothing local matches these,
        so the master satellite falls through to the house-wide table."""
        self.press.reset_mock()
        _handle("close the kitchen blinds", "master")
        self.assertEqual(self._pressed(), "button.voice_blinds_kitchen_close")
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

    def test_simon_room_commands_are_local_and_unambiguous(self):
        cases = {
            "open the blind": "button.voice_simon_blind_open",
            "close the blinds": "button.voice_simon_blind_close",
            "turn on the lights": "button.voice_simon_lights_on",
            "lights off": "button.voice_simon_lights_off",
        }
        for phrase, entity in cases.items():
            with self.subTest(phrase=phrase):
                self.press.reset_mock()
                result = _handle(phrase, "simon")
                self.assertIsNotNone(result)
                self.assertEqual(self._pressed(), entity)

        self.press.reset_mock()
        self.assertIsNone(_handle("give me a fun color", "kitchen"))
        self.press.assert_not_awaited()

    def test_simon_fan_never_crosses_with_the_lights(self):
        cases = {
            "turn on the fan": "button.voice_simon_fan_on",
            "turn the fan on": "button.voice_simon_fan_on",
            "fan off": "button.voice_simon_fan_off",
            "turn off the fan": "button.voice_simon_fan_off",
            "set the fan to low": "button.voice_simon_fan_low",
            "turn the fan down": "button.voice_simon_fan_low",
            "fan on medium": "button.voice_simon_fan_medium",
            "turn the fan on high": "button.voice_simon_fan_high",
            "turn the fan up": "button.voice_simon_fan_high",
            # The one-noun neighbours must still land on the lights.
            "turn on the lights": "button.voice_simon_lights_on",
            "turn the lights off": "button.voice_simon_lights_off",
        }
        for phrase, entity in cases.items():
            with self.subTest(phrase=phrase):
                self.press.reset_mock()
                result = _handle(phrase, "simon")
                self.assertIsNotNone(result, phrase)
                self.assertEqual(self._pressed(), entity)

    def test_simon_named_effects(self):
        cases = {
            "pac man": "button.voice_simon_fx_pac_man",
            "pack man": "button.voice_simon_fx_pac_man",
            "set it to pac man": "button.voice_simon_fx_pac_man",
            "waves": "button.voice_simon_fx_ocean_waves",
            "dino stomp": "button.voice_simon_fx_dino_stomp",
            "dinosaurs": "button.voice_simon_fx_dino_stomp",
            "slow rainbow": "button.voice_simon_fx_rainbow_slow",
            "rainbow": "button.voice_simon_fx_rainbow",
            "give me a cool color": "button.voice_simon_fun_color",
        }
        for phrase, entity in cases.items():
            with self.subTest(phrase=phrase):
                self.press.reset_mock()
                result = _handle(phrase, "simon")
                self.assertIsNotNone(result, phrase)
                self.assertEqual(self._pressed(), entity)
        self.press.reset_mock()
        self.assertIsNone(_handle("pac man", "kitchen"))
        self.press.assert_not_awaited()

    def test_simon_fan_is_room_local(self):
        # Simon's fan is Simon's room only: the kitchen's "turn on the fan"
        # is the family-room ceiling fan, never his.
        self.press.reset_mock()
        _handle("turn on the fan", "kitchen")
        self.assertEqual(self._pressed(), "button.voice_fr_fan_on")
        self.press.reset_mock()
        self.assertIsNone(_handle("turn on the fan", "master"))
        self.press.assert_not_awaited()

    def test_claire_room_commands_are_local_and_unambiguous(self):
        cases = {
            "story time": "button.voice_claire_story_time",
            "let's read a book": "button.voice_claire_story_time",
            "bedtime": "button.voice_claire_bedtime",
            "night night": "button.voice_claire_bedtime",
            "close the blind": "button.voice_claire_blind_close",
            "turn on the lights": "button.voice_claire_lights_on",
            "lights off": "button.voice_claire_lights_off",
            "turn on the fan": "button.voice_claire_fan_on",
            "fan on high": "button.voice_claire_fan_high",
            "it's hot in here": "button.voice_claire_too_hot",
            "it's cold in here": "button.voice_claire_too_cold",
            "turn on the ac": "button.voice_claire_hvac_cool",
            "turn on the heat": "button.voice_claire_hvac_heat",
            "turn off the ac": "button.voice_claire_hvac_off",
        }
        for phrase, entity in cases.items():
            with self.subTest(phrase=phrase):
                self.press.reset_mock()
                result = _handle(phrase, "claire")
                self.assertIsNotNone(result)
                self.assertEqual(self._pressed(), entity)

        # Nothing of Claire's is reachable from the kitchen or Simon's room.
        for phrase in ("story time", "bedtime", "it's hot in here"):
            for sat in ("kitchen", "simon"):
                self.press.reset_mock()
                self.assertIsNone(_handle(phrase, sat), (phrase, sat))
                self.press.assert_not_awaited()

    def test_brighter_is_room_local_and_the_kitchen_keeps_its_own(self):
        for sat in ("simon", "claire"):
            for phrase in ("make it brighter", "brighter", "turn up the lights", "more light"):
                self.press.reset_mock()
                _handle(phrase, sat)
                self.assertEqual(self._pressed(), f"button.voice_{sat}_brighter", (sat, phrase))
        self.press.reset_mock()
        _handle("make it brighter", "kitchen")
        self.assertEqual(self._pressed(), "button.voice_kitchen_brighten")
        # "turn on the lights" while they are on is the stage-2 press; still lights_on.
        self.press.reset_mock()
        _handle("turn on the lights", "simon")
        self.assertEqual(self._pressed(), "button.voice_simon_lights_on")

    def test_the_blinds_are_per_microphone_in_the_open_space(self):
        # Same phrase, different mic, different blinds: the kitchen's four
        # from the kitchen mic, the family room's two from that mic.
        _handle("close the blinds", sat="kitchen")
        self.assertEqual(self._pressed(), "button.voice_blinds_kitchen_close")
        _handle("close the blinds", sat="familyroom")
        self.assertEqual(self._pressed(), "button.voice_blinds_family_close")
        _handle("open the blinds", sat="familyroom")
        self.assertEqual(self._pressed(), "button.voice_blinds_family_open")
        # A caller with no room at all is the kitchen (pre-rooms behaviour).
        _handle("close the blinds")
        self.assertEqual(self._pressed(), "button.voice_blinds_kitchen_close")

    def test_naming_the_family_room_reaches_it_from_the_kitchen(self):
        _handle("close the family room blinds", sat="kitchen")
        self.assertEqual(self._pressed(), "button.voice_blinds_family_close")
        _handle("open the family room blinds", sat="master")
        self.assertEqual(self._pressed(), "button.voice_blinds_family_open")
        _handle("close the kitchen blinds", sat="familyroom")
        self.assertEqual(self._pressed(), "button.voice_blinds_kitchen_close")

    def test_all_the_blinds_is_the_six_from_either_mic(self):
        # "close ALL the blinds" scores 89 against the room's own "close the
        # blinds"; without the all-pin the local-first pass would take it.
        for sat in ("kitchen", "familyroom"):
            for phrase in ("close all the blinds", "close every blind",
                           "close all of the blinds"):
                _handle(phrase, sat=sat)
                self.assertEqual(self._pressed(), "button.voice_blinds_all_close",
                                 (sat, phrase))
            _handle("open all the blinds", sat=sat)
            self.assertEqual(self._pressed(), "button.voice_blinds_all_open", sat)
        # "all the KITCHEN blinds" is still just the kitchen four.
        _handle("close all the kitchen blinds", sat="familyroom")
        self.assertEqual(self._pressed(), "button.voice_blinds_kitchen_close")

    def test_all_the_blinds_elsewhere_stays_in_that_room(self):
        # The six-blind command belongs to the open space; from a bedroom or
        # the bath, "all the blinds" is that room's one blind.
        _handle("close all the blinds", sat="simon")
        self.assertEqual(self._pressed(), "button.voice_simon_blind_close")
        _handle("close all the blinds", sat="master")
        self.assertEqual(self._pressed(), "button.voice_blind_bath_close")
        # And "all" never lifts the room-local guard elsewhere.
        _handle("turn off all the lights", sat="simon")
        self.assertEqual(self._pressed(), "button.voice_simon_lights_off")

    def test_family_room_fan_from_both_mics_and_not_elsewhere(self):
        for sat in ("kitchen", "familyroom"):
            _handle("turn on the fan", sat=sat)
            self.assertEqual(self._pressed(), "button.voice_fr_fan_on", sat)
            _handle("set the fan to medium", sat=sat)
            self.assertEqual(self._pressed(), "button.voice_fr_fan_medium", sat)
            _handle("fan on high", sat=sat)
            self.assertEqual(self._pressed(), "button.voice_fr_fan_high", sat)
            _handle("turn the fan off", sat=sat)
            self.assertEqual(self._pressed(), "button.voice_fr_fan_off", sat)
        self.press.reset_mock()
        self.assertIsNone(_handle("turn on the fan", sat="master"))
        self.assertIsNone(self._pressed())
        # A kid's room keeps its own fan.
        _handle("turn on the fan", sat="simon")
        self.assertEqual(self._pressed(), "button.voice_simon_fan_on")

    def test_the_cans_are_the_family_room_cans(self):
        for sat in ("kitchen", "familyroom"):
            _handle("turn off the cans", sat=sat)
            self.assertEqual(self._pressed(), "button.voice_fr_cans_off", sat)
            _handle("kill the cans", sat=sat)
            self.assertEqual(self._pressed(), "button.voice_fr_cans_off", sat)
            _handle("turn on the can lights", sat=sat)
            self.assertEqual(self._pressed(), "button.voice_fr_cans_on", sat)

    def test_fan_and_cans_never_cross(self):
        # fuzz.ratio("turn on the fan", "turn on the cans") is 90 — an ASR
        # plural or a dropped consonant must not swap a light for a fan.
        _handle("turn on the fans", sat="kitchen")
        self.assertEqual(self._pressed(), "button.voice_fr_fan_on")
        _handle("turn off the can", sat="kitchen")
        self.assertEqual(self._pressed(), "button.voice_fr_cans_off")
        _handle("turn on the can", sat="familyroom")
        self.assertEqual(self._pressed(), "button.voice_fr_cans_on")
        _handle("turn off the fans", sat="familyroom")
        self.assertEqual(self._pressed(), "button.voice_fr_fan_off")

    def test_apostrophes_and_the_asr_split_artefact_still_match(self):
        for phrase in ("it's story time", "its story time", "it s story time",
                       "It\u2019s story time"):
            self.press.reset_mock()
            _handle(phrase, "claire")
            self.assertEqual(self._pressed(), "button.voice_claire_story_time", phrase)
        self.assertTrue(home_control.has_exact_match("it s story time", "claire"))
        self.assertEqual(home_control.fuzzy_match("story time please", "claire"),
                         "claire_story_time")
        self.assertIsNone(home_control.fuzzy_match("what time is it", "claire"))

    def test_claire_hot_and_cold_never_cross(self):
        # One word apart; a miss would run the mini split the wrong way.
        self.press.reset_mock()
        _handle("it's too hot", "claire")
        self.assertEqual(self._pressed(), "button.voice_claire_too_hot")
        self.press.reset_mock()
        _handle("it's too cold", "claire")
        self.assertEqual(self._pressed(), "button.voice_claire_too_cold")
        self.press.reset_mock()
        _handle("turn off the heat", "claire")
        self.assertEqual(self._pressed(), "button.voice_claire_hvac_off")

    def test_naming_a_kids_room_reaches_it_from_the_kitchen(self):
        self.press.reset_mock()
        _handle("close claire's blind", "kitchen")
        self.assertEqual(self._pressed(), "button.voice_claire_blind_close")
        self.press.reset_mock()
        _handle("close simon's blind", "kitchen")
        self.assertEqual(self._pressed(), "button.voice_simon_blind_close")

    def test_exact_simon_alias_can_rescue_an_intent_model_miss(self):
        self.assertTrue(home_control.has_exact_match("set a font color", "simon"))
        self.assertFalse(home_control.has_exact_match("set a font color", "kitchen"))
        self.assertFalse(home_control.has_exact_match("tell me about color", "simon"))

    def test_fun_color_presses_the_room_local_button(self):
        self.press.reset_mock()
        result = _handle("set a fun color", "simon")
        self.assertTrue(result["ok"])
        self.assertEqual(result["response"], "Picking a cool effect.")
        self.assertEqual(self._pressed(), "button.voice_simon_fun_color")

    def test_naming_a_room_overrides_the_room_you_are_standing_in(self):
        self.press.reset_mock()
        _handle("close the bathroom blind", "kitchen")
        self.assertEqual(self._pressed(), "button.voice_blind_bath_close")
        self.press.reset_mock()
        _handle("close the kitchen blinds", "master")
        self.assertEqual(self._pressed(), "button.voice_blinds_kitchen_close")


class HomeControlFastPathTest(unittest.TestCase):
    def _assert_fast_path(self, phrase: str):
        press = AsyncMock()
        parse = AsyncMock()
        token = app._CUR_SAT.set("simon")
        self.addCleanup(app._CUR_SAT.reset, token)
        with (
            patch.object(app.intent_mod, "parse", new=parse),
            patch.object(app.home_mod, "_press", new=press),
            patch.object(app, "_speak_reply", new=AsyncMock()),
            patch.object(app.broadcast_mod, "send", new=AsyncMock()),
            patch.object(app.events, "emit", new=AsyncMock()),
        ):
            result = asyncio.run(app.handle_command(phrase))

        parse.assert_not_awaited()
        self.assertEqual(result["intent"], "home_control")
        self.assertTrue(result["ok"])
        self.assertEqual(result["response"], "Picking a cool effect.")
        press.assert_awaited_once_with("button.voice_simon_fun_color")

    def test_font_homophone_bypasses_classifier(self):
        self._assert_fast_path("set a font color")

    def test_cool_color_bypasses_classifier(self):
        self._assert_fast_path("give me a cool color")


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

    def test_resplits_a_pre_split_live_table(self):
        # A live table from before 2026-08-25 has blinds_all_* unscoped and
        # carrying the bare phrase. On load it must become the six-blind
        # command and the per-room entries must appear.
        seed = json.loads(home_control._SEED_FILE.read_text())
        old = {k: v for k, v in seed.items()
               if not k.startswith(("blinds_", "fr_"))}
        old["blinds_all_close"] = {
            "aliases": ["close the blinds", "close all the blinds"],
            "entity": "button.voice_blinds_all_close",
            "confirm": "Closing all the kitchen blinds."}
        old["blinds_all_open"] = {
            "aliases": ["open the blinds", "open all the blinds"],
            "entity": "button.voice_blinds_all_open",
            "confirm": "Opening all the kitchen blinds."}
        with open(self.file, "w") as f:
            json.dump(old, f)
        table = home_control._commands()
        self.assertEqual(table["blinds_all_close"]["sats"], ["kitchen", "familyroom"])
        self.assertNotIn("close the blinds", table["blinds_all_close"]["aliases"])
        self.assertIn("blinds_kitchen_close", table)
        self.assertIn("blinds_family_open", table)
        self.assertIn("fr_fan_medium", table)
        with open(self.file) as f:
            self.assertEqual(json.load(f)["blinds_all_open"]["sats"],
                             ["kitchen", "familyroom"])

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
