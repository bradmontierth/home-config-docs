"""/partial during continued conversation reports a leading wake phrase so the
satellite can chime immediately (in-capture re-wake, 2026-08-28). The contract
the satellite relies on: `wake` iff the final /command/audio turn would strip
or rewake on the same words, `bare` iff nothing follows the phrase, and the
caption never carries the wake phrase. Nothing here touches a network."""

import asyncio
import unittest
from unittest.mock import AsyncMock, patch

from . import app


class _Req:
    def __init__(self, body: bytes) -> None:
        self._body = body

    async def body(self) -> bytes:
        return self._body


class PartialRewakeTest(unittest.IsolatedAsyncioTestCase):
    async def _partial(self, text: str, followup: int) -> tuple[dict, AsyncMock]:
        ev = AsyncMock()
        with patch.object(app.clients, "transcribe",
                          new=AsyncMock(return_value=text)), \
             patch.object(app, "_turn_event", new=ev):
            resp = await app.partial(_Req(b"RIFF"), seq=7, sat="kitchen",
                                     followup=followup)
        return resp, ev

    async def test_run_together_rewake_flags_wake_and_strips_caption(self):
        resp, ev = await self._partial("okay computer what's the forecast", 1)
        self.assertTrue(resp["wake"])
        self.assertFalse(resp["bare"])
        self.assertEqual(resp["text"], "okay computer what's the forecast")
        ev.assert_awaited_once()
        # the matcher hands back its normalized tail (no apostrophes) -- same as
        # the final-transcript strip already does
        self.assertEqual(ev.await_args.kwargs["text"], "whats the forecast")

    async def test_bare_rewake_flags_bare_and_skips_caption(self):
        resp, ev = await self._partial("okay computer", 1)
        self.assertTrue(resp["wake"])
        self.assertTrue(resp["bare"])
        ev.assert_not_awaited()

    async def test_plain_followup_is_untouched(self):
        resp, ev = await self._partial("what about tomorrow", 1)
        self.assertFalse(resp["wake"])
        self.assertFalse(resp["bare"])
        self.assertEqual(ev.await_args.kwargs["text"], "what about tomorrow")

    async def test_wake_turn_partials_never_flag(self):
        """A cold-wake capture's pre-roll stitch carries the phrase; the chime
        for that already played. Only follow-up captures ask."""
        resp, ev = await self._partial("okay computer what's the forecast", 0)
        self.assertFalse(resp["wake"])
        self.assertEqual(ev.await_args.kwargs["text"],
                         "okay computer what's the forecast")

    async def test_asr_failure_stays_cosmetic(self):
        with patch.object(app.clients, "transcribe",
                          new=AsyncMock(side_effect=RuntimeError("gx10 down"))):
            resp = await app.partial(_Req(b"RIFF"), seq=7, followup=1)
        self.assertFalse(resp["ok"])
        self.assertNotIn("wake", resp)


if __name__ == "__main__":
    unittest.main()


class RewakeArbitrationTest(unittest.IsolatedAsyncioTestCase):
    """The in-capture re-wake takes part in two-mic arbitration (live
    double-ding/double-answer 2026-08-28: kitchen + family room, which share
    the kitchen's speakers)."""

    def setUp(self) -> None:
        app._ARB.update(sat=None, until=0.0, turn_id=None, at=0.0)
        app._FOLLOWUP_LISTEN.clear()
        self._peers = app.config.ARB_PEERS
        self._wait = app.config.REWAKE_ARB_WAIT_S
        app.config.ARB_PEERS = [{"kitchen", "familyroom"}]
        app.config.REWAKE_ARB_WAIT_S = 0.2

    def tearDown(self) -> None:
        app.config.ARB_PEERS = self._peers
        app.config.REWAKE_ARB_WAIT_S = self._wait
        app._ARB.update(sat=None, until=0.0, turn_id=None, at=0.0)
        app._FOLLOWUP_LISTEN.clear()

    async def _partial(self, text, sat, followup=1):
        with patch.object(app.clients, "transcribe",
                          new=AsyncMock(return_value=text)), \
             patch.object(app, "_turn_event", new=AsyncMock()):
            return await app.partial(_Req(b"RIFF"), seq=7, sat=sat,
                                     followup=followup)

    async def _verify(self, sat):
        with patch.object(app.clients, "transcribe",
                          new=AsyncMock(return_value="okay computer")), \
             patch.object(app, "_turn_event", new=AsyncMock()), \
             patch.object(app.policy_mod, "evaluate",
                          new=AsyncMock(return_value={"allowed": True})), \
             patch.object(app, "_note_wake_loudness", new=AsyncMock()), \
             patch.object(app.turns_mod, "start", return_value="t1"), \
             patch.object(app.turns_mod, "update"):
            return await app.verify_wake(_Req(b"RIFF"), sat=sat)

    async def test_rewake_partial_claims_the_turn(self):
        resp = await self._partial("okay computer", "kitchen")
        self.assertTrue(resp["wake"])
        self.assertEqual(app._ARB["sat"], "kitchen")
        self.assertEqual(app._arb_holder("familyroom"), "kitchen")

    async def test_rewake_partial_yields_when_a_peer_already_verified(self):
        app._arb_claim("familyroom")
        resp = await self._partial("okay computer what's the date", "kitchen")
        self.assertFalse(resp["wake"])
        self.assertTrue(resp["yield"])
        self.assertEqual(resp["winner"], "familyroom")
        self.assertEqual(app._ARB["sat"], "familyroom")   # not re-claimed

    async def test_peer_cold_verify_defers_and_loses_to_the_open_conversation(self):
        """Kitchen is mid-follow-up; the family-room mic cold-verifies the
        same phrase ~0.3s in, the kitchen's partial claims it ~0.7s in."""
        await app.session_listening(sat="kitchen")

        async def kitchen_claims_soon():
            await asyncio.sleep(0.05)
            await self._partial("okay computer", "kitchen")

        task = asyncio.create_task(kitchen_claims_soon())
        resp = await self._verify("familyroom")
        await task
        self.assertTrue(resp["suppressed"])
        self.assertEqual(resp["winner"], "kitchen")

    async def test_peer_cold_verify_proceeds_when_nobody_claims(self):
        await app.session_listening(sat="kitchen")
        resp = await self._verify("familyroom")
        self.assertTrue(resp["verified"])
        self.assertEqual(app._ARB["sat"], "familyroom")

    async def test_non_peer_never_waits(self):
        await app.session_listening(sat="kitchen")
        t0 = asyncio.get_event_loop().time()
        resp = await self._verify("master")
        self.assertTrue(resp["verified"])
        self.assertLess(asyncio.get_event_loop().time() - t0, 0.15)

    async def test_idle_clears_the_listen_so_peers_stop_deferring(self):
        await app.session_listening(sat="kitchen")
        await app.session_idle(sat="kitchen")
        self.assertIsNone(app._peer_in_followup("familyroom"))

    async def test_followup_command_yields_when_peer_took_a_wake_during_listen(self):
        await app.session_listening(sat="familyroom")
        app._arb_claim("kitchen")            # kitchen cold-verified meanwhile
        with patch.object(app.clients, "transcribe",
                          new=AsyncMock(return_value="what's the date")) as asr, \
             patch.object(app, "_turn_event", new=AsyncMock()), \
             patch.object(app.policy_mod, "evaluate",
                          new=AsyncMock(return_value={"allowed": True})), \
             patch.object(app.turns_mod, "start", return_value="t2"), \
             patch.object(app.turns_mod, "update"):
            resp = await app.command_audio(_Req(b"RIFF"), followup=True,
                                           sat="familyroom")
        self.assertTrue(resp["yield"])
        self.assertEqual(resp["winner"], "kitchen")
        asr.assert_not_awaited()
        self.assertNotIn("familyroom", app._FOLLOWUP_LISTEN)

    async def test_followup_command_runs_when_own_rewake_claimed(self):
        """Our own partial claim must not read as a peer taking the wake."""
        await app.session_listening(sat="kitchen")
        app._arb_claim("kitchen")
        with patch.object(app.clients, "transcribe",
                          new=AsyncMock(return_value="what's the date")), \
             patch.object(app, "_turn_event", new=AsyncMock()), \
             patch.object(app.policy_mod, "evaluate",
                          new=AsyncMock(return_value={"allowed": True})), \
             patch.object(app.turns_mod, "start", return_value="t3"), \
             patch.object(app.turns_mod, "update"), \
             patch.object(app.turns_mod, "finish"), \
             patch.object(app, "_dispatch", new=AsyncMock(return_value={
                 "intent": "time_query", "response": "Friday.", "ok": True})):
            resp = await app.command_audio(_Req(b"RIFF"), followup=True,
                                           sat="kitchen")
        self.assertNotIn("yield", resp)
        self.assertEqual(resp["intent"], "time_query")
