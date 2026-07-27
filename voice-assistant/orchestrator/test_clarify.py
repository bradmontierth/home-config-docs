"""Timer slot-fill wiring in handle_command.

The pieces the unit tests in test_intent_slots.py can't reach: that the slot
question arms a pending clarify, that the answer stitches onto it, that only a
FOLLOW-UP turn is allowed to stitch, and that we never ask twice.

Reproduces the live 2026-07-26 sequence end to end with the classifier stubbed:
"set the timer for" -> "unclear" -> (rescued) "Sure, for how long?" ->
"Eight minutes." -> an 8 minute timer.
"""

import asyncio
import os
import shutil
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

from . import app, clients, config, intent, timers


def _parsed(**over):
    """A validated parse result with the given fields overridden."""
    return intent.validate({"intent": "none", **over})


class ClarifyFlowTest(unittest.TestCase):

    def setUp(self):
        # TimerEngine is sqlite-backed, so give each test its own database —
        # otherwise tests share timers and, run in the live container, would
        # write into the real one.
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, True)
        for attr, path in (("DB_PATH", os.path.join(tmp, "timers.db")),
                           ("ANNOUNCE_CACHE_DIR", os.path.join(tmp, "audio"))):
            p = patch.object(config, attr, path)
            p.start()
            self.addCleanup(p.stop)
        app.ENGINE = timers.TimerEngine(on_expire=AsyncMock())
        app._SESSION.update({"ts": 0.0, "summary": "", "last_added": [],
                             "pending": None})
        # Silence the outside world: no dashboard events, no spoken reply, and
        # no TTS at all — TimerEngine.create pre-renders each timer's
        # announcement, which otherwise makes these tests hit the live server.
        for target, attr, ret in ((app, "_speak_reply", None),
                                  (app.events, "emit", None),
                                  (clients, "synthesize", b"")):
            p = patch.object(target, attr, AsyncMock(return_value=ret))
            p.start()
            self.addCleanup(p.stop)

    def _run(self, command, followup=False):
        return asyncio.run(app.handle_command(command, followup=followup))

    # -- the live failure, end to end ------------------------------------
    def test_truncated_timer_asks_then_completes(self):
        # Turn 1: the classifier calls it "unclear" (as it did live); the
        # deterministic rescue turns it into a timer missing its duration.
        with patch.object(app.intent_mod, "parse",
                          AsyncMock(return_value=_parsed(intent="unclear"))):
            first = self._run("set the timer for")
        self.assertEqual(first["intent"], "set_timer")
        self.assertEqual(first["response"], "Sure, for how long?")
        self.assertTrue(first["awaiting_slot"])
        self.assertEqual(app.session_pending()["op"], "clarify")
        self.assertEqual(app.ENGINE.active(), [])

        # Turn 2: the answer alone is a fragment. It must NOT reach the plain
        # parser (that is what dropped it live) — the duration fast path reads
        # it without an LLM call at all.
        with patch.object(app.intent_mod, "parse", AsyncMock()) as plain, \
             patch.object(app.intent_mod, "parse_clarify", AsyncMock()) as slow:
            second = self._run("Eight minutes.", followup=True)
        plain.assert_not_called()
        slow.assert_not_called()
        self.assertEqual(second["intent"], "set_timer")
        self.assertTrue(second["ok"])
        self.assertEqual(second["timer"]["duration_seconds"], 480)
        self.assertIsNone(app.session_pending())   # slot consumed

    def test_label_and_theme_survive_the_fast_path(self):
        # The first parse worked out "chicken"/cluck; answering with a bare
        # duration must not lose them.
        with patch.object(app.intent_mod, "parse", AsyncMock(
                return_value=_parsed(intent="set_timer", label="chicken",
                                     sound_theme="cluck"))):
            first = self._run("set a chicken timer for")
        self.assertEqual(first["response"], "Sure, how long for the chicken?")

        second = self._run("twelve minutes", followup=True)
        self.assertEqual(second["timer"]["duration_seconds"], 720)
        self.assertEqual(second["timer"]["label"], "chicken")
        self.assertEqual(second["timer"]["sound_theme"], "cluck")

    def test_unrecognised_answer_goes_to_the_stitching_parser(self):
        # "until the pasta is done" isn't a duration the fast path can read, so
        # it goes to the LLM with the partial command stitched back on.
        with patch.object(app.intent_mod, "parse", AsyncMock(
                return_value=_parsed(intent="set_timer"))):
            self._run("set a timer for")

        with patch.object(app.intent_mod, "parse_clarify", AsyncMock(
                return_value=_parsed(intent="set_timer",
                                     duration_seconds=600))) as slow:
            result = self._run("however long the pasta takes", followup=True)
        slow.assert_awaited_once()
        partial, reply, question = slow.await_args.args
        self.assertEqual(partial, "set a timer for")
        self.assertEqual(reply, "however long the pasta takes")
        self.assertEqual(question, "Sure, for how long?")
        self.assertEqual(result["timer"]["duration_seconds"], 600)

    def test_answer_can_abandon_the_timer(self):
        # She changed her mind. The reply is parsed on its own merits and the
        # timer request is dropped — no timer, no second question.
        with patch.object(app.intent_mod, "parse", AsyncMock(
                return_value=_parsed(intent="set_timer"))):
            self._run("set a timer for")

        with patch.object(app.intent_mod, "parse_clarify", AsyncMock(
                return_value=_parsed(intent="music_control",
                                     music_action="pause"))), \
             patch.object(app.music_mod, "control",
                          AsyncMock(return_value={"response": "Paused.", "ok": True})):
            result = self._run("actually pause the music", followup=True)
        self.assertEqual(result["intent"], "music_control")
        self.assertEqual(app.ENGINE.active(), [])
        self.assertIsNone(app.session_pending())

    def test_never_asks_twice(self):
        # A second "for how long?" would be a loop. One round, then let go.
        with patch.object(app.intent_mod, "parse", AsyncMock(
                return_value=_parsed(intent="set_timer"))):
            self._run("set a timer for")

        with patch.object(app.intent_mod, "parse_clarify", AsyncMock(
                return_value=_parsed(intent="set_timer"))):
            result = self._run("hmm, hang on", followup=True)
        self.assertEqual(result["response"], "Okay, never mind.")
        self.assertFalse(result["ok"])
        self.assertIsNone(app.session_pending())
        self.assertEqual(app.ENGINE.active(), [])

    def test_wake_word_starts_over_instead_of_stitching(self):
        # She gave up on the question and re-asked with the wake word. Stitching
        # her new command onto the abandoned partial would produce nonsense, so
        # a non-follow-up turn drops the slot and parses normally.
        with patch.object(app.intent_mod, "parse", AsyncMock(
                return_value=_parsed(intent="set_timer"))):
            self._run("set a timer for")

        with patch.object(app.intent_mod, "parse", AsyncMock(
                return_value=_parsed(intent="weather", weather_when="now"))) as plain, \
             patch.object(app.intent_mod, "parse_clarify", AsyncMock()) as slow, \
             patch.object(app.weather_mod, "handle", AsyncMock(
                 return_value={"response": "It's 72 degrees.", "ok": True})):
            result = self._run("what's the weather")
        plain.assert_awaited_once()
        slow.assert_not_called()
        self.assertEqual(result["intent"], "weather")
        self.assertIsNone(app.session_pending())

    def test_complete_timer_never_arms_a_slot(self):
        with patch.object(app.intent_mod, "parse", AsyncMock(
                return_value=_parsed(intent="set_timer", duration_seconds=480))):
            result = self._run("set a timer for eight minutes")
        self.assertTrue(result["ok"])
        self.assertNotIn("awaiting_slot", result)
        self.assertIsNone(app.session_pending())

    def test_slot_expires_with_the_session(self):
        with patch.object(app.intent_mod, "parse", AsyncMock(
                return_value=_parsed(intent="set_timer"))):
            self._run("set a timer for")
        app._SESSION["ts"] -= app.SESSION_TTL_S + 1      # walk off the edge
        self.assertIsNone(app.session_pending())

        with patch.object(app.intent_mod, "parse", AsyncMock(
                return_value=_parsed(intent="none"))) as plain, \
             patch.object(app.intent_mod, "parse_clarify", AsyncMock()) as slow:
            self._run("eight minutes", followup=True)
        plain.assert_awaited_once()
        slow.assert_not_called()


if __name__ == "__main__":
    unittest.main()
