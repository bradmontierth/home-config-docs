"""The answers shelf: what survives, what gets patched, what falls off the end.

The behaviour worth pinning down is the two-phase write. A streamed ask files
its row while only the spoken part exists and patches the body in later, so the
failure that matters is a stream that dies in between: the row must still hold
a real answer.
"""

import os
import tempfile
import unittest

from . import answers, config


class AnswersStoreTest(unittest.TestCase):
    def setUp(self):
        self._dir = tempfile.TemporaryDirectory()
        self._prev_path = config.DB_PATH
        config.DB_PATH = os.path.join(self._dir.name, "orchestrator.db")
        answers._db = None          # force a fresh connection to the temp file

    def tearDown(self):
        if answers._db is not None:
            answers._db.close()
        answers._db = None
        config.DB_PATH = self._prev_path
        self._dir.cleanup()

    def test_spoken_answer_survives_a_stream_that_never_finishes(self):
        row_id = answers.record("who won the game", "The Jazz won, 112 to 104.")
        # No finish() call — the background streamer died.
        got = answers.get(row_id)
        self.assertEqual(got["question"], "who won the game")
        self.assertEqual(got["full"], "The Jazz won, 112 to 104.")

    def test_finish_patches_body_and_price(self):
        row_id = answers.record("who won the game", "The Jazz won.")
        answers.finish(row_id, "The Jazz beat the Suns 112-104 at home.",
                       {"cost": 0.0412, "searches": 1, "tokens": 3120})
        got = answers.get(row_id)
        self.assertEqual(got["full"], "The Jazz beat the Suns 112-104 at home.")
        self.assertAlmostEqual(got["cost_usd"], 0.0412)
        self.assertEqual(got["searches"], 1)
        self.assertEqual(got["tokens"], 3120)

    def test_finish_without_stats_keeps_numbers_already_recorded(self):
        row_id = answers.record("how tall is denali", "About 20,310 feet.",
                                stats={"cost": 0.02, "searches": 1, "tokens": 900})
        answers.finish(row_id, "Denali rises 20,310 feet above sea level.")
        got = answers.get(row_id)
        self.assertAlmostEqual(got["cost_usd"], 0.02)
        self.assertEqual(got["searches"], 1)

    def test_recent_is_newest_first_and_omits_bodies(self):
        for i in range(3):
            answers.record(f"question {i}", f"answer {i}", f"full {i}")
        rows = answers.recent(10)
        self.assertEqual([r["question"] for r in rows],
                         ["question 2", "question 1", "question 0"])
        self.assertNotIn("full", rows[0])

    def test_a_blank_answer_is_not_stored(self):
        self.assertIsNone(answers.record("", "something"))
        self.assertIsNone(answers.record("a question", "   "))
        self.assertEqual(answers.recent(10), [])

    def test_old_rows_fall_off_the_end(self):
        answers.MAX_ROWS, prev = 5, answers.MAX_ROWS
        try:
            for i in range(8):
                answers.record(f"question {i}", f"answer {i}")
            rows = answers.recent(50)
            self.assertEqual(len(rows), 5)
            self.assertEqual(rows[-1]["question"], "question 3")
        finally:
            answers.MAX_ROWS = prev

    def test_a_broken_database_never_raises_at_the_caller(self):
        # A directory where the database file should be: unopenable by anyone,
        # including root (permission tricks don't hold — the orchestrator's
        # container runs as root, and this has to fail there too).
        answers._db = None
        config.DB_PATH = os.path.join(self._dir.name, "wedged.db")
        os.makedirs(config.DB_PATH)
        self.assertIsNone(answers.record("q", "a"))
        self.assertEqual(answers.recent(10), [])
        self.assertIsNone(answers.get(1))
        answers.finish(1, "body")                          # must not raise


if __name__ == "__main__":
    unittest.main()
