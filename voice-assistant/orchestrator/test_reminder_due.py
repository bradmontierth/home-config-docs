"""The display policy for a reminder that just came due.

The whole feature turns on one decision — does this reminder belong on the
kitchen screen — and the answer is PROVENANCE, never content. A reminder
created by voice was already spoken aloud in that room, so echoing it there
tells the room nothing new; one typed quietly in the phone app was never
uttered in shared space, and "remind me privately to…" opted out explicitly.

These tests pin that rule, because the failure it prevents (a personal
reminder appearing on the kitchen display in front of guests) is invisible in
testing and only ever happens in front of an audience.
"""

import asyncio
import unittest
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException

from . import app, config


def _fire(**over):
    """Post one due reminder to the endpoint; returns (result, emit, chime)."""
    payload = {"item_id": 7, "user": "adrienne", "text": "take the roast out",
               "due_at": "2026-07-27T17:00:00-06:00", "source": "assistant"}
    payload.update(over)
    with patch.object(app.events, "emit", AsyncMock()) as emit, \
         patch.object(app.events, "satellite_chime", AsyncMock()) as chime:
        result = asyncio.run(app.reminder_due(payload))
    return result, emit, chime


class ReminderDisplayPolicyTest(unittest.TestCase):

    def test_voice_created_reminder_reaches_the_screen(self):
        result, emit, chime = _fire(source="assistant")
        self.assertTrue(result["shown"])
        emit.assert_awaited_once()
        event, fields = emit.await_args.args[0], emit.await_args.kwargs
        self.assertEqual(event, "reminder_due")
        self.assertEqual(fields["text"], "take the roast out")
        self.assertEqual(fields["owner"], "adrienne")
        self.assertEqual(fields["item_id"], 7)
        # A chime, never speech: the text is on screen, and audio is what
        # actually carries a personal reminder to the rest of the house.
        chime.assert_awaited_once_with(config.REMINDER_CHIME_PATH)

    def test_phone_typed_reminder_stays_on_the_phone(self):
        # The default mode the phone app writes. Never uttered in shared space
        # -> the kitchen display is not the place to promote it.
        result, emit, chime = _fire(source="new_note_per_recording")
        self.assertFalse(result["shown"])
        self.assertEqual(result["reason"], "source")
        emit.assert_not_awaited()
        chime.assert_not_awaited()

    def test_private_reminder_is_never_displayed(self):
        # "remind me privately to…" -> lists.add_from_text filed the note under
        # assistant_private, which is deliberately absent from the allow-list.
        result, emit, _ = _fire(source="assistant_private")
        self.assertFalse(result["shown"])
        emit.assert_not_awaited()

    def test_unknown_provenance_is_not_displayed(self):
        # The item's note is gone, or something else created it. Defaulting to
        # "show" would make every future writer of items opt OUT of the kitchen
        # screen; defaulting to silence makes them opt in.
        for source in ("unknown", "", "assistant "):
            with self.subTest(source=source):
                result, emit, _ = _fire(source=source)
                self.assertFalse(result["shown"])
                emit.assert_not_awaited()

    def test_missing_source_field_is_not_displayed(self):
        payload_result, emit, _ = _fire(source=None)
        self.assertFalse(payload_result["shown"])
        emit.assert_not_awaited()

    def test_unattributed_reminder_still_shows_without_a_name(self):
        result, emit, _ = _fire(user=None)
        self.assertTrue(result["shown"])
        self.assertIsNone(emit.await_args.kwargs["owner"])

    def test_empty_text_is_rejected(self):
        # Nothing to render, and a blank card on the kitchen wall is worse
        # than none — this is the one case that is an actual error.
        with self.assertRaises(HTTPException) as raised:
            _fire(text="   ")
        self.assertEqual(raised.exception.status_code, 400)

    def test_declined_display_is_not_an_error(self):
        # This is the companion's fire-and-forget tail: a non-displayed
        # reminder is a normal outcome, and answering it with a failure would
        # put a warning in the companion's log on every phone-typed reminder.
        result, _, _ = _fire(source="new_note_per_recording")
        self.assertTrue(result["ok"])


if __name__ == "__main__":
    unittest.main()
