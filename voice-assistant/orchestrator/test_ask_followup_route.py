"""Routing a follow-up that comes right after a knowledge answer (2026-11-20).

The local intent classifier only sees a one-line summary of the last answer
and dropped "what about with the skin on" as not-for-us. Such a follow-up now
goes to Claude, which holds the whole exchange and returns one of three
verdicts: an answer, "not for me" (silent drop) or "that's a command" (the
classifier takes it, as before). Deterministic matches still win outright.
Nothing here touches a network.
"""

import time
import unittest
from unittest.mock import AsyncMock, patch

from . import app, config


async def _passthrough(result, intent):
    return result


class AskFollowupRouteTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        app._CUR_SAT.set("kitchen")
        # Reset in place: other tests hold a reference to the kitchen session.
        app._session().update(app._new_session())
        self.events = AsyncMock()
        self.parse = AsyncMock(return_value=app.intent_mod.validate({"intent": "none"}))
        self.ask = AsyncMock()
        for p in (patch.object(config, "ASK_BACKEND", "claude"),
                  patch.object(config, "ASK_FOLLOWUP_DIRECT", True),
                  patch.object(app, "_turn_event", new=self.events),
                  patch.object(app, "_finalize", new=_passthrough),
                  patch.object(app.events, "on_dashboard", return_value=False),
                  patch.object(app.intent_mod, "parse", new=self.parse),
                  patch.object(app.home_mod, "fuzzy_match", return_value=None),
                  patch.object(app.ask_mod, "handle_ask", new=self.ask)):
            p.start()
            self.addCleanup(p.stop)
        self.addCleanup(lambda: app._session().update(app._new_session()))

    def _after_ask(self):
        app.session_note("you asked: how to serve apple", "ask")

    async def test_claude_answers_the_followup_without_the_classifier(self):
        self._after_ask()

        async def answered(query, sat, *, followup, on_accept):
            await on_accept()
            return {"response": "Skin on is fine.", "full": "Thin slices.", "ok": True}

        self.ask.side_effect = answered
        r = await app.handle_command("what about with the skin on", followup=True)
        self.assertEqual(r["intent"], "ask")
        self.assertEqual(r["response"], "Skin on is fine.")
        self.parse.assert_not_awaited()
        self.ask.assert_awaited_once()                  # not asked a second time
        self.assertTrue(self.ask.await_args.kwargs["followup"])
        shown = [c.args[0] for c in self.events.await_args_list]
        self.assertEqual(shown, ["transcript", "thinking", "ask_thinking"])

    async def test_not_for_me_is_dropped_silently(self):
        self._after_ask()
        self.ask.return_value = {"escape": "none", "response": "", "ok": False}
        r = await app.handle_command("come on kids do you want food", followup=True)
        self.assertEqual(r["intent"], "none")
        self.assertTrue(r["silent"])
        self.parse.assert_not_awaited()
        self.events.assert_not_awaited()

    async def test_command_verdict_goes_to_the_classifier(self):
        self._after_ask()
        self.ask.return_value = {"escape": "command", "response": "", "ok": False}
        self.parse.return_value = app.intent_mod.validate({"intent": "unclear"})
        with patch.object(app.intent_mod, "fast_parse", return_value=None):
            r = await app.handle_command("make the kitchen a bit cozier", followup=True)
        self.parse.assert_awaited_once()
        self.ask.assert_awaited_once()
        self.assertEqual(r["intent"], "unclear")        # the classifier's call stands
        shown = [c.args[0] for c in self.events.await_args_list]
        self.assertEqual(shown, ["transcript", "thinking"])   # shown once, by the normal path

    async def test_full_sentence_command_needs_no_model(self):
        self._after_ask()
        fast = app.intent_mod.validate({"intent": "none"})
        with patch.object(app.intent_mod, "fast_parse", return_value=fast) as fp:
            r = await app.handle_command("set a timer for ten minutes", followup=True)
        fp.assert_called_once()
        self.ask.assert_not_awaited()
        self.parse.assert_not_awaited()
        self.assertEqual(r["intent"], "none")

    async def test_followup_after_a_non_ask_turn_uses_the_classifier(self):
        app.session_note("you set a timer", "set_timer")
        await app.handle_command("make it fifteen", followup=True)
        self.ask.assert_not_awaited()
        self.parse.assert_awaited_once()

    async def test_stale_answer_uses_the_classifier(self):
        self._after_ask()
        app._session()["ts"] = time.time() - app.SESSION_TTL_S - 5
        await app.handle_command("what about with the skin on", followup=True)
        self.ask.assert_not_awaited()
        self.parse.assert_awaited_once()

    async def test_off_switches(self):
        for flag, val in (("ASK_BACKEND", "openrouter"), ("ASK_FOLLOWUP_DIRECT", False)):
            with self.subTest(flag=flag):
                self._after_ask()
                self.ask.reset_mock()
                self.parse.reset_mock()
                with patch.object(config, flag, val):
                    await app.handle_command("what about with the skin on", followup=True)
                self.ask.assert_not_awaited()
                self.parse.assert_awaited_once()

    async def test_wake_turn_after_an_answer_is_unchanged(self):
        self._after_ask()
        self.parse.return_value = app.intent_mod.validate({"intent": "unclear"})
        with patch.object(app.intent_mod, "fast_parse", return_value=None):
            await app.handle_command("what about with the skin on")
        self.parse.assert_awaited_once()
        self.ask.assert_not_awaited()

    async def test_answered_followup_keeps_the_chain_open(self):
        self._after_ask()
        self.assertTrue(app.session_last_was_ask())
        app.session_note("you set a timer", "set_timer")
        self.assertFalse(app.session_last_was_ask())


if __name__ == "__main__":
    unittest.main()
