"""add_from_text must hand back the row the companion stored for THIS turn.

2026-09-04 06:22, kitchen: "remind me to roast coffee this afternoon" was
answered "on Sunday at 3:00 PM". The companion parsed it right (today 15:00);
the orchestrator matched the analyzed item back to the active list by (type,
text) alone and got the Aug 30 "roast coffee" reminder — fired, never checked
off, sorted first — so the stale due date was spoken."""

import unittest

from . import lists

OLD = {"id": 291, "client_note_id": "old-note", "type": "reminder",
       "text": "roast coffee", "due_at": "2026-08-30T15:00:00-06:00",
       "created_at": "2026-08-30T12:44:09+00:00"}
NEW = {"id": 293, "client_note_id": "new-note", "type": "reminder",
       "text": "roast coffee", "due_at": "2026-09-04T15:00:00-06:00",
       "created_at": "2026-09-04T12:22:15+00:00"}
ITEM = {"type": "reminder", "text": "roast coffee",
        "due_at": "2026-09-04T15:00:00-06:00", "confidence": 0.9}


class MatchStoredTests(unittest.TestCase):
    def test_same_note_wins_over_older_same_text_row(self):
        self.assertIs(lists._match_stored(ITEM, [OLD, NEW], "new-note"), NEW)

    def test_same_due_date_wins_when_note_id_is_missing(self):
        # A deduped item has no row under our note; the one with the matching
        # due date is still the right one.
        self.assertIs(lists._match_stored(ITEM, [OLD, NEW], "other-note"), NEW)

    def test_newest_same_text_row_is_the_last_resort(self):
        item = dict(ITEM, due_at="2026-09-05T09:00:00-06:00")
        self.assertIs(lists._match_stored(item, [OLD, NEW], "other-note"), NEW)

    def test_no_row_returns_the_analyzed_item(self):
        self.assertIs(lists._match_stored(ITEM, [], "new-note"), ITEM)

    def test_text_match_is_case_insensitive_and_typed(self):
        todo = dict(NEW, type="todo", text="Roast Coffee")
        self.assertIs(lists._match_stored(ITEM, [todo], "new-note"), ITEM)
        self.assertIs(lists._match_stored(dict(ITEM, text="Roast Coffee"),
                                          [NEW], "new-note"), NEW)


if __name__ == "__main__":
    unittest.main()
