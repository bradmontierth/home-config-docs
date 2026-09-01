"""Loudness hand-off between same-hardware mics (2026-08-31).

Live: Claire's Voice PE won a wake at -41 dBFS over Simon's at -21 and the
answer went to the wrong room. The verify race is a hop race; between mics of
the same hardware the wake loudness says which room the speaker is in. The
louder mic is handed the turn at its own /verify -- no waiting for both --
and the first mic's capture is demoted to a shadow when it posts.
Nothing here touches a network.
"""

import asyncio
import os
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

from . import app, config, turns

LOUD, QUIET, MID = b"LOUD", b"QUIET", b"MID"
_DB = {LOUD: -21.0, QUIET: -41.0, MID: -35.0}


class _Req:
    def __init__(self, body: bytes) -> None:
        self._body = body

    async def body(self) -> bytes:
        return self._body


class LoudnessHandoffTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self._dir = tempfile.TemporaryDirectory()
        self._prev = config.DB_PATH
        config.DB_PATH = os.path.join(self._dir.name, "orchestrator.db")
        turns._db = None
        self._groups = config.ARB_LOUDNESS_GROUPS
        self._margin = config.ARB_LOUDNESS_MARGIN_DB
        config.ARB_LOUDNESS_GROUPS = [{"simon", "claire"}]
        config.ARB_LOUDNESS_MARGIN_DB = 8.0
        app._ARB.update(sat=None, until=0.0, turn_id=None, rms_db=None,
                        stage1=None, at=0.0)
        app._ARB_HANDOFF.clear()
        self.amp = AsyncMock()
        patchers = [
            patch.object(app.loudness, "peak_window_dbfs",
                         side_effect=lambda wav, **kw: _DB.get(wav)),
            patch.object(app, "_turn_event", new=AsyncMock()),
            patch.object(app.policy_mod, "evaluate",
                         new=AsyncMock(return_value={"allowed": True})),
            patch.object(app.broadcast_mod, "amp_wake", new=self.amp),
        ]
        for p in patchers:
            p.start()
            self.addCleanup(p.stop)

    def tearDown(self) -> None:
        config.DB_PATH = self._prev
        config.ARB_LOUDNESS_GROUPS = self._groups
        config.ARB_LOUDNESS_MARGIN_DB = self._margin
        turns._db = None
        app._ARB.update(sat=None, until=0.0, turn_id=None, rms_db=None)
        app._ARB_HANDOFF.clear()
        self._dir.cleanup()

    async def _verify(self, sat: str, wav: bytes, transcript="okay computer"):
        side = transcript if isinstance(transcript, list) else [transcript] * 4
        with patch.object(app.clients, "transcribe",
                          new=AsyncMock(side_effect=side)):
            return await app.verify_wake(_Req(wav), sat=sat)

    async def _command(self, sat: str, turn_id: str, transcript: str):
        dispatch = AsyncMock(return_value={"intent": "home_control",
                                           "response": "Pac-Man.", "ok": True})
        with patch.object(app.clients, "transcribe",
                          new=AsyncMock(return_value=transcript)), \
             patch.object(app, "_dispatch", new=dispatch):
            resp = await app.command_audio(_Req(b"RIFF"), stitched=True,
                                           sat=sat, turn_id=turn_id)
        return resp, dispatch

    def _row(self, turn_id: str) -> dict:
        return next(r for r in turns.recent() if r["turn_id"] == turn_id)

    async def test_louder_peer_is_handed_the_turn(self):
        first = await self._verify("claire", QUIET)
        self.assertTrue(first["verified"])
        self.assertEqual(app._ARB["rms_db"], -41.0)   # inline, not deferred

        second = await self._verify("simon", LOUD)
        self.assertTrue(second["verified"])
        self.assertNotIn("suppressed", second)
        self.assertEqual(second["handoff_from"], "claire")
        self.assertEqual(app._ARB["sat"], "simon")
        self.assertEqual(app._ARB["turn_id"], second["turn_id"])
        self.assertEqual(app._ARB_HANDOFF, {first["turn_id"]: "simon"})
        # the paper trail: one paired row on the first mic, a verified row
        # on the one that answers
        c = self._row(first["turn_id"])
        self.assertEqual(c["reject_reason"], "handoff")
        self.assertEqual(c["arb_winner"], "simon")
        self.assertEqual(c["other_sat"], "simon")
        self.assertEqual(c["other_rms_db"], -21.0)
        s = self._row(second["turn_id"])
        self.assertEqual(s["verified"], 1)
        self.assertEqual(s["arb_turn_id"], first["turn_id"])
        self.assertEqual(s["wake_rms_db"], -21.0)
        # the answering room's amp is woken like any verified wake
        await asyncio.sleep(0)                          # let the task run
        self.amp.assert_awaited()
        self.assertIn("simon", self.amp.await_args.args[0])

    async def test_first_mics_capture_is_demoted_and_the_louder_one_answers(self):
        first = await self._verify("claire", QUIET)
        second = await self._verify("simon", LOUD)

        resp, dispatch = await self._command(
            "claire", first["turn_id"], "okay computer give me pac man")
        self.assertFalse(resp["ok"])
        self.assertTrue(resp["silent"])
        self.assertEqual(resp["winner"], "simon")
        dispatch.assert_not_awaited()
        c = self._row(first["turn_id"])
        self.assertEqual(c["ok"], 0)
        self.assertEqual(c["reject_reason"], "handoff")
        self.assertEqual(c["transcript"], "okay computer give me pac man")
        self.assertEqual(app._ARB_HANDOFF, {})

        resp, dispatch = await self._command(
            "simon", second["turn_id"], "okay computer give me pac man")
        self.assertTrue(resp["ok"])
        dispatch.assert_awaited_once()
        self.assertEqual(dispatch.await_args.args[0], "give me pac man")

    async def test_the_louder_mic_can_also_be_the_first_to_verify(self):
        """Nothing to hand off: the quieter peer stays suppressed."""
        await self._verify("simon", LOUD)
        second = await self._verify("claire", QUIET)
        self.assertTrue(second["suppressed"])
        self.assertEqual(second["winner"], "simon")
        self.assertEqual(app._ARB_HANDOFF, {})

    async def test_inside_the_margin_stays_with_the_first_mic(self):
        await self._verify("claire", QUIET)
        second = await self._verify("simon", MID)     # +6 dB < 8 dB margin
        self.assertTrue(second["suppressed"])
        self.assertEqual(app._ARB["sat"], "claire")

    async def test_only_same_hardware_groups_compare_loudness(self):
        """Kitchen and the family room share speakers and run different
        mics; their race stays a race."""
        await self._verify("kitchen", QUIET)
        second = await self._verify("familyroom", LOUD)
        self.assertTrue(second["suppressed"])
        self.assertEqual(app._ARB["sat"], "kitchen")
        self.assertIsNone(app._ARB["rms_db"])         # not on their chime path

    async def test_handoff_still_needs_stage_two(self):
        """Louder is not enough on its own: a coincident loud sound in the
        other room that stage 1 fired on must not steal the turn."""
        await self._verify("claire", QUIET)
        second = await self._verify("simon", LOUD, transcript="hey come here")
        self.assertTrue(second["suppressed"])
        self.assertEqual(app._ARB["sat"], "claire")
        self.assertEqual(app._ARB_HANDOFF, {})

    async def test_handoff_after_a_claim_that_landed_during_our_asr(self):
        """The post-ASR branch: both wavs were decoding, the quieter one
        finished first."""
        async def transcribe(wav):
            app._arb_claim("claire", None, -41.0)
            app._ARB["turn_id"] = "claire-turn"
            return "okay computer"

        with patch.object(app.clients, "transcribe", new=transcribe):
            second = await app.verify_wake(_Req(LOUD), sat="simon")
        self.assertTrue(second["verified"])
        self.assertEqual(second["handoff_from"], "claire")
        self.assertEqual(app._ARB["sat"], "simon")
        self.assertEqual(app._ARB_HANDOFF, {"claire-turn": "simon"})


if __name__ == "__main__":
    unittest.main()
