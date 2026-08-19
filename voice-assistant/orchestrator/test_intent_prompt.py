"""Prompt-behaviour bench for the classifier. NOT a unit test — it calls the
live LLM, so it is skipped unless VOICE_LLM_TEST=1:

    VOICE_LLM_TEST=1 python3 -m unittest orchestrator.test_intent_prompt

Everything else in this package stubs the classifier out, because a prompt has
no deterministic behaviour to assert offline. But a prompt rule is still code
that can regress, and the multi-speaker rule added 2026-08-17 guards a
destructive failure mode, so it gets a bench that can actually be re-run
against the real model after a prompt edit or a model swap.

Two things are pinned here, both measured against qwen3-next on 2026-08-17:

  1. An interrupted turn resolves to the command that was actually asked for.
     Live failure: "how much time s on my timer what time it s a clear timer"
     (Brad asked; the three-year-old talked over the answer) parsed "unclear"
     3/3 and got "Sorry, I didn't catch that."

  2. A trailing fragment never steers the turn into a destructive intent.
     This is the one that matters. Before the rule, "what time is it clear the
     timer" parsed as timer_cancel 3/3 — a question about a timer would have
     silently cancelled it. It had simply never been said out loud yet.

The controls are not padding. The destructive floor in the rule is one bad
wording away from swallowing legitimate cancels, so the bare-cancel cases below
are the ones to watch when editing it.

Set VOICE_LLM_TRIALS to repeat each case (the 2026-08-17 run was 3/3 stable on
every case here); the default of 1 keeps a re-run to about a minute.
"""

import asyncio
import os
import unittest

from . import intent

TRIALS = int(os.getenv("VOICE_LLM_TRIALS", "1"))

# Each case: transcript, expected intent, expected fields, why it is here.
# "context" marks a follow-up turn, which parses under the stricter note.
CASES = [
    # -- interrupted turns: the reason the rule exists ---------------------
    {"text": "how much time s on my timer what time it s a clear timer",
     "intent": "timer_query",
     "why": "live 2026-08-17: kid talked over it, parsed 'unclear'"},
    {"text": "what time is it clear the timer",
     "intent": "time_query", "fields": {"time_kind": "time"},
     "why": "pre-rule this was timer_cancel 3/3 — destructive misfire"},
    {"text": "set a timer for ten minutes no mommy i want the blue cup",
     "intent": "set_timer", "fields": {"duration_seconds": 600},
     "why": "interjection after a complete command"},

    # -- contaminated turns that already worked: must not regress ---------
    {"text": "Set a timer for 15 minutes. Find a kid's bag.",
     "intent": "set_timer", "fields": {"duration_seconds": 900},
     "why": "live log: parsed correctly before the rule"},
    {"text": "I was distracted. Set a coffee timer for twelve minutes.",
     "intent": "set_timer",
     "fields": {"duration_seconds": 720, "label": "coffee"},
     "why": "live log: parsed correctly before the rule"},
    {"text": "add prunes to my shopping list but why can t why can why can",
     "intent": "add_items",
     "why": "live log: trailing disfluency, parsed correctly before the rule"},

    # -- the destructive floor must not over-suppress ---------------------
    {"text": "clear timer", "intent": "timer_cancel",
     "why": "a bare destructive command is still a command"},
    {"text": "cancel all timers", "intent": "timer_cancel",
     "fields": {"scope": "all"},
     "why": "bare destructive, whole-scope"},
    {"text": "actually never mind cancel all the timers",
     "intent": "timer_cancel", "fields": {"scope": "all"},
     "why": "filler BEFORE a real cancel must not read as a stray fragment"},
    {"text": "stop the music", "intent": "music_control",
     "fields": {"music_action": "stop"},
     "why": "music stop is named in the rule; it must survive"},

    # -- plain controls ----------------------------------------------------
    {"text": "what time is it", "intent": "time_query",
     "fields": {"time_kind": "time"}, "why": "control"},
    {"text": "how much time is left on my timer", "intent": "timer_query",
     "why": "control"},

    # -- chatter still drops on the follow-up path ------------------------
    # Both were logged as follow-up turns and correctly dropped. They run with
    # a context so they exercise the same prompt they did live; on the fresh
    # wake prompt an open question like the second one reads as "ask", which is
    # the follow-up note's job to suppress, not this rule's.
    {"text": "Okay, you go", "intent": "none",
     "context": "You said: Timer set for ten minutes.",
     "why": "live log: room chatter, correctly dropped"},
    {"text": "How were they out? I mean they were just kind of spread out.",
     "intent": "none",
     "context": "You said: It's 72 and sunny.",
     "why": "live log: someone else's conversation, correctly dropped"},
]


@unittest.skipUnless(os.getenv("VOICE_LLM_TEST"),
                     "live classifier bench; set VOICE_LLM_TEST=1 to run")
class MultiSpeakerPromptTest(unittest.TestCase):
    """The classifier's behaviour on transcripts holding more than one voice."""

    def test_cases(self):
        for case in CASES:
            for trial in range(TRIALS):
                with self.subTest(text=case["text"], trial=trial,
                                  why=case["why"]):
                    parsed = asyncio.run(intent.parse(
                        case["text"], context=case.get("context"),
                        sat="kitchen"))
                    self.assertEqual(parsed["intent"], case["intent"],
                                     f'{case["text"]!r} ({case["why"]})')
                    for field, want in case.get("fields", {}).items():
                        self.assertEqual(parsed[field], want,
                                         f'{case["text"]!r} field {field}')


class RuleWiringTest(unittest.TestCase):
    """The offline half: that the rule is actually in the prompt. Cheap, but it
    catches an edit that drops the interpolation and would otherwise only show
    up as a live regression."""

    def test_rule_is_present_exactly_once(self):
        prompt = intent._system("kitchen")
        self.assertEqual(prompt.count("MULTIPLE SPEAKERS"), 1)

    def test_rule_names_the_destructive_intents(self):
        # The floor is only as good as the list it names; a renamed intent must
        # be renamed here too.
        for name in ("timer_cancel", "clear_list", "remove_items"):
            with self.subTest(intent=name):
                self.assertIn(name, intent._MULTI_SPEAKER)
