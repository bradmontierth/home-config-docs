import asyncio
import unittest
from unittest.mock import AsyncMock, patch

from . import climate, intent


def _parse(command: str, sat: str | None = "simon"):
    return intent.fast_parse_climate_setpoint(command, sat)


class ClimateSetpointParseTest(unittest.TestCase):
    """The grammar. A thermostat word (or a degree word) plus a number in the
    room band, resolved to the room's own mini split."""

    def test_named_thermostat_phrasings(self):
        cases = {
            "set temperature to 72": 72,
            "set the temperature to 72": 72,
            "set the temperature to 72 degrees": 72,
            "set the thermostat to seventy two": 72,
            "set the ac to 70": 70,
            "set the heat to 68": 68,
            "set the mini split to 72": 72,
            "turn the temperature down to 68": 68,
            "change the temperature to 74": 74,
            "could you set the temperature to 72 please": 72,
            "set the temp at 71": 71,
            "make it 70 degrees in here": 70,
            "keep it at 70 degrees": 70,
            "72 degrees": 72,
            "temperature 72": 72,
            "Set temperature to 72.": 72,
        }
        for text, setpoint in cases.items():
            with self.subTest(text=text):
                parsed = _parse(text)
                self.assertIsNotNone(parsed, text)
                self.assertEqual(parsed["intent"], "climate_set")
                self.assertEqual(parsed["climate_target"], "simon")
                self.assertEqual(parsed["climate_setpoint"], setpoint)

    def test_pronoun_forms_take_the_bedroom_band_only(self):
        self.assertEqual(_parse("set it to 72", "claire")["climate_setpoint"], 72)
        self.assertEqual(_parse("turn it down to 68", "claire")["climate_setpoint"], 68)
        self.assertEqual(_parse("make it 72 in here", "claire")["climate_setpoint"], 72)
        self.assertEqual(_parse("put it on 72", "claire")["climate_setpoint"], 72)
        # Out of the 60-80 band a bare pronoun is somebody else's number.
        self.assertIsNone(_parse("set it to 40", "claire"))
        self.assertIsNone(_parse("set it to 90", "claire"))
        self.assertIsNone(_parse("set it to 85", "claire"))

    def test_named_thermostat_over_ask_is_parsed_for_clamping(self):
        """"Set the temperature to 90" must reach the handler, which clamps
        and says so -- not fall through to a refusal."""
        self.assertEqual(_parse("set the temperature to 90")["climate_setpoint"], 90)
        self.assertEqual(_parse("set the heat to 55")["climate_setpoint"], 55)
        self.assertIsNone(_parse("set the temperature to 120"))

    def test_other_numbers_never_read_as_temperature(self):
        for text in ("set the fan to 50", "set the fan to 72", "set the blinds to 70",
                     "set the blind to 72", "set the volume to 60",
                     "set a timer for 72 minutes", "what's the temperature in here",
                     "make it pac man", "72", "turn it up"):
            with self.subTest(text=text):
                self.assertIsNone(_parse(text))

    def test_rooms_without_a_mini_split_fall_through(self):
        for sat in ("kitchen", "familyroom", "master", None):
            with self.subTest(sat=sat):
                self.assertIsNone(_parse("set the temperature to 72", sat))

    def test_named_room_wins_from_anywhere(self):
        cases = {
            "set simon's room to 70": ("simon", 70),
            "set the temperature in claire's room to 70": ("claire", 70),
            "set claire's temperature to 70": ("claire", 70),
            "make it 72 in simon's room": ("simon", 72),
        }
        for text, (target, setpoint) in cases.items():
            with self.subTest(text=text):
                parsed = _parse(text, "kitchen")
                self.assertIsNotNone(parsed, text)
                self.assertEqual(parsed["climate_target"], target)
                self.assertEqual(parsed["climate_setpoint"], setpoint)
        # Standing in one kid's room, naming the other still means the other.
        self.assertEqual(_parse("set claire's room to 70", "simon")["climate_target"], "claire")

    def test_cover_grammar_still_owns_blind_levels(self):
        parsed = intent.fast_parse_cover_level("set the blind to 70", "simon")
        self.assertEqual(parsed["intent"], "cover_set")
        self.assertEqual(parsed["cover_position"], 70)

    def test_validate_drops_slots_off_intent(self):
        parsed = intent.validate({"intent": "ask", "query": "hi",
                                  "climate_target": "simon", "climate_setpoint": 72})
        self.assertIsNone(parsed["climate_target"])
        self.assertIsNone(parsed["climate_setpoint"])
        parsed = intent.validate({"intent": "climate_set",
                                  "climate_target": "garage", "climate_setpoint": 72})
        self.assertIsNone(parsed["climate_target"])


def _state(mode, cur, target=None, lo=61.0, hi=79.0):
    return {"state": mode, "attributes": {
        "current_temperature": cur, "temperature": target,
        "min_temp": lo, "max_temp": hi}}


class ClimateDecideTest(unittest.TestCase):
    """The mode rule: conditioning keeps its mode, an idle unit is switched on
    in the direction of the ask, and the device range clamps."""

    def test_conditioning_keeps_mode(self):
        for mode in ("cool", "heat", "auto", "heat_cool"):
            with self.subTest(mode=mode):
                data, applied, on = climate.decide(_state(mode, 71.5, 73.5), 72)
                self.assertEqual(data, {"temperature": 72.0})
                self.assertEqual(applied, 72.0)
                self.assertIsNone(on)

    def test_idle_unit_picks_mode_from_the_room(self):
        for mode in ("off", "dry", "fan_only", "unavailable", ""):
            with self.subTest(mode=mode):
                data, _, on = climate.decide(_state(mode, 80.0), 72)
                self.assertEqual(on, "cool")
                self.assertEqual(data, {"temperature": 72.0, "hvac_mode": "cool"})
                data, _, on = climate.decide(_state(mode, 64.0), 70)
                self.assertEqual(on, "heat")
                self.assertEqual(data["hvac_mode"], "heat")
        # No reading: cool is the default ask here.
        _, _, on = climate.decide({"state": "off", "attributes": {}}, 72)
        self.assertEqual(on, "cool")

    def test_clamps_to_device_range(self):
        data, applied, _ = climate.decide(_state("cool", 80.0, 75.0), 90)
        self.assertEqual(applied, 79.0)
        data, applied, _ = climate.decide(_state("heat", 60.0, 65.0), 55)
        self.assertEqual(applied, 61.0)


class ClimateHandleTest(unittest.TestCase):
    def _run(self, parsed, state):
        with patch.object(climate, "_state", new=AsyncMock(return_value=state)), \
                patch.object(climate, "_set_temperature", new=AsyncMock()) as set_t:
            result = asyncio.run(climate.handle(parsed))
        return result, set_t

    def test_sets_and_confirms(self):
        result, set_t = self._run({"climate_target": "claire", "climate_setpoint": 72},
                                  _state("cool", 71.5, 73.5))
        set_t.assert_awaited_once_with("climate.clairehvac2_my_heat_pump",
                                       {"temperature": 72.0})
        self.assertEqual(result["response"], "Setting the temperature to 72.")
        self.assertTrue(result["ok"])

    def test_turns_on_and_says_which_way(self):
        result, set_t = self._run({"climate_target": "simon", "climate_setpoint": 72},
                                  _state("off", 80.0))
        set_t.assert_awaited_once_with("climate.hvac100_my_heat_pump",
                                       {"temperature": 72.0, "hvac_mode": "cool"})
        self.assertEqual(result["response"], "Turning on the AC and setting it to 72.")
        result, _ = self._run({"climate_target": "simon", "climate_setpoint": 70},
                              _state("off", 64.0))
        self.assertEqual(result["response"], "Turning on the heat and setting it to 70.")

    def test_clamp_is_spoken(self):
        result, _ = self._run({"climate_target": "simon", "climate_setpoint": 90},
                              _state("cool", 80.0, 75.0))
        self.assertEqual(result["response"],
                         "Setting the temperature to 79 — that's as high as it goes.")
        result, _ = self._run({"climate_target": "simon", "climate_setpoint": 55},
                              _state("heat", 60.0, 65.0))
        self.assertIn("as low as it goes", result["response"])

    def test_unknown_target_is_a_miss(self):
        result, set_t = self._run({"climate_target": "garage", "climate_setpoint": 72},
                                  _state("cool", 70.0, 70.0))
        self.assertIsNone(result)
        set_t.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
