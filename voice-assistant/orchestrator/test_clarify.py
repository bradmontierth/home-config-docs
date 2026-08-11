"""Slot-fill wiring in handle_command, for both slots that use it.

The pieces the unit tests in test_intent_slots.py can't reach: that the slot
question arms a pending clarify, that the answer stitches onto it, that only a
FOLLOW-UP turn is allowed to stitch, and that we never ask twice.

Reproduces two live failures end to end with the classifier stubbed:
  2026-07-26  "set the timer for" -> "unclear" -> (rescued) "Sure, for how
              long?" -> "Eight minutes." -> an 8 minute timer.
  2026-07-27  "remind me to" -> (rescued) "Remind you to do what?" -> "call the
              dentist at five" -> a reminder built from the STITCHED command,
              on the list of whoever started the sentence.
"""

import asyncio
import os
import shutil
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

from . import app, clients, config, intent, timers


def _parsed(**over):
    """A validated parse result with the given fields overridden."""
    return intent.validate({"intent": "none", **over})


class _SlotTestBase(unittest.TestCase):
    """Isolation shared by both slots: a private timer DB, a clean session, and
    no reach outside the process."""

    def setUp(self):
        # TimerEngine is sqlite-backed, so give each test its own database —
        # otherwise tests share timers and, run in the live container, would
        # write into the real one.
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, True)
        for attr, path in (("DB_PATH", os.path.join(tmp, "timers.db")),
                           ("ANNOUNCE_CACHE_DIR", os.path.join(tmp, "audio"))):
            p = patch.object(config, attr, path)
            p.start()
            self.addCleanup(p.stop)
        app.ENGINE = timers.TimerEngine(on_expire=AsyncMock())
        app._SESSION.update({"ts": 0.0, "summary": "", "last_added": [],
                             "pending": None})
        # Silence the outside world: no dashboard events, no spoken reply, and
        # no TTS at all — TimerEngine.create pre-renders each timer's
        # announcement, which otherwise makes these tests hit the live server.
        for target, attr, ret in ((app, "_speak_reply", None),
                                  (app.events, "emit", None),
                                  (clients, "synthesize", b"")):
            p = patch.object(target, attr, AsyncMock(return_value=ret))
            p.start()
            self.addCleanup(p.stop)

    def _run(self, command, followup=False):
        return asyncio.run(app.handle_command(command, followup=followup))


class DeterministicDispatchTest(_SlotTestBase):
    """The pre-parser is wired ahead of the LLM on fresh wake turns only."""

    def test_plain_timer_bypasses_classifier(self):
        with patch.object(app.intent_mod, "parse", new=AsyncMock()) as parse:
            result = self._run("set a timer for eight minutes")
        parse.assert_not_awaited()
        self.assertEqual(result["intent"], "set_timer")
        self.assertEqual(result["timer"]["duration_seconds"], 480)

    def test_absolute_volume_bypasses_classifier(self):
        with patch.object(app.intent_mod, "parse", new=AsyncMock()) as parse, \
             patch.object(app.music_mod, "control",
                          new=AsyncMock(return_value=80)) as control:
            result = self._run("volume eighty")
        parse.assert_not_awaited()
        control.assert_awaited_once()
        self.assertEqual(result["response"], "Okay, volume 80.")

    def test_weather_bypasses_classifier(self):
        answer = {"response": "It's 72 and sunny.", "ok": True,
                  "weather_when": "now"}
        with patch.object(app.intent_mod, "parse", new=AsyncMock()) as parse, \
             patch.object(app.weather_mod, "handle",
                          new=AsyncMock(return_value=answer)) as weather:
            result = self._run("what's the weather")
        parse.assert_not_awaited()
        weather.assert_awaited_once()
        self.assertEqual(result["response"], answer["response"])

    def test_named_weather_bypasses_classifier_with_location(self):
        answer = {"response": "In Park City, Utah, it'll be 68.", "ok": True,
                  "weather_when": "today", "weather_location": "Park City, Utah"}
        command = "what's the weather in Park City today"
        with patch.object(app.intent_mod, "parse", new=AsyncMock()) as parse, \
             patch.object(app.weather_mod, "handle",
                          new=AsyncMock(return_value=answer)) as weather:
            result = self._run(command)
        parse.assert_not_awaited()
        parsed, raw_command = weather.await_args.args
        self.assertEqual(parsed["weather_location"], "park city")
        self.assertEqual(parsed["weather_when"], "today")
        self.assertEqual(raw_command, command)
        self.assertEqual(result["response"], answer["response"])

    def test_followup_keeps_the_contextual_classifier(self):
        parsed = _parsed(intent="weather", weather_when="now")
        with patch.object(app.intent_mod, "parse",
                          new=AsyncMock(return_value=parsed)) as parse, \
             patch.object(app.weather_mod, "handle", new=AsyncMock(
                 return_value={"response": "It's 72.", "ok": True,
                               "weather_when": "now"})):
            self._run("what's the weather", followup=True)
        parse.assert_awaited_once()

    def test_timer_rename_bypasses_classifier_even_on_followup(self):
        created = self._run("set a timer for five minutes")
        with patch.object(app.intent_mod, "parse", new=AsyncMock()) as parse:
            renamed = self._run("Rename the timer to Pasta Timer.", followup=True)
        parse.assert_not_awaited()
        self.assertEqual(renamed["intent"], "timer_rename")
        self.assertEqual(renamed["timer"]["id"], created["timer"]["id"])
        self.assertEqual(renamed["timer"]["label"], "pasta")
        self.assertEqual(renamed["response"], "Okay, it's now the pasta timer.")


class ClarifyFlowTest(_SlotTestBase):

    # -- the live failure, end to end ------------------------------------
    def test_truncated_timer_asks_then_completes(self):
        # Turn 1: the classifier calls it "unclear" (as it did live); the
        # deterministic rescue turns it into a timer missing its duration.
        with patch.object(app.intent_mod, "parse",
                          AsyncMock(return_value=_parsed(intent="unclear"))):
            first = self._run("set the timer for")
        self.assertEqual(first["intent"], "set_timer")
        self.assertEqual(first["response"], "Sure, for how long?")
        self.assertTrue(first["awaiting_slot"])
        self.assertEqual(app.session_pending()["op"], "clarify")
        self.assertEqual(app.ENGINE.active(), [])

        # Turn 2: the answer alone is a fragment. It must NOT reach the plain
        # parser (that is what dropped it live) — the duration fast path reads
        # it without an LLM call at all.
        with patch.object(app.intent_mod, "parse", AsyncMock()) as plain, \
             patch.object(app.intent_mod, "parse_clarify", AsyncMock()) as slow:
            second = self._run("Eight minutes.", followup=True)
        plain.assert_not_called()
        slow.assert_not_called()
        self.assertEqual(second["intent"], "set_timer")
        self.assertTrue(second["ok"])
        self.assertEqual(second["timer"]["duration_seconds"], 480)
        self.assertIsNone(app.session_pending())   # slot consumed

    def test_label_and_theme_survive_the_fast_path(self):
        # The first parse worked out "chicken"/cluck; answering with a bare
        # duration must not lose them.
        with patch.object(app.intent_mod, "parse", AsyncMock(
                return_value=_parsed(intent="set_timer", label="chicken",
                                     sound_theme="cluck"))):
            first = self._run("set a chicken timer for")
        self.assertEqual(first["response"], "Sure, how long for the chicken?")

        second = self._run("twelve minutes", followup=True)
        self.assertEqual(second["timer"]["duration_seconds"], 720)
        self.assertEqual(second["timer"]["label"], "chicken")
        self.assertEqual(second["timer"]["sound_theme"], "cluck")

    def test_truncated_rename_asks_then_uses_bare_name(self):
        created = self._run("set a timer for five minutes")
        first = self._run("rename the timer to")
        self.assertEqual(first["intent"], "timer_rename")
        self.assertEqual(first["response"], "Sure, what should I call it?")
        self.assertTrue(first["awaiting_slot"])
        self.assertEqual(app.session_pending()["kind"], "timer_rename")

        with patch.object(app.intent_mod, "parse", AsyncMock()) as plain, \
             patch.object(app.intent_mod, "parse_clarify", AsyncMock()) as slow:
            second = self._run("Pasta Timer.", followup=True)
        plain.assert_not_called()
        slow.assert_not_called()
        self.assertEqual(second["timer"]["id"], created["timer"]["id"])
        self.assertEqual(second["timer"]["label"], "pasta")
        self.assertIsNone(app.session_pending())

    def test_rename_with_no_timer_fails_cleanly(self):
        result = self._run("call the timer pasta")
        self.assertFalse(result["ok"])
        self.assertEqual(result["response"], "I couldn't find that timer.")

    def test_unrecognised_answer_goes_to_the_stitching_parser(self):
        # "until the pasta is done" isn't a duration the fast path can read, so
        # it goes to the LLM with the partial command stitched back on.
        with patch.object(app.intent_mod, "parse", AsyncMock(
                return_value=_parsed(intent="set_timer"))):
            self._run("set a timer for")

        with patch.object(app.intent_mod, "parse_clarify", AsyncMock(
                return_value=_parsed(intent="set_timer",
                                     duration_seconds=600))) as slow:
            result = self._run("however long the pasta takes", followup=True)
        slow.assert_awaited_once()
        partial, reply, question, sat = slow.await_args.args
        self.assertEqual(partial, "set a timer for")
        self.assertEqual(reply, "however long the pasta takes")
        self.assertEqual(question, "Sure, for how long?")
        self.assertIsNone(sat)
        self.assertEqual(result["timer"]["duration_seconds"], 600)

    def test_answer_can_abandon_the_timer(self):
        # She changed her mind. The reply is parsed on its own merits and the
        # timer request is dropped — no timer, no second question.
        with patch.object(app.intent_mod, "parse", AsyncMock(
                return_value=_parsed(intent="set_timer"))):
            self._run("set a timer for")

        with patch.object(app.intent_mod, "parse_clarify", AsyncMock(
                return_value=_parsed(intent="music_control",
                                     music_action="pause"))), \
             patch.object(app.music_mod, "control",
                          AsyncMock(return_value={"response": "Paused.", "ok": True})):
            result = self._run("actually pause the music", followup=True)
        self.assertEqual(result["intent"], "music_control")
        self.assertEqual(app.ENGINE.active(), [])
        self.assertIsNone(app.session_pending())

    def test_never_asks_twice(self):
        # A second "for how long?" would be a loop. One round, then let go.
        with patch.object(app.intent_mod, "parse", AsyncMock(
                return_value=_parsed(intent="set_timer"))):
            self._run("set a timer for")

        with patch.object(app.intent_mod, "parse_clarify", AsyncMock(
                return_value=_parsed(intent="set_timer"))):
            result = self._run("hmm, hang on", followup=True)
        self.assertEqual(result["response"], "Okay, never mind.")
        self.assertFalse(result["ok"])
        self.assertIsNone(app.session_pending())
        self.assertEqual(app.ENGINE.active(), [])

    def test_wake_word_starts_over_instead_of_stitching(self):
        # She gave up on the question and re-asked with the wake word. Stitching
        # her new command onto the abandoned partial would produce nonsense, so
        # a non-follow-up turn drops the slot and routes as a fresh command.
        with patch.object(app.intent_mod, "parse", AsyncMock(
                return_value=_parsed(intent="set_timer"))):
            self._run("set a timer for")

        with patch.object(app.intent_mod, "parse", AsyncMock(
                return_value=_parsed(intent="weather", weather_when="now"))) as plain, \
             patch.object(app.intent_mod, "parse_clarify", AsyncMock()) as slow, \
             patch.object(app.weather_mod, "handle", AsyncMock(
                 return_value={"response": "It's 72 degrees.", "ok": True})):
            result = self._run("what's the weather")
        # Local weather is deterministic, so the fresh command bypasses both
        # the ordinary classifier and the clarification classifier.
        plain.assert_not_awaited()
        slow.assert_not_called()
        self.assertEqual(result["intent"], "weather")
        self.assertIsNone(app.session_pending())

    def test_complete_timer_never_arms_a_slot(self):
        with patch.object(app.intent_mod, "parse", AsyncMock(
                return_value=_parsed(intent="set_timer", duration_seconds=480))):
            result = self._run("set a timer for eight minutes")
        self.assertTrue(result["ok"])
        self.assertNotIn("awaiting_slot", result)
        self.assertIsNone(app.session_pending())

    def test_slot_expires_with_the_session(self):
        with patch.object(app.intent_mod, "parse", AsyncMock(
                return_value=_parsed(intent="set_timer"))):
            self._run("set a timer for")
        app._SESSION["ts"] -= app.SESSION_TTL_S + 1      # walk off the edge
        self.assertIsNone(app.session_pending())

        with patch.object(app.intent_mod, "parse", AsyncMock(
                return_value=_parsed(intent="none"))) as plain, \
             patch.object(app.intent_mod, "parse_clarify", AsyncMock()) as slow:
            self._run("eight minutes", followup=True)
        plain.assert_awaited_once()
        slow.assert_not_called()


class AddSlotFlowTest(_SlotTestBase):
    """"remind me to…" / "add to my to-do list…" cut off before the content.

    Two things make this more than a copy of the timer slot: the companion
    types items from the framing words, so it must be handed the STITCHED
    command rather than the bare reply; and speaker ID has to survive the hop,
    because the one-phrase answer is much weaker audio than the full utterance
    and an unsure read would file her reminder under Brad.
    """

    def setUp(self):
        super().setUp()
        self.added = [{"id": 7, "type": "reminder", "text": "call the dentist",
                       "due_at": None, "user": "adrienne"}]
        self.add = AsyncMock(return_value=self.added)
        for target, attr, mock in (
                (app.lists_mod, "add_from_text", self.add),
                # The add path pops/refreshes the kiosk list view afterwards.
                (app.lists_mod, "fetch", AsyncMock(return_value=[]))):
            p = patch.object(target, attr, mock)
            p.start()
            self.addCleanup(p.stop)

    def _voice(self, name):
        """Patch this turn's speaker identification to `name` (None = unsure)."""
        return patch.object(app, "_speaker_name", AsyncMock(return_value=name))

    # -- the live failure, end to end ------------------------------------
    def test_truncated_reminder_asks_then_stitches(self):
        # Turn 1: the classifier reads the bare fragment as "unclear" (exactly
        # what it did to the truncated timer); the rescue makes it a reminder
        # with nothing to remind about, which is the question branch.
        with patch.object(app.intent_mod, "parse",
                          AsyncMock(return_value=_parsed(intent="unclear"))), \
             self._voice("adrienne"):
            first = self._run("remind me to")
        self.assertEqual(first["intent"], "set_reminder")
        self.assertEqual(first["response"], "Remind you to do what?")
        self.assertTrue(first["awaiting_slot"])
        self.assertFalse(first["ok"])
        self.add.assert_not_awaited()          # nothing filed on a question
        pending = app.session_pending()
        self.assertEqual(pending["op"], "clarify")
        self.assertEqual(pending["kind"], "add")
        self.assertEqual(pending["owner"], "adrienne")

        # Turn 2: the companion must see the whole command. Handing it just
        # "call the dentist at five" would lose the "remind me" framing it
        # types the item from, and the reminder would come back a to-do.
        with patch.object(app.intent_mod, "parse_clarify", AsyncMock(
                return_value=_parsed(intent="set_reminder"))) as slow, \
             self._voice(None):
            second = self._run("call the dentist at five", followup=True)
        slow.assert_awaited_once()
        self.assertEqual(self.add.await_args.args[0],
                         "remind me to call the dentist at five")
        self.assertTrue(second["ok"])
        self.assertIsNone(app.session_pending())   # slot consumed

    def test_owner_survives_the_hop(self):
        # The reply is a second or two of audio and often scores "unsure". The
        # speaker identified on the FULL first utterance is the one that counts
        # — otherwise her reminder silently lands on Brad's phone, which is the
        # exact misroute speaker ID exists to prevent.
        with patch.object(app.intent_mod, "parse", AsyncMock(
                return_value=_parsed(intent="set_reminder", missing_content=True))), \
             self._voice("adrienne"):
            self._run("remind me to")

        with patch.object(app.intent_mod, "parse_clarify", AsyncMock(
                return_value=_parsed(intent="set_reminder"))), \
             self._voice(None):                     # unsure on the short reply
            self._run("pick simon up at four", followup=True)
        self.assertEqual(self.add.await_args.kwargs["owner"], "adrienne")

    def test_unsure_first_turn_falls_back_to_the_reply(self):
        # Nothing to carry over -> use whatever the reply scores as, and if
        # that is unsure too, owner stays None (LIST_OWNER, today's behavior).
        with patch.object(app.intent_mod, "parse", AsyncMock(
                return_value=_parsed(intent="set_reminder", missing_content=True))), \
             self._voice(None):
            self._run("remind me to")

        with patch.object(app.intent_mod, "parse_clarify", AsyncMock(
                return_value=_parsed(intent="set_reminder"))), \
             self._voice("brad"):
            self._run("take the trash out tonight", followup=True)
        self.assertEqual(self.add.await_args.kwargs["owner"], "brad")

    def test_bare_duration_answer_does_not_become_a_timer(self):
        # The duration fast path reads "five minutes" perfectly well — which is
        # why it must not run on this slot. Answering "remind you to do what?"
        # that way has to reach the parser, not the timer engine.
        with patch.object(app.intent_mod, "parse", AsyncMock(
                return_value=_parsed(intent="set_reminder", missing_content=True))), \
             self._voice("brad"):
            self._run("remind me to")

        with patch.object(app.intent_mod, "parse_clarify", AsyncMock(
                return_value=_parsed(intent="set_reminder"))) as slow, \
             self._voice("brad"):
            self._run("five minutes", followup=True)
        slow.assert_awaited_once()
        self.assertEqual(app.ENGINE.active(), [])

    def test_timer_slot_never_stitches_into_a_list_add(self):
        # She abandoned the timer for a list command. The reply is parsed on
        # its own merits, so the companion must get the reply ALONE — stitching
        # "set a timer for actually add milk…" would be nonsense.
        with patch.object(app.intent_mod, "parse", AsyncMock(
                return_value=_parsed(intent="set_timer"))):
            self._run("set a timer for")

        with patch.object(app.intent_mod, "parse_clarify", AsyncMock(
                return_value=_parsed(intent="add_items"))), \
             self._voice("brad"):
            self._run("actually add milk to the shopping list", followup=True)
        self.assertEqual(self.add.await_args.args[0],
                         "actually add milk to the shopping list")
        self.assertEqual(app.ENGINE.active(), [])

    def test_never_asks_twice(self):
        with patch.object(app.intent_mod, "parse", AsyncMock(
                return_value=_parsed(intent="set_reminder", missing_content=True))), \
             self._voice("brad"):
            self._run("remind me to")

        with patch.object(app.intent_mod, "parse_clarify", AsyncMock(
                return_value=_parsed(intent="set_reminder",
                                     missing_content=True))), \
             self._voice("brad"):
            result = self._run("uh, hang on", followup=True)
        self.assertEqual(result["response"], "Okay, never mind.")
        self.assertFalse(result["ok"])
        self.add.assert_not_awaited()
        self.assertIsNone(app.session_pending())

    def test_complete_add_never_arms_a_slot(self):
        with patch.object(app.intent_mod, "parse", AsyncMock(
                return_value=_parsed(intent="add_items"))), \
             self._voice("brad"):
            result = self._run("add eggs to the shopping list")
        self.assertTrue(result["ok"])
        self.assertNotIn("awaiting_slot", result)
        self.assertIsNone(app.session_pending())
        self.assertEqual(self.add.await_args.args[0],
                         "add eggs to the shopping list")

    def test_private_reminder_is_flagged_and_said_aloud(self):
        # "privately" reaches add_from_text through the STITCHED text, and the
        # spoken reply has to say the quiet path took — that acknowledgement is
        # the only feedback they get that it won't hit the kitchen screen.
        with patch.object(app.intent_mod, "parse", AsyncMock(
                return_value=_parsed(intent="set_reminder", missing_content=True))), \
             self._voice("brad"):
            self._run("remind me privately to")

        with patch.object(app.intent_mod, "parse_clarify", AsyncMock(
                return_value=_parsed(intent="set_reminder"))), \
             self._voice("brad"):
            result = self._run("order adrienne's birthday gift", followup=True)
        self.assertTrue(self.add.await_args.kwargs["private"])
        self.assertIn("Just on your phone.", result["response"])
        self.assertNotIn("On Brad's list.", result["response"])

    def test_ordinary_add_is_not_private(self):
        with patch.object(app.intent_mod, "parse", AsyncMock(
                return_value=_parsed(intent="set_reminder"))), \
             self._voice("adrienne"):
            result = self._run("remind me to call mom at noon")
        self.assertFalse(self.add.await_args.kwargs["private"])
        self.assertIn("On Adrienne's list.", result["response"])


class ShowListScopeTest(_SlotTestBase):
    """"show my to-dos" narrows to the speaker; everything else stays the
    household view."""

    def setUp(self):
        super().setUp()
        self.fetch = AsyncMock(return_value=[])
        p = patch.object(app.lists_mod, "fetch", self.fetch)
        p.start()
        self.addCleanup(p.stop)

    def _show(self, command, voice, intent_name="show_todos"):
        with patch.object(app.intent_mod, "parse", AsyncMock(
                return_value=_parsed(intent=intent_name))), \
             patch.object(app, "_speaker_name", AsyncMock(return_value=voice)):
            return self._run(command)

    def test_my_todos_scope_to_the_speaker(self):
        result = self._show("show my to-dos", "adrienne")
        self.assertEqual(self.fetch.await_args.kwargs["user"], "adrienne")
        self.assertEqual(result["owner"], "adrienne")

    def test_household_phrasing_stays_shared(self):
        for command in ("show the to-do list", "what's on our to-do list",
                        "show me the to-dos"):
            with self.subTest(command=command):
                self.fetch.reset_mock()
                result = self._show(command, "adrienne")
                self.assertIsNone(self.fetch.await_args.kwargs["user"])
                self.assertIsNone(result["owner"])

    def test_unsure_voice_falls_back_to_the_household_list(self):
        # Never guess whose list to show: below-confidence keeps today's
        # behavior, exactly like the add path.
        result = self._show("show my to-dos", None)
        self.assertIsNone(self.fetch.await_args.kwargs["user"])
        self.assertIsNone(result["owner"])

    def test_my_reminders_scope_to_the_speaker(self):
        # The most personal list of the three, and the one Brad actually wants
        # to pull up ("that reminder tomorrow is stale, let me cancel it").
        result = self._show("show me my reminders", "brad",
                            intent_name="show_reminders")
        self.assertEqual(self.fetch.await_args.kwargs["user"], "brad")
        self.assertEqual(self.fetch.await_args.kwargs["types"], ("reminder",))
        self.assertEqual(result["owner"], "brad")

    def test_household_reminders_stay_shared(self):
        result = self._show("what reminders do we have", "brad",
                            intent_name="show_reminders")
        self.assertIsNone(self.fetch.await_args.kwargs["user"])
        self.assertIsNone(result["owner"])

    def test_shopping_is_never_narrowed(self):
        # One house, one shopping trip — "my shopping list" is still the
        # household's, however it is phrased.
        result = self._show("show my shopping list", "brad",
                            intent_name="show_shopping")
        self.assertIsNone(self.fetch.await_args.kwargs["user"])
        self.assertIsNone(result["owner"])


if __name__ == "__main__":
    unittest.main()
