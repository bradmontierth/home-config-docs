import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from . import app as app_mod
from . import config, find_phone, speaker


class DecideTest(unittest.TestCase):
    """Threshold + margin decision logic (calibrated 0.35 / 0.15)."""

    def test_clear_winner_is_named(self):
        d = speaker.decide({"brad": 0.62, "adrienne": 0.14})
        self.assertEqual(d["speaker"], "brad")
        self.assertEqual(d["score"], 0.62)
        self.assertAlmostEqual(d["margin"], 0.48)

    def test_below_absolute_threshold_is_unsure(self):
        # A kid/guest: beats nobody's bar even with a wide margin.
        d = speaker.decide({"brad": 0.30, "adrienne": 0.05})
        self.assertEqual(d["speaker"], "unsure")
        self.assertEqual(d["top"], "brad")

    def test_narrow_margin_is_unsure(self):
        # Both centroids score high-ish: never guess between them.
        d = speaker.decide({"brad": 0.45, "adrienne": 0.38})
        self.assertEqual(d["speaker"], "unsure")

    def test_exact_threshold_and_margin_accept(self):
        d = speaker.decide({"brad": config.SPEAKER_THRESHOLD,
                            "adrienne": config.SPEAKER_THRESHOLD - config.SPEAKER_MARGIN})
        self.assertEqual(d["speaker"], "brad")


class ProfilesTest(unittest.TestCase):
    def setUp(self):
        speaker._profiles_cache = None
        self.addCleanup(setattr, speaker, "_profiles_cache", None)

    def _write(self, payload) -> str:
        tmp = tempfile.NamedTemporaryFile(
            "w", suffix=".json", delete=False, encoding="utf-8")
        self.addCleanup(Path(tmp.name).unlink, missing_ok=True)
        json.dump(payload, tmp)
        tmp.close()
        return tmp.name

    def test_missing_file_is_empty(self):
        with patch.object(config, "SPEAKER_PROFILES_FILE", "/nonexistent/p.json"):
            self.assertEqual(speaker._profiles(), {})

    def test_load_and_hot_reload_on_mtime(self):
        path = self._write({"profiles": {"brad": {"centroid": [1.0, 0.0], "clips": 5},
                                         "adrienne": {"centroid": [0.0, 1.0], "clips": 5}}})
        with patch.object(config, "SPEAKER_PROFILES_FILE", path):
            self.assertEqual(set(speaker._profiles()), {"brad", "adrienne"})
            # Rewrite with a new mtime: cache must refresh.
            Path(path).write_text(json.dumps(
                {"profiles": {"brad": {"centroid": [1.0, 0.0], "clips": 9}}}))
            import os
            os.utime(path, (0, 9_999_999_999))
            self.assertEqual(set(speaker._profiles()), {"brad"})

    def test_corrupt_file_is_empty_not_fatal(self):
        path = self._write({"wrong": "shape"})
        with patch.object(config, "SPEAKER_PROFILES_FILE", path):
            self.assertEqual(speaker._profiles(), {})


def _resolve_name(result=None, exc=None):
    """Run app._speaker_name against a task that returns `result` / raises."""
    async def _go():
        async def _fake():
            if exc:
                raise exc
            return result
        return await app_mod._speaker_name(asyncio.create_task(_fake()))
    return asyncio.run(_go())


class SpeakerNameTest(unittest.TestCase):
    """The lazy await person-dependent handlers rely on."""

    def test_no_task_means_no_owner(self):
        self.assertIsNone(asyncio.run(app_mod._speaker_name(None)))

    def test_identified_speaker_resolves(self):
        self.assertEqual(_resolve_name({"speaker": "adrienne", "score": 0.6}),
                         "adrienne")

    def test_unsure_and_service_down_fall_back(self):
        self.assertIsNone(_resolve_name({"speaker": "unsure", "score": 0.3}))
        self.assertIsNone(_resolve_name(None))          # identify() gave up
        self.assertIsNone(_resolve_name(exc=RuntimeError("gx10 down")))


class FindPhoneIsSelfTest(unittest.TestCase):
    def test_self_phrases(self):
        for phrase in ("my", "mine", "me", "", None, " My "):
            self.assertTrue(find_phone.is_self(phrase), phrase)

    def test_named_owners_are_not_self(self):
        for phrase in ("brad", "adrienne", "mom", "brad's"):
            self.assertFalse(find_phone.is_self(phrase), phrase)


if __name__ == "__main__":
    unittest.main()
