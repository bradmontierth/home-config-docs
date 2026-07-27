import asyncio
import unittest
from unittest.mock import AsyncMock, patch

from . import broadcast


def _handle(query, target):
    return asyncio.run(broadcast.handle(
        {"query": query, "broadcast_target": target}))


class BroadcastResolveTest(unittest.TestCase):
    """Target resolution against the seed table."""

    def test_named_rooms(self):
        cases = {
            "simon": ["simon"],
            "simon's room": ["simon"],
            "claire": ["claire"],
            "the loft": ["loft"],
            "the master bedroom": ["master"],
            "the kids": ["simon", "claire"],
        }
        for target, rooms in cases.items():
            with self.subTest(target=target):
                got, _, matched = broadcast.resolve(target)
                self.assertTrue(matched, target)
                self.assertEqual(got, rooms)

    def test_no_target_is_all_and_matched(self):
        rooms, _, matched = broadcast.resolve(None)
        self.assertEqual(rooms, "all")
        self.assertTrue(matched)

    def test_unknown_target_falls_back_to_all_unmatched(self):
        rooms, _, matched = broadcast.resolve("grandma")
        self.assertEqual(rooms, "all")
        self.assertFalse(matched)

    def test_asr_noise_absorbed(self):
        rooms, _, matched = broadcast.resolve("simons room")
        self.assertEqual(rooms, ["simon"])
        self.assertTrue(matched)


class BroadcastHandleTest(unittest.TestCase):
    """Publish payload + spoken confirmation — MQTT publish mocked out."""

    def setUp(self):
        patcher = patch.object(broadcast, "_publish", new=AsyncMock())
        self.publish = patcher.start()
        self.addCleanup(patcher.stop)

    def _payload(self):
        return self.publish.await_args.args[0]

    def test_named_target_payload_and_confirm(self):
        result = _handle("Simon, come eat dinner", "simon")
        self.assertTrue(result["ok"])
        self.assertEqual(self._payload()["rooms"], ["simon"])
        self.assertEqual(self._payload()["message"], "Simon, come eat dinner")
        self.assertIn("Simon's room", result["response"])

    def test_no_target_broadcasts_all(self):
        result = _handle("Dinner is ready", None)
        self.assertTrue(result["ok"])
        self.assertEqual(self._payload()["rooms"], "all")

    def test_unknown_target_says_so(self):
        result = _handle("Happy birthday", "grandma")
        self.assertTrue(result["ok"])
        self.assertEqual(self._payload()["rooms"], "all")
        self.assertIn("grandma", result["response"])

    def test_empty_message_refuses_without_publishing(self):
        result = _handle("", "simon")
        self.assertFalse(result["ok"])
        self.publish.assert_not_awaited()


class BroadcastRestTest(unittest.TestCase):
    """rooms_list ordering + direct send validation — publish mocked out."""

    def setUp(self):
        patcher = patch.object(broadcast, "_publish", new=AsyncMock())
        self.publish = patcher.start()
        self.addCleanup(patcher.stop)

    def test_rooms_list_puts_all_first(self):
        rooms = broadcast.rooms_list()
        self.assertEqual(rooms[0]["key"], "all")
        self.assertIn("spoken", rooms[0])
        keys = [r["key"] for r in rooms]
        self.assertIn("kids", keys)

    def test_send_valid_rooms(self):
        sent = asyncio.run(broadcast.send(["simon", "claire"], "Dinner"))
        self.assertEqual(sent["rooms"], ["simon", "claire"])
        self.publish.assert_awaited_once()

    def test_send_all(self):
        sent = asyncio.run(broadcast.send("all", "Dinner"))
        self.assertEqual(sent["rooms"], "all")

    def test_send_volume_override(self):
        sent = asyncio.run(broadcast.send(["loft"], "Test", volume=10))
        self.assertEqual(sent["volume"], 10)

    def test_send_rejects_unknown_room(self):
        with self.assertRaises(ValueError):
            asyncio.run(broadcast.send(["garage"], "Dinner"))
        self.publish.assert_not_awaited()

    def test_send_rejects_empty(self):
        with self.assertRaises(ValueError):
            asyncio.run(broadcast.send(["simon"], "  "))
        with self.assertRaises(ValueError):
            asyncio.run(broadcast.send([], "Dinner"))
        self.publish.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
