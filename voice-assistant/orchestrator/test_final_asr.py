"""The hedged final decode (clients.transcribe_final).

Both legs are faked at _asr_request, keyed on URL, so these prove the
selection rules — deadline, sanity checks, breaker, failure handling — with no
network. The rule set is the 2026-09-04 brief: take the primary when it lands
in time and looks sane, otherwise Parakeet's answer, never fail a turn because
the primary did.
"""

import asyncio
import unittest
from unittest.mock import patch

import httpx

from . import clients, config, timing

PRIMARY = "http://gx10:8099/cohere/transcribe"
FALLBACK = "http://gx10:8090/parakeet/transcribe"


def _fake(primary=("hello", "cohere", 0.0), fallback=("hello", None, 0.0)):
    """Build an _asr_request stand-in. Each leg is (text | Exception, model,
    delay_s)."""
    table = {PRIMARY: primary, FALLBACK: fallback}

    async def req(url, wav, client_name):
        text, model, delay = table[url]
        await asyncio.sleep(delay)
        if isinstance(text, Exception):
            raise text
        return text, model, round(delay * 1000)

    return req


def _http_error(code: int) -> httpx.HTTPStatusError:
    req = httpx.Request("POST", PRIMARY)
    return httpx.HTTPStatusError("boom", request=req,
                                 response=httpx.Response(code, request=req))


class FinalAsrTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self._saved = (config.FINAL_ASR_URL, config.ASR_URL,
                       config.FINAL_ASR_DEADLINE_MS, config.FINAL_ASR_BREAKER_S,
                       config.FINAL_ASR_HARD_CAP_MS)
        config.FINAL_ASR_URL = PRIMARY
        config.ASR_URL = FALLBACK
        config.FINAL_ASR_DEADLINE_MS = 100
        config.FINAL_ASR_HARD_CAP_MS = 300
        config.FINAL_ASR_BREAKER_S = 30
        clients._breaker_until = 0.0

    def tearDown(self) -> None:
        (config.FINAL_ASR_URL, config.ASR_URL,
         config.FINAL_ASR_DEADLINE_MS, config.FINAL_ASR_BREAKER_S,
         config.FINAL_ASR_HARD_CAP_MS) = self._saved
        clients._breaker_until = 0.0

    async def _run(self, **legs) -> clients.AsrResult:
        with patch.object(clients, "_asr_request", new=_fake(**legs)):
            return await clients.transcribe_final(b"RIFF")

    async def test_unset_url_is_plain_parakeet(self):
        config.FINAL_ASR_URL = ""
        with patch.object(clients, "transcribe",
                          new=lambda wav, c=None: asyncio.sleep(0, "pk")):
            r = await clients.transcribe_final(b"RIFF")
        self.assertEqual(r.text, "pk")
        self.assertIsNone(r.model)
        self.assertEqual(r.turn_fields(), {})

    async def test_primary_in_time_wins(self):
        r = await self._run(primary=("play crab rave", "cohere", 0.01),
                            fallback=("play crap wave", None, 0.03))
        self.assertEqual(r.text, "play crab rave")
        self.assertEqual(r.model, "cohere")
        self.assertIsNone(r.fallback_reason)
        self.assertEqual(r.turn_fields()["asr_fallback_reason"], "none")

    async def test_lost_race_falls_back_without_breaker(self):
        """Primary past the deadline AND behind Parakeet: Parakeet's answer,
        no breaker (it cost nothing), primary cancelled."""
        r = await self._run(primary=("late", "cohere", 0.5),
                            fallback=("on time", None, 0.15))
        self.assertEqual(r.text, "on time")
        self.assertEqual(r.model, "parakeet")
        self.assertEqual(r.fallback_reason, "timeout")
        self.assertIsNone(r.primary_ms)
        self.assertEqual(clients._breaker_until, 0.0)

    async def test_late_primary_still_wins_if_ahead_of_parakeet(self):
        """The GX10 slows both models under LLM load: past the deadline but
        first across the line is still the primary's turn."""
        r = await self._run(primary=("cohere text", "cohere", 0.15),
                            fallback=("pk text", None, 0.25))
        self.assertEqual(r.text, "cohere text")
        self.assertIsNone(r.fallback_reason)
        self.assertEqual(clients._breaker_until, 0.0)

    async def test_hard_cap_falls_back_and_trips_breaker(self):
        r = await self._run(primary=("never", "cohere", 2.0),
                            fallback=("slow pk", None, 0.5))
        self.assertEqual(r.text, "slow pk")
        self.assertEqual(r.fallback_reason, "hard_cap")
        self.assertGreater(clients._breaker_until, 0)
        # Next call inside the window never touches the primary.
        calls = []

        async def spy(url, wav, c):
            calls.append(url)
            return "pk", None, 1
        with patch.object(clients, "_asr_request", new=spy):
            r2 = await clients.transcribe_final(b"RIFF")
        self.assertEqual(calls, [FALLBACK])
        self.assertEqual(r2.fallback_reason, "breaker")

    async def test_primary_http_error_falls_back(self):
        r = await self._run(primary=(_http_error(503), None, 0.0),
                            fallback=("pk", None, 0.02))
        self.assertEqual(r.text, "pk")
        self.assertEqual(r.fallback_reason, "http_503")
        self.assertGreater(clients._breaker_until, 0)

    async def test_connection_error_falls_back(self):
        r = await self._run(primary=(httpx.ConnectError("refused"), None, 0.0),
                            fallback=("pk", None, 0.02))
        self.assertEqual(r.text, "pk")
        self.assertEqual(r.fallback_reason, "error")

    async def test_ding_loop_is_rejected(self):
        """The looping decoder returns in time, so only the content check
        catches it."""
        r = await self._run(
            primary=("set a timer timer timer timer timer timer", "cohere", 0.01),
            fallback=("set a timer", None, 0.02))
        self.assertEqual(r.text, "set a timer")
        self.assertEqual(r.fallback_reason, "repeat")
        self.assertEqual(clients._breaker_until, 0.0)   # quality, not outage

    async def test_runaway_length_is_rejected(self):
        long = " ".join(f"w{i}" for i in range(14))
        r = await self._run(primary=(long, "cohere", 0.03),
                            fallback=("what time is it", None, 0.01))
        self.assertEqual(r.fallback_reason, "length")

    async def test_fuller_decode_of_a_clipped_parakeet_is_kept(self):
        """Parakeet lost the head to the chime; a 6-word primary against a
        1-word Parakeet is the fix working, not a loop."""
        r = await self._run(primary=("set a timer for five minutes", "cohere", 0.03),
                            fallback=("minutes", None, 0.01))
        self.assertEqual(r.text, "set a timer for five minutes")
        self.assertIsNone(r.fallback_reason)

    async def test_length_check_needs_parakeet_already_back(self):
        long = " ".join(f"w{i}" for i in range(12))
        r = await self._run(primary=(long, "cohere", 0.01),
                            fallback=("short", None, 0.05))
        self.assertEqual(r.text, long)
        self.assertIsNone(r.fallback_reason)

    async def test_both_fail_raises_like_today(self):
        with self.assertRaises(httpx.ConnectError):
            await self._run(primary=(httpx.ConnectError("x"), None, 0.0),
                            fallback=(httpx.ConnectError("y"), None, 0.0))

    async def test_parakeet_failure_after_quality_reject_keeps_primary(self):
        r = await self._run(
            primary=("a a a a a a a", "cohere", 0.0),
            fallback=(httpx.ConnectError("y"), None, 0.01))
        self.assertEqual(r.text, "a a a a a a a")
        self.assertEqual(r.fallback_reason, "repeat+fallback_error")

    async def test_empty_primary_waits_for_parakeet(self):
        r = await self._run(primary=("", "cohere", 0.01),
                            fallback=("close the blind", None, 0.04))
        self.assertEqual(r.text, "close the blind")
        self.assertEqual(r.fallback_reason, "empty")
        self.assertEqual(clients._breaker_until, 0.0)

    async def test_both_empty_is_empty_primary(self):
        r = await self._run(primary=("", "cohere", 0.01),
                            fallback=("", None, 0.02))
        self.assertEqual(r.text, "")
        self.assertEqual(r.model, "cohere")
        self.assertIsNone(r.fallback_reason)

    async def test_fallback_retries_once_on_429(self):
        config.FINAL_ASR_RETRY_429_S = 0.01
        hits = []

        async def req(url, wav, c):
            hits.append(url)
            if url == PRIMARY:
                raise httpx.ConnectError("down")
            if hits.count(FALLBACK) == 1:
                raise _http_error(429)
            return "pk", None, 1
        with patch.object(clients, "_asr_request", new=req):
            r = await clients.transcribe_final(b"RIFF")
        self.assertEqual(r.text, "pk")
        self.assertEqual(hits.count(FALLBACK), 2)

    async def test_payload_ok_false_is_a_failure(self):
        """A 200 carrying {"ok": false} (a backend reporting not-ready in
        band) must not read as an empty transcript."""
        class _Resp:
            status_code = 200
            def raise_for_status(self): pass
            def json(self): return {"ok": False, "error": "model_not_ready"}

        class _Client:
            def __init__(self, *a, **kw): pass
            async def __aenter__(self): return self
            async def __aexit__(self, *a): return False
            async def post(self, *a, **kw): return _Resp()
        with patch.object(clients.httpx, "AsyncClient", _Client), \
                self.assertRaises(RuntimeError):
            await clients._asr_request(PRIMARY, b"RIFF", None)

    async def test_asr_stage_is_timed(self):
        timing.start()
        await self._run(primary=("x", "cohere", 0.02), fallback=("x", None, 0.02))
        self.assertGreaterEqual(timing.snapshot().get("asr", 0), 15)

    def test_repeat_rule(self):
        self.assertFalse(clients._repeats("no no no no", 4))
        self.assertTrue(clients._repeats("no no no no no", 4))
        self.assertTrue(clients._repeats("Timer, timer timer TIMER timer.", 4))
        self.assertFalse(clients._repeats("", 4))


if __name__ == "__main__":
    unittest.main()
