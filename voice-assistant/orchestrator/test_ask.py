import unittest
from unittest.mock import AsyncMock, patch

from . import ask


class AskBackgroundStreamTest(unittest.IsolatedAsyncioTestCase):
    async def test_stream_updates_recall_record(self):
        async def chunks():
            yield " answer"

        entry = {"a": "spoken"}
        recall = {"full": ""}
        with patch.object(ask.events, "emit", new=AsyncMock()):
            await ask._stream_full("question", entry, recall, "full", chunks())

        self.assertEqual(recall["full"], "full answer")
        self.assertEqual(entry["a"], "spoken\nfull answer")


class AskFillerRoutingTest(unittest.TestCase):
    def test_explicit_satellite_host_builds_local_speak_url(self):
        with patch.object(
            ask.zones,
            "_entry",
            return_value={"host": "http://127.0.0.1:8796/"},
        ):
            self.assertEqual(
                ask.zones.speak_url_for("office-dot"),
                "http://127.0.0.1:8796/speak",
            )

    def test_unknown_nondefault_satellite_has_no_speak_fallback(self):
        with patch.object(ask.zones, "_entry", return_value=None):
            self.assertIsNone(ask.zones.speak_url_for("unknown-room"))

    def test_zone_routed_satellite_never_uses_legacy_filler(self):
        with (
            patch.object(
                ask.zones,
                "route_for",
                return_value={"rooms": ["simon"]},
            ),
            patch.object(ask.zones, "speak_url_for", return_value=None),
        ):
            self.assertFalse(ask._should_play_legacy_filler("simon"))

    def test_unrouted_satellite_retains_legacy_filler(self):
        with (
            patch.object(ask.config, "ASK_FILLER", True),
            patch.object(ask.zones, "route_for", return_value=None),
            patch.object(
                ask.zones,
                "speak_url_for",
                return_value="http://kitchen/speak",
            ),
        ):
            self.assertTrue(ask._should_play_legacy_filler("kitchen"))

    def test_unknown_satellite_cannot_leak_filler_to_kitchen(self):
        with (
            patch.object(ask.zones, "route_for", return_value=None),
            patch.object(ask.zones, "speak_url_for", return_value=None),
        ):
            self.assertFalse(ask._should_play_legacy_filler("unknown-room"))


class AskFillerDispatchTest(unittest.IsolatedAsyncioTestCase):
    async def test_disabled_filler_keeps_kitchen_answer_working(self):
        async def answer_chunks():
            yield "It's test time."

        filler = AsyncMock()
        with (
            patch.object(ask.config, "ASK_FILLER", False),
            patch.object(
                ask.zones,
                "route_for",
                return_value=None,
            ),
            patch.object(ask.zones, "speak_url_for", return_value="http://kitchen/speak"),
            patch.object(ask, "_play_filler", new=filler),
            patch.object(ask.openrouter, "stream_chat", return_value=answer_chunks()),
            patch.object(ask.events, "emit", new=AsyncMock()),
            patch.object(ask.answers_mod, "record"),
        ):
            result = await ask.handle_ask("what time is it", "kitchen")

        self.assertTrue(result["ok"])
        self.assertEqual(result["response"], "It's test time.")
        filler.assert_not_called()


if __name__ == "__main__":
    unittest.main()
