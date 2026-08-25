import asyncio
import unittest
from unittest.mock import AsyncMock, patch

from . import policy


# A guarded rule of the shape the kid rooms used until 2026-08-25. The live
# seed now has quiet hours and guards disabled, so the tests carry their own.
_GUARDED = {"simon": {"timezone": "America/Denver", "quiet_start": "20:00",
                      "quiet_end": "07:00", "guard_entity": "input_boolean.simonalarm",
                      "guard_blocking_state": "on", "fail_closed": True}}


class SatellitePolicyTest(unittest.TestCase):
    def setUp(self):
        policy._state_cache.clear()
        patcher = patch.object(policy, "_table", return_value=_GUARDED)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_disabled_window_never_matches(self):
        rule = {"quiet_start": "00:00", "quiet_end": "00:00"}
        self.assertFalse(policy._in_quiet_hours(rule))

    def test_unlisted_satellites_are_unchanged(self):
        result = asyncio.run(policy.evaluate("kitchen"))
        self.assertTrue(result["allowed"])
        self.assertEqual(result["reason"], "no_policy")

    def test_quiet_hours_block_before_ha_lookup(self):
        with patch.object(policy, "_in_quiet_hours", return_value=True), \
             patch.object(policy, "_guard_state", new=AsyncMock()) as guard:
            result = asyncio.run(policy.evaluate("simon"))
        self.assertFalse(result["allowed"])
        self.assertEqual(result["reason"], "quiet_hours")
        guard.assert_not_awaited()

    def test_sleep_guard_blocks_during_day(self):
        with patch.object(policy, "_in_quiet_hours", return_value=False), \
             patch.object(policy, "_guard_state",
                          new=AsyncMock(return_value=("on", None))):
            result = asyncio.run(policy.evaluate("simon"))
        self.assertFalse(result["allowed"])
        self.assertEqual(result["reason"], "guard_entity")

    def test_guard_failure_is_closed(self):
        with patch.object(policy, "_in_quiet_hours", return_value=False), \
             patch.object(policy, "_guard_state",
                          new=AsyncMock(return_value=(None, "Timeout"))):
            result = asyncio.run(policy.evaluate("simon"))
        self.assertFalse(result["allowed"])
        self.assertEqual(result["reason"], "guard_unavailable")


if __name__ == "__main__":
    unittest.main()
