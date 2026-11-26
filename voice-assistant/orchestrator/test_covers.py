import asyncio
import unittest
from unittest.mock import AsyncMock, patch

from . import app, covers, home_control, intent
from .test_clarify import _SlotTestBase


def _parse(command: str, sat: str | None = "kitchen"):
    return intent.fast_parse_cover_level(command, sat)


class CoverLevelParseTest(unittest.TestCase):
    """The grammar. Direction comes from the verb, never from the number."""

    def test_neutral_verb_is_openness(self):
        cases = {
            "set the kitchen blinds to 80 percent": ("blinds_all", 80),
            "set the sink blind to 30": ("blind_sink", 30),
            "set the sink blind to thirty percent": ("blind_sink", 30),
            "put the left blind at 45 percent": ("blind_left", 45),
            "set the blinds to eighty five percent": ("blinds_all", 85),
        }
        for text, (target, position) in cases.items():
            with self.subTest(text=text):
                parsed = _parse(text)
                self.assertEqual(parsed["intent"], "cover_set")
                self.assertEqual(parsed["cover_target"], target)
                self.assertEqual(parsed["cover_position"], position)

    def test_sync_is_heard_as_sink(self):
        parsed = _parse("set the sync blind to 30 percent")
        self.assertEqual(parsed["cover_target"], "blind_sink")
        self.assertEqual(covers.resolve("the sync blind"), "blind_sink")

    def test_close_verb_inverts_the_number(self):
        """"Close it to 80%" means 80% down -- HA position 20."""
        cases = {
            "close the sink blind to 80 percent": ("blind_sink", 20),
            "lower the kitchen blinds to 70 percent": ("blinds_all", 30),
            "drop the left blind to 60": ("blind_left", 40),
            "close the blinds 25 percent": ("blinds_all", 75),
        }
        for text, expected in cases.items():
            with self.subTest(text=text):
                parsed = _parse(text)
                if expected is None:
                    self.assertIsNone(parsed)
                    continue
                self.assertEqual(
                    (parsed["cover_target"], parsed["cover_position"]), expected)

    def test_open_verb_is_openness(self):
        parsed = _parse("open the sink blind to 30 percent")
        self.assertEqual(parsed["cover_position"], 30)
        raised = _parse("raise the blinds to 90 percent")
        self.assertEqual(raised["cover_position"], 90)

    def test_recorded_sliding_door_failures_and_natural_variants(self):
        for phrase, position in (
            ("Close the sliding door twenty percent.", 80),
            ("Close the sliding window twenty percent.", 80),
            ("Close the large sliding window, 20%.", 80),
            ("close the sliding door 20", 80),
            ("set the sliding door 50%", 50),
            ("set the sliding door shade to 50 percent", 50),
            ("set the sliding door blinds to fifty percent", 50),
            ("open the kitchen sliding window eighty five percent", 85),
            ("please close the sliding door fifty percent please", 50),
        ):
            for sat in ("kitchen", "familyroom", "master"):
                with self.subTest(phrase=phrase, sat=sat):
                    parsed = _parse(phrase, sat)
                    self.assertIsNotNone(parsed)
                    self.assertEqual((parsed["cover_target"], parsed["cover_position"]),
                                     ("blind_slider", position))

    def test_unsupported_levels_and_unknown_targets_do_not_parse(self):
        for phrase in (
            "close the sliding window by 20",
            "close the sliding door 130 percent",
            "close the sliding door to -20%",
            "close the sliding door to 20.5%",
            "set the garage door 50 percent",
            "set the sliding widow 50 percent",
        ):
            with self.subTest(phrase=phrase):
                self.assertIsNone(_parse(phrase))

    def test_halfway_forms(self):
        for text in ("close the sink blind halfway",
                     "set the sink blind half way",
                     "lower the sink blind halfway"):
            with self.subTest(text=text):
                parsed = _parse(text)
                self.assertEqual(parsed["cover_target"], "blind_sink")
                self.assertEqual(parsed["cover_position"], 50)

    def test_trailing_words_and_filler(self):
        cases = {
            "please set the sink blind to about 30 percent": 30,
            "close the sink blind to 70 percent down": 30,
            "set the sink blind to 40 percent open": 40,
            "could you lower the sink blind to 60 please": 40,
        }
        for text, position in cases.items():
            with self.subTest(text=text):
                self.assertEqual(_parse(text)["cover_position"], position)

    def test_trailing_state_word_beats_the_verb(self):
        """2026-11-25: "set the sliding door to 30 closed" moved it to 30 open."""
        cases = {
            "set the sliding door to 30 closed": 70,
            "set the sliding door to 30 percent close": 70,
            "set the sink blind to 20 down": 80,
            "close the sink blind to 70 percent open": 70,
            "close the sink blind to 70 percent closed": 30,
            "set the sink blind halfway closed": 50,
        }
        for text, position in cases.items():
            with self.subTest(text=text):
                self.assertEqual(_parse(text)["cover_position"], position)

    def test_recorded_2026_11_25_family_room_misses(self):
        for phrase, position in (
            ("Okay, Peter set the sliding kitchen blind to 50% close.", 50),
            ("Okay, Peter set the sliding kitchen blind to 30% close.", 70),
            ("set the sliding door to 50 closed", 50),
            ("set the sliding blind to 40 percent", 40),
        ):
            with self.subTest(phrase=phrase):
                parsed = _parse(phrase, "familyroom")
                self.assertIsNotNone(parsed)
                self.assertEqual((parsed["cover_target"], parsed["cover_position"]),
                                 ("blind_slider", position))

    def test_wake_echo_strip_still_needs_a_verb(self):
        for phrase in ("Okay, Peter the sliding door to 50 percent",
                       "Peter set the sliding door to 50 percent",
                       "okay maybe peter set the sliding door to 50"):
            with self.subTest(phrase=phrase):
                self.assertIsNone(_parse(phrase))

    def test_plain_open_close_still_belongs_to_home_control(self):
        """No level named -> this grammar must not fire at all, or it would
        steal the curated buttons."""
        for text in ("close the blinds", "open the kitchen blinds",
                     "close the sink blind", "fix the glare",
                     "close the big one", "open the sliding door"):
            with self.subTest(text=text):
                self.assertIsNone(_parse(text))

    def test_fails_closed(self):
        for text in ("set the sink blind to 130 percent",
                     "set the sink blind to a lot",
                     "set the fireplace to 30 percent",
                     "set the thermostat to 68",
                     "set a timer for 30 minutes",
                     "turn the music up to 40 percent"):
            with self.subTest(text=text):
                self.assertIsNone(_parse(text))


class CoverTargetTest(unittest.TestCase):
    """Room scoping: "the blind" is a different window in each room."""

    def test_bare_blind_is_the_local_one(self):
        self.assertEqual(_parse("close the blind halfway", "master")
                         ["cover_target"], "blind_bath")
        self.assertEqual(_parse("close the blind halfway", "simon")
                         ["cover_target"], "blind_simon")
        self.assertEqual(_parse("close the blinds halfway", "kitchen")
                         ["cover_target"], "blinds_all")
        self.assertEqual(_parse("close the blind halfway", "claire")
                         ["cover_target"], "blind_claire")

    def test_naming_a_kids_room_reaches_it_from_anywhere(self):
        self.assertEqual(_parse("close claire's blind halfway", "kitchen")
                         ["cover_target"], "blind_claire")
        self.assertEqual(_parse("open simon's blind to 80", "claire")
                         ["cover_target"], "blind_simon")

    def test_family_room_blinds_by_mic_and_by_name(self):
        self.assertEqual(_parse("set the blinds to 50", "familyroom")["cover_target"],
                         "blinds_family")
        self.assertEqual(_parse("set the blinds to 50", "kitchen")["cover_target"],
                         "blinds_all")
        self.assertEqual(_parse("open the family room blinds to 30", "kitchen")["cover_target"],
                         "blinds_family")
        self.assertEqual(_parse("close the family room blinds to 80", "master")["cover_target"],
                         "blinds_family")
        self.assertEqual(_parse("set the kitchen blinds to 40", "familyroom")["cover_target"],
                         "blinds_all")
        self.assertEqual(covers.entities("blinds_family"),
                         ["cover.family_room_family_room_serena",
                          "cover.family_room_small_shade"])

    def test_naming_the_kitchen_reaches_it_from_anywhere(self):
        parsed = _parse("set the kitchen blinds to 40 percent", "master")
        self.assertEqual(parsed["cover_target"], "blinds_all")

    def test_resolution_is_exact_not_fuzzy(self):
        self.assertIsNone(covers.resolve("sunk blind", "kitchen"))
        self.assertEqual(covers.resolve("the sink blind", "kitchen"),
                         "blind_sink")

    def test_every_target_names_real_entities(self):
        for key in covers._TARGETS:
            with self.subTest(key=key):
                ents = covers.entities(key)
                self.assertTrue(ents)
                self.assertTrue(all(e.startswith("cover.") for e in ents))


class CoverHandleTest(unittest.TestCase):
    def setUp(self):
        patcher = patch.object(covers, "_set_position", new=AsyncMock())
        self.call = patcher.start()
        self.addCleanup(patcher.stop)

    def test_sets_position_on_every_entity_of_the_target(self):
        parsed = _parse("set the kitchen blinds to 30 percent")
        result = asyncio.run(covers.handle(parsed))
        self.assertTrue(result["ok"])
        self.assertIn("30 percent open", result["response"])
        entities, position = self.call.await_args.args
        self.assertEqual(position, 30)
        self.assertEqual(sorted(entities), sorted([
            "cover.kitchen_left_shade", "cover.kitchen_right_shade",
            "cover.sink_shade", "cover.kitchen_sliding_kitchen_door"]))

    def test_unknown_target_moves_nothing(self):
        self.assertIsNone(asyncio.run(covers.handle(
            {"cover_target": "blind_nowhere", "cover_position": 30})))
        self.assertIsNone(asyncio.run(covers.handle(
            {"cover_target": "blind_sink", "cover_position": None})))
        self.call.assert_not_awaited()


class CoverLevelDispatchTest(_SlotTestBase):
    """Exercise the real routing with every device call mocked."""

    def setUp(self):
        super().setUp()
        token = app._CUR_SAT.set("kitchen")
        self.addCleanup(app._CUR_SAT.reset, token)

    def test_percentage_moves_only_the_slider_and_skips_classifier(self):
        for phrase, position in (
            ("close the sliding door 20", 80),
            ("close the large sliding window, 20%", 80),
            ("set the sliding door 50 percent", 50),
        ):
            with (self.subTest(phrase=phrase),
                  patch.object(covers, "_set_position", new=AsyncMock()) as move,
                  patch.object(home_control, "_press", new=AsyncMock()) as press,
                  patch.object(intent, "parse", new=AsyncMock()) as classify):
                result = self._run(phrase)
                self.assertTrue(result["ok"])
                self.assertEqual(result["intent"], "cover_set")
                move.assert_awaited_once_with(
                    ["cover.kitchen_sliding_kitchen_door"], position)
                press.assert_not_awaited()
                classify.assert_not_awaited()

    def test_unsupported_percentage_cannot_fall_through_to_buttons(self):
        for phrase in ("close the sliding window by 20",
                       "close the sliding door 130%",
                       "close the sliding door to -20%",
                       "close the sliding door to 20.5%",
                       "close the sliding widow to 50%"):
            with (self.subTest(phrase=phrase),
                  patch.object(covers, "_set_position", new=AsyncMock()) as move,
                  patch.object(home_control, "_press", new=AsyncMock()) as press,
                  patch.object(intent, "parse", new=AsyncMock(return_value=
                      intent.validate({"intent": "home_control",
                                       "query": "close the sliding door"})))):
                result = self._run(phrase)
                self.assertFalse(result["ok"])
                move.assert_not_awaited()
                press.assert_not_awaited()

    def test_classifier_cannot_drop_the_original_number(self):
        with patch.object(home_control, "_press", new=AsyncMock()) as press:
            result = asyncio.run(home_control.handle(
                {"query": "close the sliding door"},
                "close the sliding door 20", "kitchen"))
            self.assertFalse(result["ok"])
            press.assert_not_awaited()

    def test_phone_tester_and_fuzzy_rescue_do_not_offer_full_close(self):
        for phrase in ("close the sliding door 20", "close simons blind to 20",
                       "close the sliding window by 20"):
            with self.subTest(phrase=phrase):
                self.assertFalse(home_control.has_exact_match(phrase, "kitchen"))
                self.assertIsNone(home_control.fuzzy_match(phrase, "kitchen"))
                self.assertFalse(home_control.evaluate(phrase)["matched"])

    def test_plain_close_and_named_targets_still_work(self):
        for phrase in ("close the sliding door", "close the sliding window",
                       "close the big one"):
            with (self.subTest(phrase=phrase),
                  patch.object(home_control, "_press", new=AsyncMock()) as press):
                result = asyncio.run(home_control.handle({}, phrase, "kitchen"))
                self.assertTrue(result["ok"])
                press.assert_awaited_once_with("button.voice_blind_slider_close")
        for phrase in ("close the two small blinds", "close the big one",
                       "why is the sliding door 50 percent open"):
            self.assertFalse(covers.has_level_request(phrase))
