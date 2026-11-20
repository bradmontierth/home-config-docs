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

from . import app, config, turns


class _Req:
    def __init__(self, body: bytes) -> None:
        self._body = body

    async def body(self) -> bytes:
        return self._body


class ShadowRescueTest(unittest.IsolatedAsyncioTestCase):
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


if __name__ == "__main__":
    unittest.main()
