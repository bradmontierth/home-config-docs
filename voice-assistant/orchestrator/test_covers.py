import asyncio
import unittest
from unittest.mock import AsyncMock, patch

from . import covers, intent


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

    def test_close_verb_inverts_the_number(self):
        """"Close it to 80%" means 80% down -- HA position 20."""
        cases = {
            "close the sink blind to 80 percent": ("blind_sink", 20),
            "lower the kitchen blinds to 70 percent": ("blinds_all", 30),
            "drop the left blind to 60": ("blind_left", 40),
            "close the blinds 25 percent": None,     # no anchor -> classifier
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
