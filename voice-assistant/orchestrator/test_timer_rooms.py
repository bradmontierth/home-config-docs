"""Timers belong to the room they were set in.

The rule under test is Brad's: a spoken stop must never silence an alarm in a
room the speaker is not standing in. "Cancel all timers" is the one deliberate
way to reach the whole house.
"""

import asyncio
import os
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

from . import config, timers


class TimerRoomScopeTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        for name, value in (("DB_PATH", os.path.join(tmp.name, "t.db")),
                            ("ANNOUNCE_CACHE_DIR", os.path.join(tmp.name, "a"))):
            patcher = patch.object(config, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        # TTS prerender is not what this file is about.
        patcher = patch.object(timers.clients, "synthesize",
                               new=AsyncMock(return_value=b"RIFF"))
        patcher.start()
        self.addCleanup(patcher.stop)
        self.engine = timers.TimerEngine()

    def _add(self, label, sat, seconds=600):
        return asyncio.run(self.engine.create(label, seconds, "marimba", sat))

    def _ring(self, timer_id):
        self.engine._db.execute("UPDATE timers SET state=? WHERE id=?",
                                (timers.RINGING, timer_id))
        self.engine._db.commit()

    def test_create_records_the_room(self):
        t = self._add("pasta", "kitchen")
        self.assertEqual(self.engine.get(t["id"])["sat"], "kitchen")

    def test_stop_in_one_room_leaves_the_other_ringing(self):
        kitchen = self._add("pasta", "kitchen")
        bath = self._add("shower", "master")
        self._ring(kitchen["id"])
        self._ring(bath["id"])

        stopped = self.engine.dismiss_any_ringing("master")
        self.assertEqual(stopped["id"], bath["id"])
        self.assertEqual(self.engine.get(kitchen["id"])["state"], timers.RINGING)

    def test_unlabelled_cancel_stays_in_its_room(self):
        kitchen = self._add("pasta", "kitchen")
        self._add("shower", "master")
        cancelled = self.engine.cancel(None, "master")
        self.assertEqual(cancelled["sat"], "master")
        self.assertEqual(self.engine.get(kitchen["id"])["state"], timers.RUNNING)

    def test_a_room_with_no_timers_cancels_nothing(self):
        """The failure that matters: reaching out of your room by accident."""
        kitchen = self._add("pasta", "kitchen")
        self.assertIsNone(self.engine.cancel(None, "master"))
        self.assertIsNone(self.engine.dismiss_any_ringing("master"))
        self.assertEqual(self.engine.get(kitchen["id"])["state"], timers.RUNNING)

    def test_naming_a_timer_reaches_across_the_house(self):
        """A label is explicit enough to mean a timer elsewhere — only the
        unlabelled 'stop' is confined to the room."""
        kitchen = self._add("pasta", "kitchen")
        self._add("shower", "master")
        cancelled = self.engine.cancel("pasta", "master")
        self.assertEqual(cancelled["id"], kitchen["id"])

    def test_local_label_wins_over_an_identical_one_elsewhere(self):
        self._add("laundry", "kitchen")
        mine = self._add("laundry", "master")
        self.assertEqual(self.engine.cancel("laundry", "master")["id"], mine["id"])

    def test_rename_preserves_timer_and_rerenders_announcement(self):
        original = self._add(None, "kitchen")
        with patch.object(timers.clients, "synthesize",
                          new=AsyncMock(return_value=b"RIFF-new")) as synth:
            renamed = asyncio.run(self.engine.rename(None, "pasta", "kitchen"))
        self.assertEqual(renamed["id"], original["id"])
        self.assertEqual(renamed["label"], "pasta")
        self.assertEqual(renamed["duration_seconds"], original["duration_seconds"])
        self.assertEqual(renamed["sound_theme"], original["sound_theme"])
        self.assertEqual(renamed["state"], timers.RUNNING)
        self.assertTrue(renamed["has_announcement"])
        synth.assert_awaited_once_with("Your pasta timer is done.")

    def test_unlabelled_rename_stays_in_its_room(self):
        kitchen = self._add("pasta", "kitchen")
        master = self._add("shower", "master")
        renamed = asyncio.run(self.engine.rename(None, "bath", "master"))
        self.assertEqual(renamed["id"], master["id"])
        self.assertEqual(renamed["label"], "bath")
        self.assertEqual(self.engine.get(kitchen["id"])["label"], "pasta")

    def test_rename_in_a_room_with_no_timer_changes_nothing(self):
        kitchen = self._add("pasta", "kitchen")
        renamed = asyncio.run(self.engine.rename(None, "bath", "master"))
        self.assertIsNone(renamed)
        self.assertEqual(self.engine.get(kitchen["id"])["label"], "pasta")

    def test_cancel_all_is_the_house_wide_escape(self):
        kitchen = self._add("pasta", "kitchen")
        bath = self._add("shower", "master")
        self.assertEqual(len(self.engine.cancel_all()), 2)
        for t in (kitchen, bath):
            self.assertEqual(self.engine.get(t["id"])["state"], timers.CANCELLED)

    def test_active_defaults_to_the_whole_house(self):
        self._add("pasta", "kitchen")
        self._add("shower", "master")
        self.assertEqual(len(self.engine.active()), 2)        # kitchen display
        self.assertEqual(len(self.engine.active("master")), 1)

    def test_timers_from_before_the_column_still_work(self):
        """Rows written by the old code read back as sat NULL. They must not
        vanish from the display or become uncancellable."""
        t = self._add("legacy", "kitchen")
        self.engine._db.execute("UPDATE timers SET sat=NULL WHERE id=?", (t["id"],))
        self.engine._db.commit()
        self.assertEqual(len(self.engine.active()), 1)
        self.assertIsNone(self.engine.cancel(None, "master"))   # scoped: not mine
        self.assertEqual(len(self.engine.cancel_all()), 1)      # house-wide: caught


if __name__ == "__main__":
    unittest.main()


class ActiveScopeTest(TimerRoomScopeTest):
    """What each room's board is allowed to show."""

    def test_default_scope_is_the_whole_house(self):
        self._add(None, "kitchen")
        self._add(None, "master")
        self.assertEqual(len(self.engine.active()), 2)

    def test_a_room_sees_only_its_own(self):
        self._add("pasta", "kitchen")
        self._add("laundry", "master")
        rooms = self.engine.active("master")
        self.assertEqual([t["label"] for t in rooms], ["laundry"])

    def test_the_default_room_keeps_timers_that_predate_the_column(self):
        """Rows with a NULL sat were all kitchen timers. Scoping the board to
        the kitchen must not drop them off it."""
        tid = self._add("old", "kitchen")
        self.engine._db.execute("UPDATE timers SET sat=NULL WHERE id=?", (tid["id"],))
        self.engine._db.commit()
        self.assertEqual([t["label"] for t in self.engine.active(config.DEFAULT_SAT)],
                         ["old"])

    def test_another_room_does_not_inherit_them(self):
        tid = self._add("old", "kitchen")
        self.engine._db.execute("UPDATE timers SET sat=NULL WHERE id=?", (tid["id"],))
        self.engine._db.commit()
        self.assertEqual(self.engine.active("master"), [])
