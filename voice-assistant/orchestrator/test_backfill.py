"""Backfill importer tests.

The two properties that matter: turns are reconstructed from event ORDER
correctly (there is no turn id in the history), and a re-run corrects rather
than duplicates.
"""

import json
import os
import tempfile
import unittest

from . import backfill_events as bf
from . import config, turns

KITCHEN = "kitchen"


def _ev(**kw) -> dict:
    kw.setdefault("ts", "2026-08-01T10:00:00.000-06:00")
    return kw


def _at(second: int) -> str:
    return f"2026-08-01T10:00:{second:02d}.000-06:00"


class BuildTurnsTest(unittest.TestCase):
    def build(self, events):
        return bf.build_turns(iter(events), KITCHEN)

    def test_trigger_verify_command_is_one_turn(self):
        rows = self.build([
            _ev(type="trigger", ts=_at(1), peak_score=0.83, model="okay_google"),
            _ev(type="verify", ts=_at(2), verified=True, score=95.0,
                decode="full", chime_ms=226, rtt_ms=393, server_ms=364,
                transcript="okay computer set a timer", clip="v.wav"),
            _ev(type="command", ts=_at(4), intent="set_timer",
                transcript="set a timer for ten minutes",
                response="Ten minutes, starting now.", clip="c.wav"),
        ])
        self.assertEqual(len(rows), 1)
        r = rows[0]
        self.assertEqual(r["kind"], "wake")
        self.assertEqual(r["stage1_score"], 0.83)
        self.assertEqual(r["wake_model"], "okay_google")
        self.assertEqual(r["verified"], 1)
        self.assertEqual(r["chime_ms"], 226)
        self.assertEqual(r["server_ms"], 364)
        self.assertEqual(r["intent"], "set_timer")
        self.assertEqual(r["response"], "Ten minutes, starting now.")
        self.assertEqual(r["backfilled"], 1)

    def test_a_reject_closes_the_turn(self):
        """4.2% of kitchen triggers survive stage 2 — the rejects are most of
        the data and must not swallow the next turn."""
        rows = self.build([
            _ev(type="trigger", ts=_at(1), peak_score=0.6),
            _ev(type="verify", ts=_at(2), verified=False, score=47.1,
                transcript="come in, simon"),
            _ev(type="trigger", ts=_at(5), peak_score=0.9),
            _ev(type="verify", ts=_at(6), verified=True, score=99.0),
        ])
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["reject_reason"], "low_score")
        self.assertEqual(rows[0]["verified"], 0)
        self.assertEqual(rows[1]["verified"], 1)

    def test_empty_transcript_is_distinguished_from_a_low_score(self):
        rows = self.build([
            _ev(type="trigger", ts=_at(1)),
            _ev(type="verify", ts=_at(2), verified=False, score=0.0,
                transcript=""),
        ])
        self.assertEqual(rows[0]["reject_reason"], "empty")

    def test_suppressed_verify_keeps_the_winner(self):
        rows = self.build([
            _ev(type="trigger", ts=_at(1)),
            _ev(type="verify", ts=_at(2), suppressed=True, winner="kitchen"),
        ])
        self.assertEqual(rows[0]["reject_reason"], "suppressed")
        self.assertEqual(rows[0]["arb_winner"], "kitchen")

    def test_shadow_mode_triggers_have_no_verify(self):
        """Early history is triggers with nothing after them. That is a real
        state, not a parse failure."""
        rows = self.build([
            _ev(type="trigger", ts=_at(1), mode="shadow"),
            _ev(type="trigger", ts=_at(2), mode="shadow"),
            _ev(type="trigger", ts=_at(3), mode="shadow"),
        ])
        self.assertEqual(len(rows), 3)
        # No verify event means no verdict at all — the column stays NULL
        # rather than being recorded as a rejection that never happened.
        self.assertTrue(all(r.get("verified") is None for r in rows))
        self.assertTrue(all(r["kind"] == "wake" for r in rows))

    def test_pre_dual_wake_triggers_default_to_okay_computer(self):
        rows = self.build([_ev(type="trigger", ts=_at(1), peak_score=0.7)])
        self.assertEqual(rows[0]["wake_model"], "okay_computer")

    def test_missing_chime_ms_is_tolerated(self):
        """chime_ms only exists after the 2026-07-12 latency work."""
        rows = self.build([
            _ev(type="trigger", ts=_at(1)),
            _ev(type="verify", ts=_at(2), verified=True, score=99.0),
        ])
        self.assertIsNone(rows[0].get("chime_ms"))

    def test_followup_is_its_own_turn(self):
        rows = self.build([
            _ev(type="trigger", ts=_at(1)),
            _ev(type="verify", ts=_at(2), verified=True, score=99.0),
            _ev(type="command", ts=_at(3), intent="weather", response="Sunny."),
            _ev(type="followup", ts=_at(9), intent="weather",
                transcript="and tomorrow", response="Also sunny."),
        ])
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[1]["kind"], "followup")
        self.assertIsNone(rows[1].get("verified"))

    def test_box_events_are_ignored(self):
        rows = self.build([
            _ev(type="start", ts=_at(1), mode="active"),
            _ev(type="mark", ts=_at(2), clip="m.wav", seconds=20),
            _ev(type="alarm_stop_model", ts=_at(3), score=0.4),
        ])
        self.assertEqual(rows, [])

    def test_orphan_command_becomes_a_manual_turn(self):
        """The button path never fires stage 1."""
        rows = self.build([
            _ev(type="command", ts=_at(1), intent="play_music",
                transcript="play the beatles", response="Playing."),
        ])
        self.assertEqual(rows[0]["kind"], "manual")
        self.assertEqual(rows[0]["intent"], "play_music")

    def test_unparseable_timestamp_is_skipped_not_fatal(self):
        rows = self.build([
            _ev(type="trigger", ts="not-a-date"),
            _ev(type="trigger", ts=_at(5), peak_score=0.9),
        ])
        self.assertEqual(len(rows), 1)

    def test_ids_are_deterministic(self):
        events = [_ev(type="trigger", ts=_at(1), peak_score=0.9)]
        self.assertEqual(self.build(events)[0]["turn_id"],
                         self.build(events)[0]["turn_id"])

    def test_ids_differ_between_satellites(self):
        ts = [_ev(type="trigger", ts=_at(1))]
        a = bf.build_turns(iter(ts), "kitchen")[0]["turn_id"]
        b = bf.build_turns(iter(ts), "master")[0]["turn_id"]
        self.assertNotEqual(a, b)


class WriteTest(unittest.TestCase):
    def setUp(self):
        self._dir = tempfile.TemporaryDirectory()
        self._prev = config.DB_PATH
        config.DB_PATH = os.path.join(self._dir.name, "orchestrator.db")
        turns._db = None

    def tearDown(self):
        config.DB_PATH = self._prev
        turns._db = None
        self._dir.cleanup()

    def _rows(self):
        return bf.build_turns(iter([
            _ev(type="trigger", ts=_at(1), peak_score=0.83),
            _ev(type="verify", ts=_at(2), verified=True, score=95.0,
                chime_ms=226),
            _ev(type="command", ts=_at(4), intent="weather", response="Sunny."),
        ]), KITCHEN)

    def test_rerun_updates_instead_of_duplicating(self):
        bf.write_turns(self._rows())
        bf.write_turns(self._rows())
        rows = turns.recent(limit=100)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["chime_ms"], 226)
        self.assertEqual(rows[0]["backfilled"], 1)

    def test_dry_run_writes_nothing(self):
        n = bf.write_turns(self._rows(), dry_run=True)
        self.assertEqual(n, 1)
        self.assertEqual(turns.recent(), [])

    def test_a_live_row_is_never_overwritten(self):
        rows = self._rows()
        live_id = rows[0]["turn_id"]
        # Force the collision the guard exists for.
        turns.start("kitchen", "wake", intent="live_intent")
        conn = turns._conn()
        conn.execute("UPDATE turns SET turn_id=? WHERE intent='live_intent'",
                     (live_id,))
        conn.commit()
        bf.write_turns(rows)
        stored = turns.recent(limit=100)
        self.assertEqual(len(stored), 1)
        self.assertEqual(stored[0]["intent"], "live_intent")
        self.assertEqual(stored[0]["backfilled"], 0)

    def test_end_to_end_from_a_file(self):
        path = os.path.join(self._dir.name, "events.jsonl")
        with open(path, "w") as fh:
            fh.write(json.dumps(_ev(type="trigger", ts=_at(1),
                                    peak_score=0.9)) + "\n")
            fh.write("{ this is not json\n")           # must not abort
            fh.write(json.dumps(_ev(type="verify", ts=_at(2), verified=True,
                                    score=99.0, chime_ms=210)) + "\n")
        rc = bf.main([f"kitchen={path}", "--db", config.DB_PATH])
        self.assertEqual(rc, 0)
        turns._db = None
        rows = turns.recent(limit=10)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["chime_ms"], 210)


if __name__ == "__main__":
    unittest.main()
