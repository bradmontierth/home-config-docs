"""Shadow-capture rescue (2026-11-20).

Live: Adrienne in the kitchen, "did the kids sleep today". The kitchen won
arbitration, but its ch0 VAD scored her at voiced=0ms and dropped the turn;
the family room's suppressed shadow capture had the whole command. The
winner now calls /command/rescue on an empty capture and the loser's take is
dispatched as its command. Nothing here touches a network.
"""

import asyncio
import os
import tempfile
import time
import unittest
from contextlib import ExitStack
from unittest.mock import AsyncMock, patch

from . import app, clients, config, turns


class _Req:
    def __init__(self, body: bytes) -> None:
        self._body = body

    async def body(self) -> bytes:
        return self._body


class _RecoveryBase(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self._dir = tempfile.TemporaryDirectory()
        self._prev_db = config.DB_PATH
        self._prev_wait = config.RESCUE_WAIT_S
        self._prev_mode = config.SPEAKER_MODE
        config.DB_PATH = os.path.join(self._dir.name, "orchestrator.db")
        config.RESCUE_WAIT_S = 0.5
        config.SPEAKER_MODE = "shadow"
        turns._db = None
        app._SHADOWS.clear()
        app._SHADOW_PENDING.clear()
        self.dispatch = AsyncMock(return_value={
            "intent": "nap_status", "response": "Claire napped twice.", "ok": True})
        patchers = [
            patch.object(app, "_turn_event", new=AsyncMock()),
            patch.object(app, "_dispatch", new=self.dispatch),
            patch.object(app.policy_mod, "evaluate",
                         new=AsyncMock(return_value={"allowed": True})),
        ]
        for p in patchers:
            p.start()
            self.addCleanup(p.stop)

    def tearDown(self) -> None:
        config.DB_PATH = self._prev_db
        config.RESCUE_WAIT_S = self._prev_wait
        config.SPEAKER_MODE = self._prev_mode
        turns._db = None
        app._SHADOWS.clear()
        app._SHADOW_PENDING.clear()
        self._dir.cleanup()

    async def _shadow(self, winner_turn: str, transcript: str) -> None:
        with ExitStack() as stack:
            if hasattr(app, "quality"):     # paired-mic quality obs, when present
                stack.enter_context(patch.object(app.quality, "begin", return_value=None))
            stack.enter_context(patch.object(app.clients, "transcribe",
                                             new=AsyncMock(return_value=transcript)))
            await app.command_shadow(_Req(b"RIFF"), sat="familyroom",
                                     arb_turn_id=winner_turn, turn_id="loser")



class ShadowRescueTest(_RecoveryBase):
    async def test_shadow_already_in_is_dispatched(self):
        tid = turns.start("kitchen", "wake", verified=True)
        app._shadow_expect(tid)
        await self._shadow(tid, "Okay, computer. Did the kids sleep today?")
        resp = await app.command_rescue(sat="kitchen", turn_id=tid)
        self.assertTrue(resp["rescued"])
        self.assertEqual(resp["rescued_from"], "familyroom")
        self.assertEqual(resp["response"], "Claire napped twice.")
        cmd = self.dispatch.await_args.args[0].lower()
        self.assertIn("did the kids sleep today", cmd)
        self.assertNotIn("computer", cmd)
        row = turns.recent(limit=1)[0]
        self.assertEqual(row["intent"], "nap_status")
        self.assertEqual(row["asr_model"], "shadow:familyroom")

    async def test_waits_for_a_shadow_in_flight(self):
        tid = turns.start("kitchen", "wake", verified=True)
        app._shadow_expect(tid)

        async def late():
            await asyncio.sleep(0.2)
            await self._shadow(tid, "did the kids sleep today")

        task = asyncio.create_task(late())
        resp = await app.command_rescue(sat="kitchen", turn_id=tid)
        await task
        self.assertTrue(resp["rescued"])

    async def test_no_loser_answers_at_once(self):
        tid = turns.start("kitchen", "wake", verified=True)
        t0 = time.time()
        resp = await app.command_rescue(sat="kitchen", turn_id=tid)
        self.assertFalse(resp["rescued"])
        self.assertLess(time.time() - t0, 0.1)
        self.dispatch.assert_not_awaited()

    async def test_loser_never_posts_gives_up_at_deadline(self):
        tid = turns.start("kitchen", "wake", verified=True)
        app._shadow_expect(tid)
        t0 = time.time()
        resp = await app.command_rescue(sat="kitchen", turn_id=tid)
        self.assertFalse(resp["rescued"])
        self.assertGreaterEqual(time.time() - t0, 0.45)
        self.dispatch.assert_not_awaited()

    async def test_shadow_for_another_turn_is_not_used(self):
        tid = turns.start("kitchen", "wake", verified=True)
        await self._shadow("some-other-turn", "turn off the lights")
        resp = await app.command_rescue(sat="kitchen", turn_id=tid)
        self.assertFalse(resp["rescued"])


    async def test_rescued_ask_is_dropped(self):
        tid = turns.start("kitchen", "wake", verified=True)
        app._shadow_expect(tid)
        await self._shadow(tid, "come on kids do you want food")
        self.dispatch.return_value = {"intent": "ask", "salvage_dropped": True,
                                      "silent": True, "ok": False, "response": ""}
        resp = await app.command_rescue(sat="kitchen", turn_id=tid)
        self.assertFalse(resp["rescued"])
        self.assertTrue(self.dispatch.await_args.kwargs["salvage"])
        row = turns.recent(limit=1)[0]
        self.assertEqual(row["reject_reason"], "salvage_dropped")


class SalvageTest(_RecoveryBase):
    """/command/salvage: the winner's own ch1 audio, transcribed after its ch0
    VAD found nothing (2026-11-20 16:00, "did the kids sleep today" on the ding)."""

    def _asr(self, text):
        return patch.object(app.clients, "transcribe_final",
                            new=AsyncMock(return_value=clients.AsrResult(text)))

    def _by_text(self, local_words):
        async def dispatch(text, **kw):
            if any(w in text.lower() for w in local_words):
                return {"intent": "nap_status", "response": "Claire napped twice.", "ok": True}
            return {"intent": "ask", "response": "", "ok": False, "silent": True,
                    "salvage_dropped": True}
        self.dispatch.side_effect = dispatch

    async def test_local_command_on_ch1_is_answered(self):
        tid = turns.start("kitchen", "wake", verified=True)
        with self._asr("Okay, computer. Did the kids sleep today?"):
            resp = await app.command_salvage(_Req(b"RIFF"), sat="kitchen", turn_id=tid)
        self.assertTrue(resp["rescued"])
        self.assertEqual(resp["rescued_from"], "ch1")
        self.assertEqual(self.dispatch.await_args.args[0].lower().strip("?"),
                         "did the kids sleep today")
        self.assertTrue(self.dispatch.await_args.kwargs["salvage"])
        row = turns.recent(limit=1)[0]
        self.assertEqual(row["asr_model"], "salvage:ch1")
        self.assertEqual(row["intent"], "nap_status")

    async def test_llm_bound_salvage_is_dropped_silently(self):
        tid = turns.start("kitchen", "wake", verified=True)
        self._by_text(["kids sleep"])
        with self._asr("Come on, kids. Do you want food?"):
            resp = await app.command_salvage(_Req(b"RIFF"), sat="kitchen", turn_id=tid)
        self.assertFalse(resp["rescued"])
        app._turn_event.assert_not_awaited()
        row = turns.recent(limit=1)[0]
        self.assertEqual(row["reject_reason"], "salvage_dropped")
        self.assertIn("food", row["command"].lower())

    async def test_dropped_salvage_falls_back_to_a_good_shadow(self):
        tid = turns.start("kitchen", "wake", verified=True)
        app._shadow_expect(tid)
        await self._shadow(tid, "did the kids sleep today")
        self._by_text(["kids sleep"])
        with self._asr("mumble mumble what"):
            resp = await app.command_salvage(_Req(b"RIFF"), sat="kitchen", turn_id=tid)
        self.assertTrue(resp["rescued"])
        self.assertEqual(resp["rescued_from"], "familyroom")

    async def test_empty_or_bare_wake_goes_to_shadow_then_miss(self):
        for heard in ("", "Okay, computer."):
            with self.subTest(heard=heard):
                tid = turns.start("kitchen", "wake", verified=True)
                with self._asr(heard):
                    resp = await app.command_salvage(_Req(b"RIFF"), sat="kitchen",
                                                     turn_id=tid)
                self.assertFalse(resp["rescued"])
                self.dispatch.assert_not_awaited()
                self.assertEqual(turns.recent(limit=1)[0]["reject_reason"], "no_command")

    async def test_asr_failure_is_a_quiet_miss(self):
        tid = turns.start("kitchen", "wake", verified=True)
        with patch.object(app.clients, "transcribe_final",
                          new=AsyncMock(side_effect=RuntimeError("gx10 down"))):
            resp = await app.command_salvage(_Req(b"RIFF"), sat="kitchen", turn_id=tid)
        self.assertFalse(resp["rescued"])


class SalvageGateTest(unittest.IsolatedAsyncioTestCase):
    """The real handle_command gate (only the classifier/handlers stubbed)."""

    def setUp(self) -> None:
        self.events = AsyncMock()
        for p in (patch.object(app, "_turn_event", new=self.events),
                  patch.object(app.events, "on_dashboard", return_value=False)):
            p.start()
            self.addCleanup(p.stop)
        app._CUR_SAT.set("kitchen")
        app.session_clear_pending()
        self.addCleanup(app.session_clear_pending)

    async def test_ask_none_unclear_are_dropped_before_any_event(self):
        for label in ("ask", "none", "unclear"):
            with self.subTest(label=label):
                self.events.reset_mock()
                parse = AsyncMock(return_value=app.intent_mod.validate({"intent": label}))
                with patch.object(app.intent_mod, "fast_parse", return_value=None), \
                     patch.object(app.intent_mod, "parse", new=parse), \
                     patch.object(app.home_mod, "fuzzy_match", return_value=None):
                    r = await app.handle_command("come on kids do you want food",
                                                 salvage=True)
                self.assertTrue(r.get("salvage_dropped"), r)
                self.events.assert_not_awaited()

    async def test_without_salvage_ask_is_not_gated(self):
        parse = AsyncMock(return_value=app.intent_mod.validate({"intent": "ask"}))
        ask = AsyncMock(return_value={"response": "x", "full": "x", "ok": True})
        with patch.object(app.intent_mod, "fast_parse", return_value=None), \
             patch.object(app.intent_mod, "parse", new=parse), \
             patch.object(app.ask_mod, "handle_ask", new=ask):
            r = await app.handle_command("is it normal for x or y")
        self.assertFalse(r.get("salvage_dropped"))

    async def test_local_fast_path_passes_the_gate_and_shows(self):
        nap = AsyncMock(return_value={"response": "Claire napped.", "ok": True})
        with patch.object(app.nap_mod, "handle", new=nap):
            r = await app.handle_command("did the kids sleep today", salvage=True)
        self.assertFalse(r.get("salvage_dropped"), r)
        self.assertEqual(r["intent"], "nap_status")
        shown = [c.args[0] for c in self.events.await_args_list]
        self.assertIn("transcript", shown)

    async def test_salvage_never_confirms_a_pending_bulk_op(self):
        app.session_set_pending("clear", [{"id": "1"}], list_type="todo")
        run = AsyncMock(return_value={"intent": "clear_list", "response": "Cleared.", "ok": True})
        parse = AsyncMock(return_value=app.intent_mod.validate({"intent": "none"}))
        with patch.object(app, "_run_pending", new=run), \
             patch.object(app.intent_mod, "parse", new=parse), \
             patch.object(app.home_mod, "fuzzy_match", return_value=None):
            r = await app.handle_command("yeah", salvage=True)
        run.assert_not_awaited()
        self.assertTrue(r.get("salvage_dropped"))
        self.assertIsNotNone(app.session_pending())   # left as it was


if __name__ == "__main__":
    unittest.main()
