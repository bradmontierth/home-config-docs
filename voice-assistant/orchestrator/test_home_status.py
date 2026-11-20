import asyncio
import unittest
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

from . import app, covers, home_control, home_status, intent
from .intent_schema import INTENTS, SCHEMA

NOW = datetime(2026, 10, 3, 14, 0, tzinfo=timezone.utc).timestamp()


def _iso(seconds_ago: float) -> str:
    return datetime.fromtimestamp(NOW - seconds_ago, timezone.utc).isoformat()


def _st(state: str, seconds_ago: float = 3600) -> dict:
    return {"state": state, "last_changed": _iso(seconds_ago)}


def _all_clear() -> dict[str, dict]:
    """Every entity in its secure state, as HA reports it."""
    states = {}
    for group in home_status._GROUPS.values():
        for item in group["items"]:
            secure = {"contact": "off", "contact_lock": "off",
                      "cover": "closed", "lock": "locked"}[item["kind"]]
            states[item["entity"]] = _st(secure)
            if item.get("fallback"):
                states[item["fallback"]] = _st("off")
    return states


def _answer(states, scope="all"):
    return home_status.answer(states, scope, now=NOW)


class ClassifyTest(unittest.TestCase):
    """The confirm-or-refuse rule, one entity at a time."""

    def test_secure_and_insecure_states(self):
        c = lambda kind, s: home_status.classify(kind, _st(s), NOW)[0]
        self.assertEqual(c("contact", "off"), "ok")
        self.assertEqual(c("contact", "on"), "bad")
        self.assertEqual(c("contact_lock", "off"), "ok")
        self.assertEqual(c("contact_lock", "on"), "bad")
        self.assertEqual(c("cover", "closed"), "ok")
        self.assertEqual(c("cover", "open"), "bad")
        self.assertEqual(c("lock", "locked"), "ok")
        self.assertEqual(c("lock", "unlocked"), "bad")
        self.assertEqual(c("lock", "jammed"), "jammed")

    def test_unknown_is_never_ok(self):
        for kind in ("contact", "contact_lock", "cover", "lock"):
            for value in ("unavailable", "unknown", "", "banana"):
                with self.subTest(kind=kind, value=value):
                    self.assertEqual(
                        home_status.classify(kind, _st(value), NOW)[0], "unknown")
            self.assertEqual(home_status.classify(kind, None, NOW)[0], "unknown")

    def test_in_between_state_is_moving_only_while_fresh(self):
        self.assertEqual(home_status.classify("cover", _st("closing", 10), NOW),
                         ("moving", "closing"))
        self.assertEqual(home_status.classify("lock", _st("locking", 5), NOW),
                         ("moving", "locking"))
        # The 2026-09-27 Nest x Yale case: "locking" for six days.
        self.assertEqual(
            home_status.classify("lock", _st("locking", 6 * 86400), NOW)[0], "unknown")
        self.assertEqual(
            home_status.classify("cover", _st("opening", 600), NOW)[0], "unknown")
        self.assertEqual(
            home_status.classify("cover", {"state": "closing"}, NOW)[0], "unknown")


class AnswerTest(unittest.TestCase):

    def test_all_clear_is_secure(self):
        out = _answer(_all_clear())
        self.assertTrue(out["secure"])
        self.assertEqual(out["response"],
                         "Everything's secure. The garage, doors, windows, and gates "
                         "are closed, and the locks are locked.")

    def test_loading_the_car(self):
        """The case that prompted this: big garage door up, two windows open."""
        states = _all_clear()
        states["cover.big_garage_relay_door"] = _st("open")
        states["binary_sensor.office_window_contact"] = _st("on")
        states["binary_sensor.family_room_window_contact"] = _st("on")
        out = _answer(states)
        self.assertFalse(out["secure"])
        self.assertEqual(out["response"],
                         "The big garage door is open. Two windows are open: the "
                         "family room and the office. The doors and gates are closed "
                         "and the locks are locked.")
        self.assertNotIn("secure", out["response"])

    def test_todays_locks_are_named_not_vouched_for(self):
        """Live 2026-10-03: three Nest x Yale locks stuck on "locking", the
        slider's bolt contact fine."""
        states = _all_clear()
        for e in ("lock.entryway_lock", "lock.backyard_lock", "lock.garage_lock"):
            states[e] = _st("locking", 6 * 86400)
        out = _answer(states)
        self.assertFalse(out["secure"])
        self.assertEqual(out["response"],
                         "I can't confirm the entryway, the backyard, and the garage "
                         "locks. The garage, doors, windows, and gates are closed.")
        self.assertEqual(_answer(states, "locks")["response"],
                         "I can't confirm the entryway, the backyard, and the garage "
                         "locks. The sliding door lock is locked.")

    def test_missing_entity_is_unknown(self):
        states = _all_clear()
        del states["binary_sensor.front_door_contact"]
        out = _answer(states, "doors")
        self.assertFalse(out["secure"])
        self.assertEqual(out["response"],
                         "I can't confirm the front door. The other three are closed.")

    def test_whole_group_unknown_collapses(self):
        states = _all_clear()
        for item in home_status._GROUPS["windows"]["items"]:
            states[item["entity"]] = _st("unavailable")
        self.assertEqual(_answer(states, "windows")["response"],
                         "I can't confirm the windows.")

    def test_garage_closing_and_fallback(self):
        states = _all_clear()
        states["cover.big_garage_relay_door"] = _st("closing", 8)
        self.assertEqual(_answer(states, "garage")["response"],
                         "The big garage door is closing. The small garage door is closed.")
        # The relay is offline: the contact on the door still answers.
        states = _all_clear()
        states["cover.small_garage_door_door"] = _st("unavailable")
        states["binary_sensor.small_garage_door_contact"] = _st("on")
        self.assertEqual(_answer(states, "garage")["response"],
                         "The small garage door is open. The big garage door is closed.")
        states["binary_sensor.small_garage_door_contact"] = _st("unavailable")
        self.assertEqual(_answer(states, "garage")["response"],
                         "I can't confirm the small garage door. The big garage door is closed.")

    def test_narrow_scopes(self):
        states = _all_clear()
        self.assertEqual(_answer(states, "windows")["response"],
                         "All twelve windows are closed.")
        self.assertEqual(_answer(states, "locks")["response"],
                         "All four locks are locked.")
        self.assertEqual(_answer(states, "garage")["response"],
                         "Both garage doors are closed.")
        self.assertEqual(_answer(states, "gates")["response"],
                         "Both gates are closed.")
        states["binary_sensor.sliding_door_contact_contact"] = _st("on")
        states["binary_sensor.front_door_contact"] = _st("on")
        self.assertEqual(_answer(states, "doors")["response"],
                         "Two doors are open: the front door and the sliding door. "
                         "The other two are closed.")
        states["lock.entryway_lock"] = _st("unlocked")
        self.assertEqual(_answer(states, "locks")["response"],
                         "The entryway lock is unlocked. The other three are locked.")
        states["lock.entryway_lock"] = _st("jammed")
        self.assertIn("The entryway lock is jammed.", _answer(states, "locks")["response"])

    def test_unknown_scope_widens_to_all(self):
        self.assertEqual(_answer(_all_clear(), "blinds")["scope"], "all")


class FastParseTest(unittest.TestCase):

    CASES = {
        "is the house secure": "all",
        "Is the house secure?": "all",
        "is everything locked up": "all",
        "is the home all closed up": "all",
        "is the house locked": "all",
        "what's the status of the house": "all",
        "what's the house status": "all",
        "house status": "all",
        "is anything open": "all",
        "is there anything left open": "all",
        "did we lock up": "all",
        "are we good to leave": "all",
        "is the garage open": "garage",
        "is the garage door closed": "garage",
        "is the big garage door open": "garage",
        "is the garage still open": "garage",
        "are the garage doors closed": "garage",
        "did I close the garage door": "garage",
        "did someone close the garage": "garage",
        "are the doors locked": "locks",
        "are all the doors locked": "locks",
        "is the front door locked": "locks",
        "are any doors unlocked": "locks",
        "did we lock the doors": "locks",
        "are the doors closed": "doors",
        "are all the doors closed": "doors",
        "are any doors open": "doors",
        "which doors are open": "doors",
        "is the sliding door open": "doors",
        "are the windows closed": "windows",
        "are all the windows closed": "windows",
        "are any windows open": "windows",
        "how many windows are open": "windows",
        "which windows are open right now": "windows",
        "is the gate closed": "gates",
        "are the gates shut": "gates",
        "are any gates open": "gates",
    }

    def test_phrasings(self):
        for text, scope in self.CASES.items():
            with self.subTest(text=text):
                parsed = intent.fast_parse(text)
                self.assertIsNotNone(parsed, text)
                self.assertEqual(parsed["intent"], "home_status")
                self.assertEqual(parsed["status_scope"], scope)

    def test_commands_and_other_questions_do_not_match(self):
        for text in ("close the garage door", "open the garage", "lock the front door",
                     "close the windows", "are the blinds closed",
                     "is the kitchen light on", "is it open", "what's the weather",
                     "is home depot open", "is the sliding door blind closed"):
            with self.subTest(text=text):
                self.assertIsNone(intent.fast_parse_home_status(text), text)

    def test_no_earlier_fast_path_steals_the_question(self):
        """app.handle_command checks curated home aliases, cover levels and
        climate before fast_parse; none may claim a status question."""
        for text in self.CASES:
            for sat in ("kitchen", "familyroom", "simon", "claire", "master", None):
                with self.subTest(text=text, sat=sat):
                    self.assertFalse(home_control.has_exact_match(text, sat))
                    self.assertFalse(covers.has_level_request(text))
                    self.assertIsNone(intent.fast_parse_cover_level(text, sat))
                    self.assertIsNone(intent.fast_parse_climate_setpoint(text, sat))


class ValidateTest(unittest.TestCase):

    def test_scope_slot(self):
        self.assertIn("home_status", INTENTS)
        self.assertIn("status_scope", SCHEMA["properties"])
        v = intent.validate({"intent": "home_status", "status_scope": "windows"})
        self.assertEqual(v["status_scope"], "windows")
        self.assertEqual(intent.validate({"intent": "home_status"})["status_scope"], "all")
        self.assertEqual(intent.validate(
            {"intent": "home_status", "status_scope": "blinds"})["status_scope"], "all")
        self.assertIsNone(intent.validate(
            {"intent": "weather", "status_scope": "windows"})["status_scope"])

    def test_prompt_mentions_intent(self):
        system = intent._system("kitchen")
        self.assertIn("home_status =", system)
        self.assertIn('"status_scope"', system)


class AppWiringTest(unittest.TestCase):

    def _run(self, phrase, fetch):
        parse = AsyncMock()
        token = app._CUR_SAT.set("kitchen")
        self.addCleanup(app._CUR_SAT.reset, token)
        with (
            patch.object(app.intent_mod, "parse", new=parse),
            patch.object(home_status, "_fetch_states", new=fetch),
            patch.object(app, "_speak_reply", new=AsyncMock()),
            patch.object(app.broadcast_mod, "send", new=AsyncMock()),
            patch.object(app.events, "emit", new=AsyncMock()),
        ):
            result = asyncio.run(app.handle_command(phrase))
        parse.assert_not_called()
        return result

    def test_fast_path_answers_from_ha(self):
        states = _all_clear()
        states["cover.big_garage_relay_door"] = _st("open")
        result = self._run("is the garage open",
                           AsyncMock(return_value=states))
        self.assertTrue(result["ok"])
        self.assertEqual(result["response"],
                         "The big garage door is open. The small garage door is closed.")
        self.assertFalse(result["home_status"]["secure"])

    def test_ha_down_refuses(self):
        result = self._run("is the house secure",
                           AsyncMock(side_effect=OSError("connection refused")))
        self.assertFalse(result["ok"])
        self.assertEqual(result["response"], "Sorry, I couldn't check the house sensors.")


if __name__ == "__main__":
    unittest.main()
