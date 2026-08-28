"""/partial during continued conversation reports a leading wake phrase so the
satellite can chime immediately (in-capture re-wake, 2026-08-28). The contract
the satellite relies on: `wake` iff the final /command/audio turn would strip
or rewake on the same words, `bare` iff nothing follows the phrase, and the
caption never carries the wake phrase. Nothing here touches a network."""

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
