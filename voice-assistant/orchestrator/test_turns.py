"""Turn-telemetry tests.

The bar these are holding: telemetry is allowed to lose data, never to break a
turn. Several of these assert that a broken database is survivable.
"""

import asyncio
import os
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from . import config, timing, turns


class TurnsTest(unittest.TestCase):
    def setUp(self) -> None:
        self._dir = tempfile.TemporaryDirectory()
        self._prev = config.DB_PATH
        config.DB_PATH = os.path.join(self._dir.name, "orchestrator.db")
        turns._db = None

    def tearDown(self) -> None:
        config.DB_PATH = self._prev
        turns._db = None
        self._dir.cleanup()

    # -- basics ------------------------------------------------------------
    def test_start_then_update_is_one_row(self):
        tid = turns.start("kitchen", "wake", verified=True, wake_score=91.0)
        turns.update(tid, chime_ms=226, rtt_ms=393)
        turns.update(tid, intent="set_timer", response="Okay, 12 minutes.")
        rows = turns.recent()
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row["sat"], "kitchen")
        self.assertEqual(row["verified"], 1)
        self.assertEqual(row["chime_ms"], 226)
        self.assertEqual(row["intent"], "set_timer")
        self.assertEqual(row["wake_score"], 91.0)

    def test_update_never_blanks_an_earlier_write(self):
        """The command POST knows nothing about chime_ms and must not erase the
        back-post that already landed."""
        tid = turns.start("kitchen", "wake")
        turns.update(tid, chime_ms=226)
        turns.update(tid, chime_ms=None, intent="weather")
        row = turns.recent()[0]
        self.assertEqual(row["chime_ms"], 226)
        self.assertEqual(row["intent"], "weather")

    def test_unknown_turn_id_is_a_noop(self):
        """A satellite running code that predates this feature, or one that
        restarted mid-turn, must not produce an error."""
        turns.update("nonexistent", chime_ms=1)
        turns.update(None, chime_ms=1)
        self.assertEqual(turns.recent(), [])

    def test_unknown_field_is_dropped_not_raised(self):
        tid = turns.start("kitchen", "wake", nonsense_column=1, intent="ask")
        self.assertEqual(turns.recent()[0]["intent"], "ask")
        turns.update(tid, another_bogus_one="x")
        self.assertEqual(len(turns.recent()), 1)

    def test_slots_dict_is_json_encoded(self):
        tid = turns.start("kitchen", "wake")
        turns.update(tid, slots={"label": "chicken", "seconds": 720})
        self.assertIn("chicken", turns.recent()[0]["slots"])

    def test_recent_filters_by_sat(self):
        turns.start("kitchen", "wake")
        turns.start("master", "wake")
        self.assertEqual(len(turns.recent(sat="master")), 1)
        self.assertEqual(len(turns.recent()), 2)

    # -- finish ------------------------------------------------------------
    def test_finish_derives_handler_as_residual(self):
        tid = turns.start("kitchen", "wake")
        turns.finish(tid, {"intent": "weather", "response": "Sunny.", "ok": True},
                     timings={"asr": 300, "classify": 200, "tts": 100},
                     total_ms=900)
        row = turns.recent()[0]
        self.assertEqual(row["asr_ms"], 300)
        self.assertEqual(row["classify_ms"], 200)
        self.assertEqual(row["tts_ms"], 100)
        self.assertEqual(row["handler_ms"], 300)
        self.assertEqual(row["ok"], 1)

    def test_finish_clamps_negative_handler(self):
        """Stage timers accumulate re-entrant calls, so the parts can exceed
        the outer wall clock on a slot-fill turn."""
        tid = turns.start("kitchen", "wake")
        turns.finish(tid, {"intent": "ask"},
                     timings={"asr": 800, "classify": 800}, total_ms=900)
        self.assertEqual(turns.recent()[0]["handler_ms"], 0)

    def test_note_speaker_keeps_unsure(self):
        """An unsure verdict is exactly the data enrollment needs — store it."""
        tid = turns.start("kitchen", "wake")
        turns.note_speaker(tid, {"speaker": "unsure", "score": 0.11,
                                 "margin": 0.21})
        row = turns.recent()[0]
        self.assertEqual(row["speaker"], "unsure")
        self.assertAlmostEqual(row["speaker_score"], 0.11)

    def test_note_speaker_ignores_none(self):
        tid = turns.start("kitchen", "wake")
        turns.note_speaker(tid, None)
        self.assertIsNone(turns.recent()[0]["speaker"])

    # -- retention ---------------------------------------------------------
    def test_prune_is_off_by_default(self):
        self.assertIsNone(config.TURNS_MAX_ROWS)
        for _ in range(25):
            turns.start("kitchen", "wake")
        self.assertEqual(len(turns.recent(limit=100)), 25)

    def test_prune_keeps_newest_when_enabled(self):
        with patch.object(config, "TURNS_MAX_ROWS", 5):
            for _ in range(12):
                turns.start("kitchen", "wake")
            rows = turns.recent(limit=100)
        self.assertEqual(len(rows), 5)

    # -- failure is survivable --------------------------------------------
    def test_wedged_database_never_raises(self):
        """A directory where the database file should be: every entry point
        must degrade to a log line."""
        config.DB_PATH = os.path.join(self._dir.name, "wedged.db")
        os.makedirs(config.DB_PATH)
        turns._db = None
        tid = turns.start("kitchen", "wake", verified=True)
        self.assertTrue(tid)                    # caller still gets an id
        turns.update(tid, chime_ms=1)
        turns.finish(tid, {"intent": "none"}, timings={}, total_ms=1)
        turns.note_speaker(tid, {"speaker": "brad"})
        self.assertEqual(turns.recent(), [])

    def test_wal_is_enabled(self):
        """The dashboard reads this file from another container; under the
        default rollback journal a slow read can block a turn's write."""
        turns.start("kitchen", "wake")
        mode = sqlite3.connect(config.DB_PATH).execute(
            "PRAGMA journal_mode").fetchone()[0]
        self.assertEqual(str(mode).lower(), "wal")


class TimingTest(unittest.TestCase):
    def test_stages_accumulate(self):
        async def run():
            timing.start()
            for _ in range(2):
                with timing.stage("asr"):
                    await asyncio.sleep(0.01)
            return timing.snapshot()
        snap = asyncio.run(run())
        self.assertGreaterEqual(snap["asr"], 15)

    def test_outside_a_turn_is_a_noop(self):
        """Alarm-window ASR and the filler pre-render are not part of anybody's
        turn and must not accumulate onto one."""
        async def run():
            with timing.stage("asr"):
                await asyncio.sleep(0.001)
            return timing.snapshot()
        self.assertEqual(asyncio.run(run()), {})

    def test_turns_are_isolated_across_concurrent_tasks(self):
        """Two satellites' turns interleave on the event loop; each task gets
        its own copy of the context var."""
        async def one(name, delay):
            timing.start()
            with timing.stage("asr"):
                await asyncio.sleep(delay)
            return name, timing.snapshot()

        async def run():
            return await asyncio.gather(one("a", 0.05), one("b", 0.005))

        results = dict(asyncio.run(run()))
        self.assertGreater(results["a"]["asr"], results["b"]["asr"])

    def test_stage_records_even_when_the_body_raises(self):
        async def run():
            timing.start()
            try:
                with timing.stage("classify"):
                    raise RuntimeError("LLM down")
            except RuntimeError:
                pass
            return timing.snapshot()
        self.assertIn("classify", asyncio.run(run()))


if __name__ == "__main__":
    unittest.main()
