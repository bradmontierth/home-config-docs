"""The `ask` backend chain (2026-11-20): Claude knowledge service first,
OpenRouter only when Claude fails before producing any text.

Live trigger: OpenRouter ran out of credits (402) and every general question
answered "Sorry, I couldn't reach the knowledge service." The Claude backend
needs a STATIC system prompt (the host service keeps a process pre-warmed with
it), so the date, room and recent Q&A travel in the question instead.
Nothing here touches a network beyond a loopback test server.
"""

import json
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from unittest.mock import AsyncMock, patch

from . import ask, claude_knowledge, config


async def _chunks(*parts, fail_with=None):
    for p in parts:
        yield p
    if fail_with:
        raise fail_with


class PromptSplitTest(unittest.TestCase):
    def test_static_prompt_has_nothing_per_request(self):
        a = ask._system_static()
        with patch.object(ask.zones, "spoken_for", return_value="the master bedroom"):
            b = ask._system_static()
        self.assertEqual(a, b)
        self.assertNotIn("Right now it is", a)
        self.assertNotIn("originated in", a)
        self.assertIn(ask.SENTINEL, a)
        self.assertIn("<context>", a)
        self.assertIn("names a source", a)
        self.assertIn("never mention them", a)

    def test_openrouter_prompt_still_carries_room_and_date(self):
        s = ask._system("kitchen")
        self.assertIn("Right now it is", s)
        self.assertIn("The request originated in", s)
        self.assertIn(ask._search_and_format_rules(), s)

    def test_question_carries_room_date_and_recent_turns(self):
        hist = [{"role": "user", "content": "did the kids nap today"},
                {"role": "assistant", "content": "Yes, Claire napped twice."}]
        with patch.object(ask, "_history_messages", return_value=hist), \
             patch.object(ask.zones, "spoken_for", return_value="the kitchen"):
            p = ask._claude_prompt("what about yesterday", "kitchen")
        self.assertIn("Room: the kitchen", p)
        self.assertIn("Local date and time:", p)
        self.assertIn("<earlier>\nQ: did the kids nap today\nA: Yes, Claire napped twice.\n</earlier>", p)
        self.assertTrue(p.endswith("Question: what about yesterday"))

    def test_no_earlier_block_without_history(self):
        with patch.object(ask, "_history_messages", return_value=[]):
            self.assertNotIn("<earlier>", ask._claude_prompt("how tall is everest", "kitchen"))


class BackendChainTest(unittest.IsolatedAsyncioTestCase):
    async def _collect(self, **patches):
        stats: dict = {}
        out = []
        async for d in ask._answer_stream("q", "kitchen", stats):
            out.append(d)
        return "".join(out)

    async def test_claude_answers_and_openrouter_is_not_called(self):
        orouter = AsyncMock()
        with patch.object(config, "ASK_BACKEND", "claude"), \
             patch.object(ask.claude_knowledge, "stream_chat",
                          return_value=_chunks("Two ", "tablespoons.")) as claude, \
             patch.object(ask.openrouter, "stream_chat", new=orouter):
            text = await self._collect()
        self.assertEqual(text, "Two tablespoons.")
        orouter.assert_not_called()
        system, prompt = claude.call_args.args
        self.assertEqual(system, ask._system_static())
        self.assertIn("Question: q", prompt)

    async def test_claude_down_before_text_falls_back_to_openrouter(self):
        with patch.object(config, "ASK_BACKEND", "claude"), \
             patch.object(ask.claude_knowledge, "stream_chat",
                          return_value=_chunks(fail_with=RuntimeError("service down"))), \
             patch.object(ask.openrouter, "stream_chat",
                          return_value=_chunks("From ", "GPT.")) as orouter:
            text = await self._collect()
        self.assertEqual(text, "From GPT.")
        messages = orouter.call_args.args[0]
        self.assertEqual(messages[0]["role"], "system")
        self.assertEqual(messages[-1], {"role": "user", "content": "q"})

    async def test_failure_after_text_does_not_start_a_second_answer(self):
        orouter = AsyncMock()
        with patch.object(config, "ASK_BACKEND", "claude"), \
             patch.object(ask.claude_knowledge, "stream_chat",
                          return_value=_chunks("Half an ans", fail_with=RuntimeError("died"))), \
             patch.object(ask.openrouter, "stream_chat", new=orouter):
            with self.assertRaises(RuntimeError):
                await self._collect()
        orouter.assert_not_called()

    async def test_openrouter_backend_never_touches_claude(self):
        claude = AsyncMock()
        with patch.object(config, "ASK_BACKEND", "openrouter"), \
             patch.object(ask.claude_knowledge, "stream_chat", new=claude), \
             patch.object(ask.openrouter, "stream_chat", return_value=_chunks("ok")):
            self.assertEqual(await self._collect(), "ok")
        claude.assert_not_called()

    async def test_handle_ask_end_to_end_on_claude(self):
        with patch.object(config, "ASK_BACKEND", "claude"), \
             patch.object(ask.config, "ASK_FILLER", False), \
             patch.object(ask.zones, "route_for", return_value=None), \
             patch.object(ask.zones, "speak_url_for", return_value="http://kitchen/speak"), \
             patch.object(ask.claude_knowledge, "stream_chat",
                          return_value=_chunks("About two tablespoons.\n", ask.SENTINEL,
                                               "\nA medium lime gives 1 to 3.")), \
             patch.object(ask.events, "emit", new=AsyncMock()), \
             patch.object(ask.answers_mod, "record", return_value=None), \
             patch.object(ask.answers_mod, "finish"):
            result = await ask.handle_ask("how much juice is in a lime", "kitchen")
        self.assertTrue(result["ok"])
        self.assertEqual(result["response"], "About two tablespoons.")

    async def test_both_backends_down_is_the_spoken_apology(self):
        with patch.object(config, "ASK_BACKEND", "claude"), \
             patch.object(ask.config, "ASK_FILLER", False), \
             patch.object(ask.zones, "route_for", return_value=None), \
             patch.object(ask.zones, "speak_url_for", return_value=None), \
             patch.object(ask.claude_knowledge, "stream_chat",
                          return_value=_chunks(fail_with=RuntimeError("down"))), \
             patch.object(ask.openrouter, "stream_chat",
                          return_value=_chunks(fail_with=RuntimeError("OpenRouter 402"))), \
             patch.object(ask.events, "emit", new=AsyncMock()):
            result = await ask.handle_ask("how tall is everest", "kitchen")
        self.assertFalse(result["ok"])
        self.assertIn("couldn't reach the knowledge service", result["response"])


class FollowupVerdictTest(unittest.IsolatedAsyncioTestCase):
    """handle_ask(followup=True): Claude rules on an open-mic follow-up before
    anything is shown, spoken, remembered or filed (2026-11-20)."""

    def setUp(self):
        self.emit = AsyncMock()
        self.record = patch.object(ask.answers_mod, "record", return_value=None)
        for p in (patch.object(config, "ASK_BACKEND", "claude"),
                  patch.object(ask.config, "ASK_FILLER", False),
                  patch.object(ask.events, "emit", new=self.emit),
                  patch.object(ask.answers_mod, "finish")):
            p.start()
            self.addCleanup(p.stop)
        self.record_mock = self.record.start()
        self.addCleanup(self.record.stop)

    def test_verdict_function(self):
        v = ask._escape_verdict
        self.assertIsNone(v(""))
        self.assertIsNone(v("  \n"))
        self.assertIsNone(v("=="))                      # could still be a token
        self.assertIsNone(v("===NOT_FOR"))
        self.assertEqual(v("===NOT_FOR_ME==="), "none")
        self.assertEqual(v("\n===NOT_FOR_ME===\n"), "none")
        self.assertEqual(v("===COMMAND==="), "command")
        self.assertEqual(v("Skin on is fine."), "answer")
        self.assertEqual(v("===MORE==="), "answer")     # the answer separator is not an escape
        self.assertEqual(v("=x"), "answer")

    async def _ask(self, *chunks, fail_with=None, followup=True):
        accepted = AsyncMock()
        with patch.object(ask.claude_knowledge, "stream_chat",
                          return_value=_chunks(*chunks, fail_with=fail_with)) as claude, \
             patch.object(ask.openrouter, "stream_chat", new=AsyncMock()) as orouter:
            r = await ask.handle_ask("come on kids do you want food", "kitchen",
                                     followup=followup, on_accept=accepted)
        return r, accepted, claude, orouter

    async def test_not_for_me_is_silent(self):
        r, accepted, claude, _ = await self._ask("===NOT_", "FOR_ME===")
        self.assertEqual(r["escape"], "none")
        accepted.assert_not_awaited()
        self.emit.assert_not_awaited()
        self.record_mock.assert_not_called()
        self.assertIn("Turn: follow-up", claude.call_args.args[1])

    async def test_command_verdict(self):
        r, accepted, _, _ = await self._ask("===COMMAND===")
        self.assertEqual(r["escape"], "command")
        accepted.assert_not_awaited()
        self.emit.assert_not_awaited()

    async def test_real_followup_is_answered_and_accept_fires_first(self):
        order = []
        accepted = AsyncMock(side_effect=lambda: order.append("accept"))
        self.emit.side_effect = lambda *a, **k: order.append("event")
        with patch.object(ask.claude_knowledge, "stream_chat",
                          return_value=_chunks("Skin on ", "is fine.\n", ask.SENTINEL,
                                               "\nThin slices only.")):
            r = await ask.handle_ask("what about with the skin on", "kitchen",
                                     followup=True, on_accept=accepted)
        self.assertNotIn("escape", r)
        self.assertEqual(r["response"], "Skin on is fine.")
        accepted.assert_awaited_once()
        self.assertEqual(order[0], "accept")

    async def test_claude_unreachable_hands_the_turn_to_the_classifier(self):
        r, accepted, _, orouter = await self._ask(fail_with=RuntimeError("service down"))
        self.assertEqual(r["escape"], "command")
        orouter.assert_not_called()         # no verdict-less fallback on a follow-up
        accepted.assert_not_awaited()

    async def test_wake_turn_prompt_is_not_marked_followup(self):
        with patch.object(ask, "_history_messages", return_value=[]):
            self.assertIn("Turn: wake word", ask._claude_prompt("q", "kitchen"))
            self.assertIn("Turn: follow-up", ask._claude_prompt("q", "kitchen", True))

    def test_static_prompt_defines_both_escapes(self):
        st = ask._system_static()
        self.assertIn(ask.ESCAPE_NOT_FOR_ME, st)
        self.assertIn(ask.ESCAPE_COMMAND, st)


class _FakeService(BaseHTTPRequestHandler):
    lines: list[dict] = []
    seen: dict = {}

    def log_message(self, *a):
        pass

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        type(self).seen = json.loads(self.rfile.read(n))
        self.send_response(200)
        self.send_header("Content-Type", "application/x-ndjson")
        self.end_headers()
        for ev in type(self).lines:
            self.wfile.write((json.dumps(ev) + "\n").encode())
            self.wfile.flush()


class WireClientTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.srv = HTTPServer(("127.0.0.1", 0), _FakeService)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.addCleanup(self.srv.server_close)
        self.addCleanup(self.srv.shutdown)
        p = patch.object(config, "CLAUDE_KNOWLEDGE_URL",
                         f"http://127.0.0.1:{self.srv.server_address[1]}")
        p.start()
        self.addCleanup(p.stop)

    async def _run(self, lines):
        _FakeService.lines = lines
        stats: dict = {}
        out = []
        async for d in claude_knowledge.stream_chat("SYS", "PROMPT", stats=stats):
            out.append(d)
        return "".join(out), stats

    async def test_deltas_and_stats(self):
        text, stats = await self._run([
            {"t": "meta", "model": "claude-fable-5-2", "warm": True},
            {"t": "delta", "text": "Two "}, {"t": "delta", "text": "tablespoons."},
            {"t": "done", "model": "claude-fable-5-2", "searches": 1, "tokens": 4321,
             "notional_cost_usd": 0.07, "ms": 1500, "first_text_ms": 500}])
        self.assertEqual(text, "Two tablespoons.")
        self.assertEqual(_FakeService.seen, {"system": "SYS", "prompt": "PROMPT"})
        self.assertEqual(stats["backend"], "claude")
        self.assertEqual(stats["model"], "claude-fable-5-2")
        self.assertEqual(stats["searches"], 1)
        self.assertEqual(stats["tokens"], 4321)
        self.assertIsNone(stats["cost"])           # subscription: no charge to file

    async def test_error_event_raises(self):
        with self.assertRaisesRegex(RuntimeError, "every model failed"):
            await self._run([{"t": "error", "error": "every model failed"}])

    async def test_stream_cut_short_raises(self):
        with self.assertRaisesRegex(RuntimeError, "closed the stream early"):
            await self._run([{"t": "delta", "text": "Half"}])

    async def test_service_down_raises(self):
        with patch.object(config, "CLAUDE_KNOWLEDGE_URL", "http://127.0.0.1:9"):
            with self.assertRaises(Exception):
                await self._run([])


if __name__ == "__main__":
    unittest.main()
