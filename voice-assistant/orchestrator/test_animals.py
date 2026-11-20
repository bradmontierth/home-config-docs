import unittest
import tempfile
import json
from pathlib import Path
from unittest.mock import AsyncMock, patch
from . import animals, app, intent
from .test_clarify import _SlotTestBase


class AnimalParseTest(unittest.TestCase):
    def test_exact_phrases(self):
        for text, target in [("What sound does a lion make?", "lion"),
                             ("what noise do elephants make", "elephant"),
                             ("what does a frog say", "frog"),
                             ("make an elephant sound please", "elephant")]:
            self.assertEqual(intent.fast_parse(text)["animal"], target)

    def test_expanded_library_singular_and_plural(self):
        for animal in animals.names():
            self.assertEqual(intent.fast_parse(f"what sound does a {animal} make")["animal"], animal)
            plural = animal if animal == "sheep" else animal + "s"
            self.assertEqual(intent.fast_parse(f"what sound do {plural} make")["animal"], animal)

    def test_does_not_steal_nearby_commands(self):
        for text in ("why does a lion roar", "play the lion king", "show me simon",
                     "what sound does a monkey make", "stop", "again"):
            self.assertNotEqual((intent.fast_parse(text) or {}).get("intent"), "animal_sound")


class AnimalDispatchTest(_SlotTestBase):
    @patch.object(app.camera_mod, "is_open", AsyncMock(return_value=False))
    def test_fast_dispatch_has_no_tts(self):
        with patch.object(animals, "request", AsyncMock(return_value={"animal":"lion"})) as req, \
             patch.object(intent, "parse", AsyncMock(side_effect=AssertionError("classifier called"))), \
             patch.object(app, "_speak_reply", AsyncMock()) as tts:
            result = self._run("what sound does a lion make")
        self.assertTrue(result["ok"])
        self.assertTrue(result["end_session"])
        self.assertIsNone(result["audio_url"])
        tts.assert_not_called()
        req.assert_awaited_once_with("play", {"animal":"lion"})

    def test_other_rooms_do_not_open_kitchen(self):
        with patch.object(app.events, "on_dashboard", return_value=False), \
             patch.object(animals, "request", AsyncMock()) as req:
            result = self._run("what sound does a frog make")
        self.assertFalse(result["ok"])
        req.assert_not_called()


class AnimalContextTest(unittest.IsolatedAsyncioTestCase):
    async def test_replay_requires_recent_animal(self):
        with patch.object(animals, "request", AsyncMock(return_value={"recent":False})):
            self.assertIsNone(await animals.contextual("again"))
        with patch.object(animals, "request", AsyncMock(return_value={"recent":True})):
            self.assertEqual((await animals.contextual("again"))["animal"], "again")

    async def test_unsupported_animal_never_requests_playback(self):
        with patch.object(animals, "request", AsyncMock()) as req:
            self.assertFalse((await animals.handle("monkey"))["ok"])
            req.assert_not_called()


class DynamicCatalogTest(unittest.TestCase):
    def test_new_label_and_alias_hot_reload_without_classifier_or_restart(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "catalog.json"
            path.write_text(json.dumps({"lion":{"name":"Lion"}}))
            with patch.object(animals, "CATALOG_PATH", path), \
                 patch.object(animals, "_cache_catalog", {}), \
                 patch.object(animals, "_cache_signature", None):
                self.assertIsNone(animals.fast_parse("what sound does a sea lion make"))
                staging = path.with_suffix('.tmp')
                staging.write_text(json.dumps({"sea-lion":{"name":"Sea Lion", "aliases":["seal friend"]}}))
                staging.replace(path)
                self.assertEqual(animals.fast_parse("what sound does a sea lion make")["animal"], "sea-lion")
                self.assertEqual(animals.fast_parse("what do sea lions sound like")["animal"], "sea-lion")
                self.assertEqual(animals.resolve("seal friend"), "sea-lion")
                self.assertIn('seal friend', intent._system('kitchen'))
                self.assertNotIn('"lion": "lion"', intent._system('kitchen'))

    def test_malformed_update_retains_last_good_catalog(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'catalog.json'
            path.write_text('{"lion":{"name":"Lion"}}')
            with patch.object(animals,'CATALOG_PATH',path),patch.object(animals,'_cache_catalog',{}),patch.object(animals,'_cache_signature',None):
                self.assertEqual(animals.resolve('lions'),'lion')
                path.write_text('{broken')
                self.assertEqual(animals.resolve('lions'),'lion')
