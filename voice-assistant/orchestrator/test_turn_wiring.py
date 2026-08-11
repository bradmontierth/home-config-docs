"""End-to-end wiring of the turn row across the three endpoints that build it.

The unit tests in test_turns.py prove the store behaves; these prove app.py
actually calls it, in the right order, with the right ids — which is where a
telemetry spine really goes wrong. Nothing here touches a network, a device or
a satellite.
"""

import asyncio
import os
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

from . import app, config, turns


def _wav() -> bytes:
    """Minimal non-empty body. Nothing decodes it — ASR is mocked."""
    return b"RIFF____WAVEfmt "


class _Req:
    """Stand-in for a Starlette Request: the handlers only await .body()."""

    def __init__(self, body: bytes) -> None:
        self._body = body

    async def body(self) -> bytes:
        return self._body


class TurnWiringTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self._dir = tempfile.TemporaryDirectory()
        self._prev = config.DB_PATH
        config.DB_PATH = os.path.join(self._dir.name, "orchestrator.db")
        turns._db = None
        app._ARB["sat"] = None
        app._ARB["until"] = 0.0

    def tearDown(self) -> None:
        config.DB_PATH = self._prev
        turns._db = None
        self._dir.cleanup()

    async def _verify(self, transcript: str, sat: str = "kitchen") -> dict:
        with patch.object(app.clients, "transcribe",
                          new=AsyncMock(return_value=transcript)), \
             patch.object(app, "_turn_event", new=AsyncMock()), \
             patch.object(app.policy_mod, "evaluate",
                          new=AsyncMock(return_value={"allowed": True})):
            return await app.verify_wake(_Req(_wav()), sat=sat)

    async def test_verify_opens_a_row_and_hands_back_its_id(self):
        resp = await self._verify("okay computer what's the weather")
        self.assertTrue(resp["verified"])
        self.assertIn("turn_id", resp)
        rows = turns.recent()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["turn_id"], resp["turn_id"])
        self.assertEqual(rows[0]["verified"], 1)
        self.assertEqual(rows[0]["sat"], "kitchen")
        # asr_ms is deliberately not asserted here: the stage timer lives
        # INSIDE clients.transcribe, which these tests mock away. The real
        # instrumentation is covered by ClientStageTimingTest below.

    async def test_stage2_reject_is_recorded_with_a_reason(self):
        """The rejects ARE the funnel data — 4.2% of kitchen stage-1 triggers
        survive stage 2, so a table holding only confirmations is useless."""
        resp = await self._verify("come in, simon")
        self.assertFalse(resp["verified"])
        row = turns.recent()[0]
        self.assertEqual(row["verified"], 0)
        self.assertEqual(row["reject_reason"], "low_score")
        self.assertEqual(row["transcript"], "come in, simon")

    async def test_empty_transcript_rejects_as_empty_not_low_score(self):
        await self._verify("")
        self.assertEqual(turns.recent()[0]["reject_reason"], "empty")

    async def test_policy_block_is_counted(self):
        with patch.object(app.policy_mod, "evaluate", new=AsyncMock(
                return_value={"allowed": False, "reason": "quiet_hours"})):
            await app.verify_wake(_Req(_wav()), sat="simon")
        row = turns.recent()[0]
        self.assertEqual(row["reject_reason"], "policy")
        self.assertEqual(row["sat"], "simon")

    async def test_arbitration_loser_records_the_winner(self):
        app._arb_claim("kitchen")
        with patch.object(app.policy_mod, "evaluate",
                          new=AsyncMock(return_value={"allowed": True})):
            resp = await app.verify_wake(_Req(_wav()), sat="familyroom")
        self.assertTrue(resp["suppressed"])
        row = turns.recent()[0]
        self.assertEqual(row["reject_reason"], "suppressed")
        self.assertEqual(row["arb_winner"], "kitchen")

    async def test_telemetry_merges_onto_the_same_row(self):
        resp = await self._verify("okay computer set a timer")
        await app.telemetry({"turn_id": resp["turn_id"], "chime_ms": 226,
                             "rtt_ms": 393, "server_ms": 364,
                             "peak_score": 0.834,
                             "model": "okay_google", "clip": "verify-ok.wav"})
        rows = turns.recent()
        self.assertEqual(len(rows), 1, "telemetry must update, not insert")
        self.assertEqual(rows[0]["chime_ms"], 226)
        self.assertEqual(rows[0]["stage1_score"], 0.834)
        self.assertEqual(rows[0]["wake_model"], "okay_google")
        # rtt - server is the WiFi + HTTP cost; both must survive the merge.
        self.assertEqual(rows[0]["server_ms"], 364)

    async def test_telemetry_for_an_unknown_turn_is_harmless(self):
        """The satellite fires this without waiting; a restarted orchestrator
        must not turn that into an error."""
        out = await app.telemetry({"turn_id": "gone", "chime_ms": 1})
        self.assertTrue(out["ok"])
        self.assertEqual(turns.recent(), [])

    async def _command(self, transcript: str, **kw):
        with patch.object(app.clients, "transcribe",
                          new=AsyncMock(return_value=transcript)), \
             patch.object(app, "_turn_event", new=AsyncMock()), \
             patch.object(app.policy_mod, "evaluate",
                          new=AsyncMock(return_value={"allowed": True})), \
             patch.object(app, "_dispatch", new=AsyncMock(return_value={
                 "intent": "weather", "response": "Sunny and 70.", "ok": True})):
            return await app.command_audio(_Req(_wav()), **kw)

    async def test_full_turn_is_exactly_one_row(self):
        """A wake turn spans /verify + /telemetry + /command/audio. All three
        must land on one row or the drawer can't show a turn."""
        v = await self._verify("okay computer what's the weather")
        await app.telemetry({"turn_id": v["turn_id"], "chime_ms": 226,
                             "rtt_ms": 393, "peak_score": 0.9})
        await self._command("what's the weather", turn_id=v["turn_id"])

        rows = turns.recent()
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row["verified"], 1)          # from /verify
        self.assertEqual(row["chime_ms"], 226)        # from /telemetry
        self.assertEqual(row["intent"], "weather")    # from /command/audio
        self.assertEqual(row["response"], "Sunny and 70.")
        self.assertEqual(row["command"], "what's the weather")
        self.assertIsNotNone(row["total_ms"])
        self.assertIsNotNone(row["handler_ms"])

    async def test_command_without_a_verify_opens_its_own_row(self):
        """Follow-ups, text turns and the dashboard mic button have no wake
        step."""
        await self._command("and tomorrow?", followup=True)
        row = turns.recent()[0]
        self.assertEqual(row["kind"], "followup")
        self.assertEqual(row["intent"], "weather")
        self.assertIsNone(row["verified"])

    async def test_stale_turn_id_does_not_lose_the_command(self):
        """A satellite whose /verify predates this feature, or an orchestrator
        restarted mid-turn: the turn must still be answered."""
        resp = await self._command("what's the weather", turn_id="nonexistent")
        self.assertEqual(resp["intent"], "weather")
        self.assertEqual(turns.recent(), [])

    async def test_silence_after_the_chime_is_recorded(self):
        v = await self._verify("okay computer")
        await self._command("", turn_id=v["turn_id"])
        row = turns.recent()[0]
        self.assertEqual(row["reject_reason"], "no_command")
        self.assertEqual(row["ok"], 0)

    async def test_a_broken_table_never_breaks_the_turn(self):
        """The whole contract in one test: telemetry may lose data, never a
        reply."""
        config.DB_PATH = os.path.join(self._dir.name, "wedged.db")
        os.makedirs(config.DB_PATH)
        turns._db = None
        v = await self._verify("okay computer what's the weather")
        self.assertTrue(v["verified"])
        resp = await self._command("what's the weather", turn_id=v["turn_id"])
        self.assertEqual(resp["response"], "Sunny and 70.")


class ClientStageTimingTest(unittest.IsolatedAsyncioTestCase):
    """The stage timers live inside clients.py so every caller is covered
    without a signature change. That only holds if they actually wrap the HTTP
    call — mock at the transport layer, not the function, to prove it."""

    def _fake_client(self, payload, *, content=b""):
        class _Resp:
            def raise_for_status(self): pass
            def json(self): return payload
            @property
            def content(self): return content

        class _Client:
            def __init__(self, *a, **kw): pass       # httpx.AsyncClient(timeout=30)
            async def __aenter__(self): return self
            async def __aexit__(self, *a): return False
            async def post(self, *a, **kw):
                await asyncio.sleep(0.02)
                return _Resp()

        return _Client

    async def test_transcribe_records_asr(self):
        from . import clients, timing
        with patch.object(clients.httpx, "AsyncClient",
                          self._fake_client({"transcript_text": "hello"})):
            timing.start()
            await clients.transcribe(b"RIFF")
            snap = timing.snapshot()
        self.assertGreaterEqual(snap.get("asr", 0), 15)

    async def test_classifier_records_classify(self):
        """The local LLM classifier had no timing at all before this — the one
        stage nobody could see."""
        from . import clients, timing
        payload = {"choices": [{"message": {"content": "{}"}}]}
        with patch.object(clients.httpx, "AsyncClient", self._fake_client(payload)):
            timing.start()
            await clients.parse_intent_raw([{"role": "user", "content": "hi"}])
            snap = timing.snapshot()
        self.assertGreaterEqual(snap.get("classify", 0), 15)

    async def test_synthesize_records_tts(self):
        from . import clients, timing
        with patch.object(clients.httpx, "AsyncClient",
                          self._fake_client({}, content=b"RIFF")):
            timing.start()
            await clients.synthesize("hello")
            snap = timing.snapshot()
        self.assertGreaterEqual(snap.get("tts", 0), 15)

    async def test_two_decodes_accumulate_into_one_asr_number(self):
        """The dual-decode tail rescue runs ASR twice on a reject; asr_ms must
        be the turn's total, not the last call's."""
        from . import clients, timing
        with patch.object(clients.httpx, "AsyncClient",
                          self._fake_client({"transcript_text": "x"})):
            timing.start()
            await clients.transcribe(b"RIFF")
            one = timing.snapshot()["asr"]
            await clients.transcribe(b"RIFF")
            two = timing.snapshot()["asr"]
        self.assertGreater(two, one)


if __name__ == "__main__":
    unittest.main()
