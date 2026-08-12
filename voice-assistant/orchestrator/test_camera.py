import asyncio
import unittest
from unittest.mock import AsyncMock, patch

from . import camera, intent


def _fast(text):
    return intent.fast_parse(text) or {}


class CameraResolveTest(unittest.TestCase):
    """Which spoken phrases name a child's camera."""

    def test_names_shapes_and_possessives(self):
        cases = {
            "simon": "simon",
            "Simon's": "simon",
            "simons room": "simon",
            "the camera in Simon's room": "simon",
            "claire": "claire",
            "Claire's camera": "claire",
            # What the ASR hands us for Claire often enough to matter.
            "clare": "claire",
        }
        for text, key in cases.items():
            with self.subTest(text=text):
                self.assertEqual(camera.resolve(text), key)

    def test_unknown_names_do_not_resolve(self):
        # "the baby" points at both children and belongs to neither: Simon's
        # camera is named BabyCAMR upstream, Claire is the one with the nap
        # monitor. Showing the wrong kid is worse than asking.
        for text in ("the baby", "brad", "the dog", "", None, "the doorbell"):
            with self.subTest(text=text):
                self.assertIsNone(camera.resolve(text))


class CameraFastParseTest(unittest.TestCase):
    """The deterministic path, which keeps kid cameras off the classifier."""

    def test_show_phrasings(self):
        cases = {
            "show me simon": "simon",
            "show me Claire": "claire",
            "pull up simon's camera": "simon",
            "bring up claire": "claire",
            "let me see simon": "simon",
            "i want to see claire": "claire",
            "check on simon": "simon",
            "put claire's room on the screen": "claire",
            "show me the camera in simon's room": "simon",
            "show me claire please": "claire",
        }
        for text, target in cases.items():
            with self.subTest(text=text):
                parsed = _fast(text)
                self.assertEqual(parsed.get("intent"), "show_camera")
                self.assertEqual(parsed.get("camera_target"), target)

    def test_close_phrasings(self):
        for text in ("close the camera", "stop the camera", "turn off the camera",
                     "close the video", "hide the camera", "close simon",
                     "I'm done with the camera"):
            with self.subTest(text=text):
                self.assertEqual(_fast(text).get("intent"), "close_camera")

    def test_neighbouring_intents_are_not_stolen(self):
        # Each of these lives one word away from a camera phrase.
        cases = {
            "show me home depot": "place_search is a place, not a person",
            "tell simon to come eat": "broadcast",
            "turn on simon's lights": "home_control",
            "show me that again": "show_answer",
            "show me the shopping list": "show_shopping",
            "open simon's blinds": "home_control",
            "where is simon": "not a camera request",
            # Both kids' names are also real chains — Simon Property Group runs
            # the malls, Claire's is the accessory store. A distance or hours
            # question is never a camera request. (Adding show_camera to the
            # prompt did briefly break these two on the classifier; the
            # description now rules them out explicitly.)
            "how far is claire's": "place_search",
            "is claire's open": "business_hours",
            "show me simon mall": "place_search",
            "show me claire's boutique": "place_search",
        }
        for text, why in cases.items():
            with self.subTest(text=text):
                self.assertNotIn(_fast(text).get("intent"),
                                 ("show_camera", "close_camera"), why)

    def test_bare_dismissals_are_state_gated_not_fast_parsed(self):
        # "go back" means close the camera only while one is up, so it must not
        # resolve deterministically — app.handle_command checks the display.
        for text in ("go back", "close it", "back"):
            with self.subTest(text=text):
                self.assertNotIn(_fast(text).get("intent"),
                                 ("show_camera", "close_camera"))
                self.assertTrue(intent.is_camera_back(text))

    def test_ordinary_speech_is_not_a_dismissal(self):
        for text in ("what's the weather", "pause the music", "go back to the office"):
            with self.subTest(text=text):
                self.assertFalse(intent.is_camera_back(text))

    def test_camera_target_only_survives_on_show_camera(self):
        parsed = intent.validate({"intent": "ask", "camera_target": "simon"})
        self.assertIsNone(parsed["camera_target"])


class CameraHandleTest(unittest.TestCase):
    """Handler behaviour against a stubbed display helper."""

    def setUp(self):
        camera._cancel_audio()

    def test_show_opens_video_then_schedules_audio(self):
        calls = []

        async def fake_request(method, path):
            calls.append((method, path))
            return {}

        async def run():
            with patch.object(camera, "_request", side_effect=fake_request):
                # Zero delay so the scheduled audio task runs inside the test;
                # in production it is held back behind the spoken reply.
                result = await camera.handle({"camera_target": "simon"})
                await asyncio.sleep(0)
                await camera._audio_task
                return result

        with patch.object(camera.config, "CAMERA_AUDIO_DELAY_S", 0):
            result = asyncio.run(run())

        self.assertTrue(result["ok"])
        self.assertEqual(result["camera"], "simon")
        self.assertEqual(result["response"], "Showing Simon.")
        self.assertEqual(calls, [("POST", "/open/simon"),
                                 ("POST", "/audio/play/simon")])

    def test_close_inside_the_delay_window_cancels_the_audio(self):
        """A view dismissed before the audio fires must not bring the audio up
        for a camera that is no longer on screen."""
        calls = []

        async def fake_request(method, path):
            calls.append((method, path))
            return {}

        async def run():
            with patch.object(camera, "_request", side_effect=fake_request):
                await camera.show("claire", delay=30)
                await camera.close()
                await asyncio.sleep(0)
                return camera._audio_task

        asyncio.run(run())
        self.assertEqual(calls, [("POST", "/open/claire"), ("POST", "/close")])

    def test_unresolved_target_offers_the_two_cameras(self):
        result = asyncio.run(camera.handle({"camera_target": "the baby"}))
        self.assertFalse(result["ok"])
        self.assertIn("Simon", result["response"])
        self.assertIn("Claire", result["response"])

    def test_unreachable_helper_is_reported_not_raised(self):
        async def run():
            with patch.object(camera, "_request",
                              AsyncMock(side_effect=RuntimeError("no route"))):
                return await camera.handle({"camera_target": "simon"})

        result = asyncio.run(run())
        self.assertFalse(result["ok"])
        self.assertIn("couldn't reach", result["response"])

    def test_close_with_nothing_showing_says_so(self):
        async def run():
            with patch.object(camera, "_request",
                              AsyncMock(return_value={"running": False})):
                return await camera.handle_close()

        result = asyncio.run(run())
        self.assertTrue(result["ok"])
        self.assertIn("Nothing", result["response"])

    def test_close_stops_a_running_view(self):
        calls = []

        async def fake_request(method, path):
            calls.append((method, path))
            return {"running": True, "stream": "simon"}

        async def run():
            with patch.object(camera, "_request", side_effect=fake_request):
                return await camera.handle_close()

        result = asyncio.run(run())
        self.assertTrue(result["ok"])
        self.assertIn(("POST", "/close"), calls)

    def test_status_swallows_an_unreachable_display(self):
        async def run():
            with patch.object(camera, "_request",
                              AsyncMock(side_effect=RuntimeError("down"))):
                return await camera.is_open()

        self.assertFalse(asyncio.run(run()))


if __name__ == "__main__":
    unittest.main()
