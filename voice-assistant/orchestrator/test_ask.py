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
    def test_zone_routed_satellite_never_uses_legacy_filler(self):
        with patch.object(
            ask.zones,
            "route_for",
            return_value={"rooms": ["simon"]},
        ):
            self.assertFalse(ask._should_play_legacy_filler("simon"))

    def test_unrouted_satellite_retains_legacy_filler(self):
        with patch.object(ask.zones, "route_for", return_value=None):
            self.assertTrue(ask._should_play_legacy_filler("kitchen"))


class AskFillerDispatchTest(unittest.IsolatedAsyncioTestCase):
    async def test_simon_ask_does_not_schedule_kitchen_filler(self):
        async def answer_chunks():
            yield "It's test time."

        filler = AsyncMock()
        with (
            patch.object(
                ask.zones,
                "route_for",
                return_value={"rooms": ["simon"]},
            ),
            patch.object(ask, "_play_filler", new=filler),
            patch.object(ask.openrouter, "stream_chat", return_value=answer_chunks()),
            patch.object(ask.events, "emit", new=AsyncMock()),
            patch.object(ask.answers_mod, "record"),
        ):
            result = await ask.handle_ask("what time is it", "simon")

        self.assertTrue(result["ok"])
        filler.assert_not_called()


if __name__ == "__main__":
    unittest.main()
