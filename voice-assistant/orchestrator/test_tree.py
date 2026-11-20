import json
from unittest.mock import AsyncMock, patch

import httpx

from . import app, tree
from .test_clarify import _SlotTestBase

# Live layout 2026-11-03 (Pride 2015): tree id 0, star id 1.
_STATE = {"on": True, "bri": 255, "seg": [
    {"id": 0, "start": 0, "stop": 300, "fx": 63},
    {"id": 1, "start": 300, "stop": 409, "fx": 0}]}
# Meteor / Off / White presets: the tree is two segments and the star is id 2.
_METEOR = {"on": True, "bri": 200, "seg": [
    {"id": 0, "start": 0, "stop": 225, "fx": 77},
    {"id": 1, "start": 225, "stop": 300, "fx": 77},
    {"id": 2, "start": 300, "stop": 409, "fx": 0}]}


class TreeParseTest(_SlotTestBase):
    def test_phrases(self):
        for phrase, want in [
            ("set the star to red", {"star": "red"}),
            ("Set the star to red.", {"star": "red"}),
            ("make the star gold", {"star": "gold"}),
            ("turn the tree green please", {"tree": "green"}),
            ("change the christmas tree to blue", {"tree": "blue"}),
            ("set the color of the star to hot pink", {"star": "hot pink"}),
            ("set the star's color to white", {"star": "white"}),
            ("set the tree lights to the color purple", {"tree": "purple"}),
            ("can you make the tree red and the star gold",
             {"tree": "red", "star": "gold"}),
            ("set the tree and the star to blue", {"tree": "blue", "star": "blue"}),
            ("set the tree and star to blue", {"tree": "blue", "star": "blue"}),
            ("set the star to plaid", {"star": None}),
        ]:
            with self.subTest(phrase=phrase):
                self.assertEqual(tree.parse(phrase), want)

    def test_unrelated(self):
        for phrase in ["play twinkle twinkle little star", "set the star",
                       "turn on the tree", "give me chaos in red",
                       "set the lights to blue", "what color is the star",
                       "set a timer for the tree", "turn the tree off"]:
            with self.subTest(phrase=phrase):
                self.assertIsNone(tree.parse(phrase))


class TreePayloadTest(_SlotTestBase):
    def test_star_only_touches_star_segment(self):
        body = tree.payload(_STATE, {"star": "red"})
        self.assertEqual(body, {"seg": [
            {"id": 1, "on": True, "fx": 0, "col": [[255, 0, 0, 0]]}]})

    def test_star_found_by_position_not_id(self):
        body = tree.payload(_METEOR, {"star": "red"})
        self.assertEqual([s["id"] for s in body["seg"]], [2])
        body = tree.payload(_METEOR, {"tree": "green"})
        self.assertEqual([s["id"] for s in body["seg"]], [0, 1])

    def test_white_uses_white_channel(self):
        self.assertEqual(tree.rgbw("white"), [0, 0, 0, 255])

    def test_tree_off_lights_only_the_asked_part(self):
        off = dict(_STATE, on=False)
        body = tree.payload(off, {"star": "red"})
        self.assertTrue(body["on"])
        self.assertEqual(body["seg"][0], {"id": 0, "on": False})
        self.assertEqual(body["seg"][1]["id"], 1)


class TreeDispatchTest(_SlotTestBase):
    def _client(self, respond):
        original = httpx.AsyncClient
        return patch.object(tree.httpx, "AsyncClient", side_effect=lambda **kw:
                            original(transport=httpx.MockTransport(respond), **kw))

    def test_voice_turn_bypasses_classifier_and_posts(self):
        posts = []

        def respond(request):
            if request.method == "GET":
                return httpx.Response(200, json=_STATE)
            posts.append(json.loads(request.content))
            return httpx.Response(200, json={"success": True})

        with self._client(respond), \
                patch.object(app.broadcast_mod, "send", AsyncMock()), \
                patch.object(app.intent_mod, "parse", AsyncMock()) as parse:
            result = self._run("set the star to red")
        parse.assert_not_awaited()
        self.assertTrue(result["ok"])
        self.assertEqual(result["intent"], "home_control")
        self.assertEqual(result["response"], "Setting the star to red.")
        self.assertEqual(posts, [{"seg": [
            {"id": 1, "on": True, "fx": 0, "col": [[255, 0, 0, 0]]}]}])

    def test_unreachable_tree_refuses(self):
        def respond(request):
            raise httpx.ConnectError("no route")

        with self._client(respond), \
                patch.object(app.broadcast_mod, "send", AsyncMock()), \
                patch.object(app.intent_mod, "parse", AsyncMock()):
            result = self._run("make the tree green")
        self.assertFalse(result["ok"])
        self.assertIn("isn't answering", result["response"])

    def test_unknown_color_asks_without_actuation(self):
        with patch.object(tree.httpx, "AsyncClient") as client, \
                patch.object(app.broadcast_mod, "send", AsyncMock()), \
                patch.object(app.intent_mod, "parse", AsyncMock()):
            result = self._run("set the star to plaid")
        client.assert_not_called()
        self.assertFalse(result["ok"])
