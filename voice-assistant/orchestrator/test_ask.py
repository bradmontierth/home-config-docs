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


if __name__ == "__main__":
    unittest.main()
