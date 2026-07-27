import asyncio
import json
import os
import shutil
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

from . import find_phone


def _handle(owner, action="ring"):
    return asyncio.run(find_phone.handle(
        {"phone_owner": owner, "phone_action": action}))


class FindPhoneResolveTest(unittest.TestCase):
    """Owner resolution against the seed table."""

    def test_names_and_family_words(self):
        cases = {
            "brad": "brad",
            "brad's": "brad",
            "dad": "brad",
            "adrienne": "adrienne",
            "adrienne's": "adrienne",
            "mom": "adrienne",
        }
        for text, key in cases.items():
            with self.subTest(text=text):
                entry = find_phone.resolve(text)
                self.assertIsNotNone(entry, text)
                self.assertEqual(entry["key"], key)

    def test_followup_answer_phrasings(self):
        # Raw follow-up answers to "Whose phone?" — filler stripped first.
        cases = {
            "Brad's phone.": "brad",
            "ring adrienne's": "adrienne",
            "mom's phone": "adrienne",
        }
        for text, key in cases.items():
            with self.subTest(text=text):
                entry = find_phone.resolve(text)
                self.assertIsNotNone(entry, text)
                self.assertEqual(entry["key"], key)

    def test_asr_noise_absorbed(self):
        entry = find_phone.resolve("adrian's phone")
        self.assertIsNotNone(entry)
        self.assertEqual(entry["key"], "adrienne")

    def test_unknown_and_self_words_resolve_to_none(self):
        for text in ("simon", "grandma", "my phone", "mine", "", "the phone"):
            with self.subTest(text=text):
                self.assertIsNone(find_phone.resolve(text))


class FindPhonePayloadTest(unittest.TestCase):
    """The ring notification: alarm-stream channel, dismissible, stoppable."""

    def test_ring_payload(self):
        entry = find_phone.resolve("brad")
        payload = find_phone._ring_payload(entry)
        self.assertIn("Brad's phone", payload["message"])
        data = payload["data"]
        self.assertEqual(data["channel"], "alarm_stream")
        self.assertEqual(data["tag"], "find_phone")
        self.assertEqual(data["ttl"], 0)
        self.assertEqual(data["priority"], "high")
        self.assertGreater(data["timeout"], 0)
        self.assertEqual(data["actions"][0]["action"], "FIND_PHONE_FOUND")

    def test_clear_payload(self):
        payload = find_phone._clear_payload()
        self.assertEqual(payload["message"], "clear_notification")
        self.assertEqual(payload["data"]["tag"], "find_phone")


class FindPhoneVolumeTest(unittest.TestCase):
    """Alarm volume is pegged for the ring window and always handed back."""

    def setUp(self):
        patcher = patch.object(find_phone, "_post", new=AsyncMock())
        self.post = patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(find_phone.stop)

    def _volume_calls(self):
        return [p for _, p in (c.args for c in self.post.await_args_list)
                if p.get("message") == "command_volume_level"]

    def test_peg_and_restore_around_ring(self):
        with patch.object(find_phone, "_alarm_volume",
                          new=AsyncMock(return_value=4)), \
             patch.object(find_phone.config, "FIND_PHONE_INTERVAL_S", 0), \
             patch.object(find_phone.config, "FIND_PHONE_REPEATS", 2):
            async def scenario():
                await find_phone.ring_and_reply(find_phone.resolve("brad"))
                await find_phone._ring_task
            asyncio.run(scenario())
        levels = [p["data"]["command"] for p in self._volume_calls()]
        self.assertEqual(levels, [find_phone.config.FIND_PHONE_MAX_VOLUME, 4])
        self.assertEqual(self._volume_calls()[0]["data"]["media_stream"],
                         "alarm_stream")

    def test_restore_on_early_stop(self):
        with patch.object(find_phone, "_alarm_volume",
                          new=AsyncMock(return_value=3)), \
             patch.object(find_phone.config, "FIND_PHONE_INTERVAL_S", 60):
            async def scenario():
                await find_phone.ring_and_reply(find_phone.resolve("brad"))
                task = find_phone._ring_task
                await asyncio.sleep(0)
                find_phone.stop()
                await asyncio.gather(task, return_exceptions=True)
            asyncio.run(scenario())
        levels = [p["data"]["command"] for p in self._volume_calls()]
        self.assertEqual(levels, [find_phone.config.FIND_PHONE_MAX_VOLUME, 3])

    def test_unreadable_volume_means_no_peg(self):
        """No sensor -> never touch the setting (a guessed restore would
        silently re-tune their real alarm)."""
        with patch.object(find_phone, "_alarm_volume",
                          new=AsyncMock(return_value=None)), \
             patch.object(find_phone.config, "FIND_PHONE_INTERVAL_S", 0), \
             patch.object(find_phone.config, "FIND_PHONE_REPEATS", 1):
            async def scenario():
                await find_phone.ring_and_reply(find_phone.resolve("brad"))
                await find_phone._ring_task
            asyncio.run(scenario())
        self.assertEqual(self._volume_calls(), [])

    def _fake_active_ring(self):
        """A not-done _ring_task, as the readback requires."""
        async def forever():
            await asyncio.sleep(3600)
        return forever()

    def test_peg_readback_warns_when_ignored(self):
        """No Do Not Disturb access -> the app drops the volume command and
        only the phone shows a notice; we must log it ourselves."""
        async def scenario():
            find_phone._ring_task = asyncio.create_task(self._fake_active_ring())
            self.addCleanup(find_phone._ring_task.cancel)
            with patch.object(find_phone.asyncio, "sleep", new=AsyncMock()), \
                 patch.object(find_phone, "_alarm_volume",
                              new=AsyncMock(return_value=3)), \
                 self.assertLogs(find_phone.log, "WARNING") as logs:
                await find_phone._warn_if_peg_ignored(
                    find_phone.resolve("brad"), 3)
            return "\n".join(logs.output)
        self.assertIn("Do Not Disturb", asyncio.run(scenario()))

    def test_peg_readback_skipped_once_ring_is_over(self):
        """An early stop restores the level BEFORE the readback, which looks
        identical to a dropped command — must not cry wolf (fired live on a
        +6s tap 2026-07-24)."""
        find_phone._ring_task = None
        with patch.object(find_phone.asyncio, "sleep", new=AsyncMock()), \
             patch.object(find_phone, "_alarm_volume",
                          new=AsyncMock(return_value=3)) as vol:
            asyncio.run(find_phone._warn_if_peg_ignored(
                find_phone.resolve("brad"), 3))
        vol.assert_not_awaited()

    def test_peg_readback_quiet_when_effective(self):
        async def scenario():
            find_phone._ring_task = asyncio.create_task(self._fake_active_ring())
            self.addCleanup(find_phone._ring_task.cancel)
            with patch.object(find_phone.asyncio, "sleep", new=AsyncMock()), \
                 patch.object(find_phone, "_alarm_volume",
                              new=AsyncMock(return_value=7)), \
                 self.assertLogs(find_phone.log, "INFO") as logs:
                await find_phone._warn_if_peg_ignored(
                    find_phone.resolve("brad"), 3)
            return "\n".join(logs.output)
        joined = asyncio.run(scenario())
        self.assertIn("peg confirmed", joined)
        self.assertNotIn("Do Not Disturb", joined)

    def test_peg_disabled_by_config(self):
        with patch.object(find_phone.config, "FIND_PHONE_PEG_VOLUME", False):
            self.assertIsNone(asyncio.run(
                find_phone._alarm_volume(find_phone.resolve("brad"))))

    def test_implausible_sensor_value_refuses_to_peg(self):
        """A percentage-scale sensor (e.g. 71) must NOT be trusted: restoring
        71 would clamp to max and leave their real alarm at full volume."""
        entry = find_phone.resolve("brad")
        for state, expect in (("4", 4), ("71", None), ("-1", None),
                              ("unavailable", None)):
            with self.subTest(state=state):
                class _R:
                    status_code = 200
                    @staticmethod
                    def raise_for_status(): pass
                    @staticmethod
                    def json(): return {"state": state}
                with patch.object(find_phone, "_token", return_value="t"), \
                     patch.object(find_phone.httpx, "AsyncClient") as client:
                    client.return_value.__aenter__ = AsyncMock(
                        return_value=AsyncMock(get=AsyncMock(return_value=_R)))
                    client.return_value.__aexit__ = AsyncMock(
                        return_value=False)
                    self.assertEqual(
                        asyncio.run(find_phone._alarm_volume(entry)), expect)


class FindPhoneStrandedVolumeTest(unittest.TestCase):
    """A restart mid-ring must not leave the alarm pegged at max."""

    def setUp(self):
        patcher = patch.object(find_phone, "_post", new=AsyncMock())
        self.post = patcher.start()
        self.addCleanup(patcher.stop)
        self.tmp = tempfile.mkdtemp()
        patcher_f = patch.object(
            find_phone.config, "FIND_PHONE_VOLUME_STATE_FILE",
            os.path.join(self.tmp, "find_phone_volume.json"))
        patcher_f.start()
        self.addCleanup(patcher_f.stop)
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.addCleanup(find_phone.stop)

    @property
    def _journal(self):
        return find_phone.config.FIND_PHONE_VOLUME_STATE_FILE

    def test_journal_written_on_peg_and_cleared_on_restore(self):
        with patch.object(find_phone, "_alarm_volume",
                          new=AsyncMock(return_value=5)), \
             patch.object(find_phone.config, "FIND_PHONE_INTERVAL_S", 0), \
             patch.object(find_phone.config, "FIND_PHONE_REPEATS", 1):
            async def scenario():
                await find_phone.ring_and_reply(find_phone.resolve("brad"))
                self.assertTrue(os.path.exists(self._journal))
                with open(self._journal) as fh:
                    self.assertEqual(json.load(fh),
                                     {"phone": "brad", "prior": 5})
                await find_phone._ring_task
            asyncio.run(scenario())
        self.assertFalse(os.path.exists(self._journal))

    def test_startup_restores_stranded_level(self):
        with open(self._journal, "w") as fh:
            json.dump({"phone": "brad", "prior": 2}, fh)
        asyncio.run(find_phone.restore_stranded())
        _, payload = self.post.await_args.args
        self.assertEqual(payload["message"], "command_volume_level")
        self.assertEqual(payload["data"]["command"], 2)
        self.assertFalse(os.path.exists(self._journal))

    def test_startup_noop_without_journal(self):
        asyncio.run(find_phone.restore_stranded())
        self.post.assert_not_awaited()

    def test_startup_drops_corrupt_or_unknown_journal(self):
        for content in ("{not json", '{"phone": "nobody", "prior": 2}',
                        '{"phone": "brad", "prior": "loud"}'):
            with self.subTest(content=content):
                with open(self._journal, "w") as fh:
                    fh.write(content)
                asyncio.run(find_phone.restore_stranded())
                self.assertFalse(os.path.exists(self._journal))
        self.post.assert_not_awaited()


class FindPhoneHandleTest(unittest.TestCase):
    """Handle flows — HA notify POST + volume read mocked out."""

    def setUp(self):
        patcher = patch.object(find_phone, "_post", new=AsyncMock())
        self.post = patcher.start()
        self.addCleanup(patcher.stop)
        # Volume pegging has its own test class; keep it out of these counts.
        patcher_v = patch.object(find_phone, "_alarm_volume",
                                 new=AsyncMock(return_value=None))
        patcher_v.start()
        self.addCleanup(patcher_v.stop)
        # Keep any background loop from re-posting during a test.
        patcher_i = patch.object(find_phone.config, "FIND_PHONE_INTERVAL_S", 60)
        patcher_i.start()
        self.addCleanup(patcher_i.stop)
        self.addCleanup(find_phone.stop)

    def test_named_owner_rings_first_post_synchronous(self):
        result = _handle("adrienne")
        self.assertTrue(result["ok"])
        self.assertEqual(result["phone"]["key"], "adrienne")
        self.assertIn("Adrienne's phone", result["response"])
        # First call is the synchronous ring (asyncio.run teardown cancels the
        # background loop, which appends a clear_notification call after it).
        service, payload = self.post.await_args_list[0].args
        self.assertEqual(service, "mobile_app_pixel_9_pro")
        self.assertEqual(payload["data"]["channel"], "alarm_stream")

    def test_my_asks_whose_without_ringing(self):
        for owner in ("my", "mine", None):
            with self.subTest(owner=owner):
                result = _handle(owner)
                self.assertFalse(result["ok"])
                self.assertTrue(result["needs_owner"])
                self.assertIn("Whose phone", result["response"])
        self.post.assert_not_awaited()

    def test_unknown_owner_refuses_without_ringing(self):
        result = _handle("simon")
        self.assertFalse(result["ok"])
        self.assertNotIn("needs_owner", result)
        self.assertIn("Brad's phone", result["response"])
        self.post.assert_not_awaited()

    def test_stop_without_ring_says_so(self):
        result = _handle(None, action="stop")
        self.assertTrue(result["ok"])
        self.assertFalse(result["stopped"])
        self.post.assert_not_awaited()

    def test_stop_cancels_loop_and_clears_notification(self):
        async def scenario():
            await find_phone.ring_and_reply(find_phone.resolve("brad"))
            task = find_phone._ring_task
            await asyncio.sleep(0)   # let the loop task start (park in sleep)
            self.assertTrue(find_phone.stop())
            await asyncio.gather(task, return_exceptions=True)
            self.assertFalse(find_phone.stop())
        asyncio.run(scenario())
        # First ring + clear_notification on cancel.
        self.assertEqual(self.post.await_count, 2)
        _, clear = self.post.await_args_list[1].args
        self.assertEqual(clear["message"], "clear_notification")

    def test_ring_loop_reposts(self):
        with patch.object(find_phone.config, "FIND_PHONE_INTERVAL_S", 0), \
             patch.object(find_phone.config, "FIND_PHONE_REPEATS", 3):
            async def scenario():
                await find_phone.ring_and_reply(find_phone.resolve("brad"))
                await find_phone._ring_task
            asyncio.run(scenario())
        # 1 synchronous + 2 background re-posts, no clear on natural end
        # (the notification's own timeout dismisses it).
        self.assertEqual(self.post.await_count, 3)


if __name__ == "__main__":
    unittest.main()
