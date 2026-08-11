"""Multi-room state and prompt isolation regressions.

No test in this module reaches a network, device, dashboard, or speaker.
"""

import asyncio
import copy
import unittest
from unittest.mock import AsyncMock, patch

from . import app, ask, config, intent


class SessionRoomIsolationTest(unittest.TestCase):
    def setUp(self):
        saved = copy.deepcopy(app._SESSIONS)
        app._SESSION.clear()
        app._SESSION.update(app._new_session())
        app._SESSIONS.clear()
        app._SESSIONS[config.DEFAULT_SAT] = app._SESSION

        def restore():
            app._SESSION.clear()
            app._SESSION.update(saved.get(config.DEFAULT_SAT, app._new_session()))
            app._SESSIONS.clear()
            app._SESSIONS[config.DEFAULT_SAT] = app._SESSION
            for sat, session in saved.items():
                if sat != config.DEFAULT_SAT:
                    app._SESSIONS[sat] = session

        self.addCleanup(restore)

    def _room(self, sat, callback):
        token = app._CUR_SAT.set(sat)
        try:
            return callback()
        finally:
            app._CUR_SAT.reset(token)

    def test_pending_context_and_undo_state_are_room_local(self):
        def seed_kitchen():
            app.session_note("you added kitchen milk")
            app.session_set_added([{"id": 1, "text": "kitchen milk"}])
            app.session_set_pending("remove", [{"id": 1}], "shopping")

        self._room("kitchen", seed_kitchen)

        def inspect_simon_empty():
            self.assertIsNone(app.session_context())
            self.assertEqual(app.session_last_added(), [])
            self.assertIsNone(app.session_pending())

        self._room("simon", inspect_simon_empty)

        def seed_simon():
            app.session_note("you set a Simon timer")
            app.session_set_added([{"id": 2, "text": "Simon item"}])
            app.session_set_clarify("set a timer for", "For how long?")

        self._room("simon", seed_simon)

        def inspect_kitchen_preserved():
            self.assertEqual(app.session_context(), "you added kitchen milk")
            self.assertEqual(app.session_last_added()[0]["id"], 1)
            self.assertEqual(app.session_pending()["op"], "remove")

        self._room("kitchen", inspect_kitchen_preserved)

        def inspect_simon_preserved():
            self.assertEqual(app.session_context(), "you set a Simon timer")
            self.assertEqual(app.session_last_added()[0]["id"], 2)
            self.assertEqual(app.session_pending()["op"], "clarify")

        self._room("simon", inspect_simon_preserved)
        self._room("master", inspect_simon_empty)


class AskRoomIsolationTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.saved_histories = copy.deepcopy(ask._histories)
        self.saved_answers = copy.deepcopy(ask._last_answers)
        ask._histories.clear()
        ask._last_answers.clear()

    def tearDown(self):
        ask._histories.clear()
        ask._histories.update(self.saved_histories)
        ask._last_answers.clear()
        ask._last_answers.update(self.saved_answers)

    async def test_history_and_repeat_answer_are_room_local(self):
        ask.remember("kitchen question", "kitchen answer", sat="kitchen")
        ask.remember("Simon question", "Simon answer", sat="simon")

        kitchen = ask._history_messages("kitchen")
        simon = ask._history_messages("simon")
        master = ask._history_messages("master")
        self.assertEqual([m["content"] for m in kitchen],
                         ["kitchen question", "kitchen answer"])
        self.assertEqual([m["content"] for m in simon],
                         ["Simon question", "Simon answer"])
        self.assertEqual(master, [])

        with patch.object(ask.events, "emit", new=AsyncMock()):
            kitchen_recall = await ask.handle_show_answer("kitchen")
            simon_recall = await ask.handle_show_answer("simon")
            master_recall = await ask.handle_show_answer("master")

        self.assertEqual(kitchen_recall["response"], "kitchen answer")
        self.assertEqual(simon_recall["response"], "Simon answer")
        self.assertFalse(master_recall["ok"])


class RoomPromptTest(unittest.IsolatedAsyncioTestCase):
    async def test_classifier_receives_only_origin_room_context(self):
        raw = '{"intent":"ask","query":"hello"}'
        parse = AsyncMock(return_value=raw)
        with patch.object(intent.clients, "parse_intent_raw", new=parse):
            await intent.parse("hello", sat="simon")

        messages = parse.await_args.args[0]
        system = messages[0]["content"]
        self.assertIn("intent parser for a household voice assistant", system)
        self.assertIn("ORIGIN ROOM: Simon's room", system)
        self.assertNotIn("simon_fun_color", system)
        self.assertNotIn("button.voice_", system)

    def test_knowledge_prompt_is_room_neutral_and_origin_scoped(self):
        system = ask._system("master")
        self.assertIn("knowledge engine for a household voice assistant", system)
        self.assertIn("request originated in master bath", system)
        self.assertNotIn("household kitchen voice assistant", system)


if __name__ == "__main__":
    unittest.main()
