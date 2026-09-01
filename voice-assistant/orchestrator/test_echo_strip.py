"""Own-reply echo handling, including the barge-in shape (2026-08-31).

The mic reopens DURING a zone reply on purpose, so a capture can be the reply
alone (the original is_echo case) or the reply with the person's words after
it. Live: Simon re-woke over "Playing the album Piano Man." and the turn
arrived as "play the album piano man give me pac man" -- the classifier took
the first clause and played Piano Man again. Nothing here touches a network.
"""

import os
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

from . import app, config, turns, zones


class StripEchoTest(unittest.TestCase):
    def setUp(self) -> None:
        zones._last_reply.clear()
        self._min = config.ZONE_ECHO_MIN_WORDS
        self._wake_win = config.ZONE_ECHO_WAKE_WINDOW_S

    def tearDown(self) -> None:
        zones._last_reply.clear()
        config.ZONE_ECHO_MIN_WORDS = self._min
        config.ZONE_ECHO_WAKE_WINDOW_S = self._wake_win

    def test_whole_capture_echo_is_dropped(self):
        zones.note_reply("simon", "Playing the album Piano Man.")
        self.assertEqual(zones.strip_echo("simon", "playing the album piano man"),
                         ("", True))
        self.assertTrue(zones.is_echo("simon", "Playing the album piano man."))

    def test_reply_then_command_keeps_the_command(self):
        """The live case: ASR heard 'Play' for 'Playing', and the kid's
        command followed straight after."""
        zones.note_reply("simon", "Playing the album Piano Man.")
        rest, echoed = zones.strip_echo(
            "simon", "play the album piano man give me pac man", followup=False)
        self.assertTrue(echoed)
        self.assertEqual(rest, "give me pac man")

    def test_word_count_slack_absorbs_asr_splitting(self):
        zones.note_reply("simon", "Turning on Pac-Man.")
        rest, echoed = zones.strip_echo("simon", "turning on pac man stop")
        self.assertTrue(echoed)
        self.assertEqual(rest, "stop")

    def test_prefix_alignment_never_eats_a_short_real_command(self):
        """partial_ratio would score 'stop' at 100 inside this reply."""
        zones.note_reply("simon", "Okay, I'll stop the music now.")
        self.assertEqual(zones.strip_echo("simon", "stop"), ("stop", False))
        self.assertEqual(zones.strip_echo("simon", "yes"), ("yes", False))

    def test_unrelated_command_is_untouched(self):
        zones.note_reply("simon", "Playing the album Piano Man.")
        text = "what's the weather tomorrow"
        self.assertEqual(zones.strip_echo("simon", text), (text, False))

    def test_a_short_reply_is_not_distinctive_enough_to_strip(self):
        zones.note_reply("simon", "Done.")
        text = "done turn on the fan"
        self.assertEqual(zones.strip_echo("simon", text), (text, False))
        # ...but heard back on its own it is still an echo
        self.assertEqual(zones.strip_echo("simon", "Done."), ("", True))

    def test_asr_drift_on_a_pure_echo_still_reads_as_whole(self):
        zones.note_reply("master", "It's 72 degrees and sunny.")
        self.assertEqual(zones.strip_echo("master", "It is 72 degrees and sunny"),
                         ("", True))

    def test_other_satellites_replies_do_not_apply(self):
        zones.note_reply("claire", "Playing the album Piano Man.")
        text = "play the album piano man give me pac man"
        self.assertEqual(zones.strip_echo("simon", text), (text, False))

    def test_wake_turn_window_is_the_tighter_one(self):
        zones.note_reply("simon", "Playing the album Piano Man.")
        zones._last_reply["simon"] = (zones._last_reply["simon"][0] - 30,
                                      zones._last_reply["simon"][1])
        text = "play the album piano man give me pac man"
        # 30s ago: inside the 45s follow-up window, outside the 20s wake one
        self.assertTrue(zones.strip_echo("simon", text, followup=True)[1])
        self.assertFalse(zones.strip_echo("simon", text, followup=False)[1])


def _wav() -> bytes:
    return b"RIFF____WAVEfmt "


class _Req:
    def __init__(self, body: bytes) -> None:
        self._body = body

    async def body(self) -> bytes:
        return self._body


class EchoOnWakeTurnTest(unittest.IsolatedAsyncioTestCase):
    """The strip runs on cold wakes too: a Voice PE room has no follow-up
    capture, so a re-wake over the reply IS a wake turn."""

    def setUp(self) -> None:
        self._dir = tempfile.TemporaryDirectory()
        self._prev = config.DB_PATH
        config.DB_PATH = os.path.join(self._dir.name, "orchestrator.db")
        turns._db = None
        zones._last_reply.clear()
        app._ARB.update(sat=None, until=0.0, turn_id=None, rms_db=None)

    def tearDown(self) -> None:
        config.DB_PATH = self._prev
        turns._db = None
        zones._last_reply.clear()
        self._dir.cleanup()

    async def _command(self, transcript: str, **kw):
        dispatch = AsyncMock(return_value={"intent": "home_control",
                                           "response": "Pac-Man.", "ok": True})
        with patch.object(app.clients, "transcribe",
                          new=AsyncMock(return_value=transcript)), \
             patch.object(app, "_turn_event", new=AsyncMock()), \
             patch.object(app.policy_mod, "evaluate",
                          new=AsyncMock(return_value={"allowed": True})), \
             patch.object(app, "_dispatch", new=dispatch):
            resp = await app.command_audio(_Req(_wav()), **kw)
        return resp, dispatch

    async def test_stitched_wake_turn_acts_on_what_follows_the_reply(self):
        zones.note_reply("simon", "Playing the album Piano Man.")
        resp, dispatch = await self._command(
            "Okay computer. Play the album Piano Man. Give me Pac-Man.",
            stitched=True, sat="simon")
        self.assertTrue(resp["ok"])
        dispatch.assert_awaited_once()
        self.assertEqual(dispatch.await_args.args[0], "give me pac man")

    async def test_wake_turn_that_is_only_the_reply_is_a_missed_command(self):
        zones.note_reply("simon", "Playing the album Piano Man.")
        resp, dispatch = await self._command(
            "Okay computer. Playing the album Piano Man.",
            stitched=True, sat="simon")
        self.assertFalse(resp["ok"])
        dispatch.assert_not_awaited()
        row = turns.recent()[0]
        self.assertEqual(row["reject_reason"], "echo")
        self.assertEqual(row["transcript"], "playing the album piano man")

    async def test_followup_that_is_only_the_reply_keeps_listening(self):
        zones.note_reply("master", "It is 72 degrees and sunny.")
        resp, dispatch = await self._command(
            "It's 72 degrees and sunny.", followup=True, sat="master")
        self.assertTrue(resp["echo"])
        dispatch.assert_not_awaited()

    async def test_followup_reply_then_question_is_answered(self):
        zones.note_reply("master", "It is 72 degrees and sunny.")
        resp, dispatch = await self._command(
            "It's 72 degrees and sunny. What about tomorrow?",
            followup=True, sat="master")
        dispatch.assert_awaited_once()
        self.assertEqual(dispatch.await_args.args[0], "What about tomorrow?")


if __name__ == "__main__":
    unittest.main()
