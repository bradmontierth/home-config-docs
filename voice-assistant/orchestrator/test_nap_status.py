import asyncio
import unittest
from datetime import datetime
from zoneinfo import ZoneInfo
from unittest.mock import AsyncMock, patch

from . import app, intent, nap_status
from .intent_schema import INTENTS, SCHEMA

# Household time, whatever the test machine's zone (the container runs on UTC).
def _t(hm):
    return datetime.strptime(f"2026-10-24 {hm}", "%Y-%m-%d %H:%M").replace(
        tzinfo=ZoneInfo("America/Denver")).timestamp()


NOW = _t("15:30")   # a Saturday afternoon


def _nap(kid, in_bed, out, asleep=None, awake=None, kind="nap", is_open=False, cam=None):
    return {"cam": cam or kid.lower(), "kid": kid, "kind": kind, "in_bed": _t(in_bed),
            "out": _t(out) if out else None, "asleep": _t(asleep) if asleep else None,
            "awake": _t(awake) if awake else None, "open": is_open}


class FastParseTest(unittest.TestCase):

    CASES = {
        "did claire nap today": "claire",
        "did Clare nap": "claire",
        "has the baby napped yet": "claire",
        "when did claire go down": "claire",
        "what time did simon fall asleep": "simon",
        "how long did claire nap": "claire",
        "how long did the baby sleep for": "claire",
        "is claire still asleep": "claire",
        "is simon napping": "simon",
        "did simon take a nap today": "simon",
        "did the kids nap": "both",
        "did the kids nap today": "both",
        "how was claire's nap": "claire",
        "claire's nap": "claire",
        "any naps today": "both",
        "did anyone nap": "both",
        "when did claire wake up from her nap": "claire",
        # Loose net: real misses from the turn log, 2026-10-29 .. 11-09.
        "what time did the kids sleep today": "both",
        "did simon nap tonight": "simon",
        "who had a nap today": "both",
        "did simon map today": "simon",
        "when did claire sleep": "claire",
        "when did claires nap today": "claire",
        "what time was claire's nap": "claire",
        "how did claire sleep today": "claire",
        "what time did simon go down for his nap": "simon",
        "who napped today": "both",
        "see if the kids take a nap today": "both",
        "check if claire is asleep": "claire",
        "can you see if simon napped": "simon",
        "could you check whether the baby slept today": "claire",
    }

    def test_phrasings(self):
        for text, kid in self.CASES.items():
            with self.subTest(text=text):
                parsed = intent.fast_parse(text)
                self.assertIsNotNone(parsed, text)
                self.assertEqual(parsed["intent"], "nap_status")
                self.assertEqual(parsed["nap_kid"], kid)

    def test_other_things_do_not_match(self):
        for text in ("show me claire", "show claire's camera", "when did claire wake up",
                     "play nap time music", "set a nap timer for twenty minutes",
                     "is the house secure", "what time is it", "turn off claire's light",
                     "how did claire sleep last night", "what time did simon go to bed",
                     "tell simon to take a nap", "remind me to put claire down for a nap",
                     "did simon wake up this morning", "is anyone sleeping over",
                     "how long until claire's nap timer",
                     "see if claire is up", "check if simon slept okay last night"):
            with self.subTest(text=text):
                self.assertIsNone(nap_status.fast_kid(intent._fast_clean(text)), text)


class AnswerTest(unittest.TestCase):

    def test_one_nap(self):
        naps = [_nap("Claire", "11:08", "13:03", "11:21", "12:50")]
        self.assertEqual(nap_status.answer(naps, "claire", NOW),
                         "Yes, Claire napped from 11:21 to 12:50, about an hour and a half.")

    def test_napping_now(self):
        naps = [_nap("Claire", "14:55", None, "15:02", None, is_open=True)]
        self.assertEqual(nap_status.answer(naps, "claire", NOW),
                         "Claire is napping now. She fell asleep at 3:02, about 28 minutes ago.")

    def test_in_bed_not_asleep(self):
        naps = [_nap("Simon", "15:10", None, kind="rest", is_open=True)]
        self.assertEqual(nap_status.answer(naps, "simon", NOW),
                         "Simon is in bed now, since 3:10, but he isn't asleep yet.")

    def test_quiet_time_without_sleep(self):
        naps = [_nap("Simon", "11:10", "12:49", kind="rest")]
        self.assertEqual(nap_status.answer(naps, "simon", NOW),
                         "Simon had quiet time in bed from 11:10 to 12:49, but he didn't fall asleep.")

    def test_two_naps_two_places(self):
        naps = [_nap("Claire", "10:38", "11:52", "10:40", "11:50", cam="claire"),
                _nap("Claire", "14:53", "15:31", "14:58", "15:29", cam="basement")]
        self.assertEqual(nap_status.answer(naps, "claire", NOW),
                         "Yes, Claire napped twice: 10:40 to 11:50, and 2:58 to 3:29. "
                         "That's about an hour and 45 minutes in all.")

    def test_none_yet_and_both(self):
        naps = [_nap("Claire", "11:08", "13:03", "11:21", "12:50")]
        self.assertEqual(nap_status.answer(naps, "both", NOW),
                         "Yes, Claire napped from 11:21 to 12:50, about an hour and a half. "
                         "Simon hasn't napped yet today.")
        late = _t("19:30")
        self.assertEqual(nap_status.answer([], "simon", late), "Simon didn't nap today.")

    def test_bedtime_is_not_a_nap(self):
        naps = [_nap("Claire", "18:10", "18:30", "18:15", "18:30", kind="bedtime")]
        self.assertEqual(nap_status.answer(naps, "claire", NOW), "Claire hasn't napped yet today.")


class ValidateTest(unittest.TestCase):

    def test_kid_slot(self):
        self.assertIn("nap_status", INTENTS)
        self.assertIn("nap_kid", SCHEMA["properties"])
        self.assertEqual(intent.validate({"intent": "nap_status", "nap_kid": "simon"})["nap_kid"], "simon")
        self.assertEqual(intent.validate({"intent": "nap_status"})["nap_kid"], "both")
        self.assertEqual(intent.validate({"intent": "nap_status", "nap_kid": "rover"})["nap_kid"], "both")
        self.assertIsNone(intent.validate({"intent": "weather", "nap_kid": "simon"})["nap_kid"])

    def test_prompt_mentions_intent(self):
        system = intent._system("kitchen")
        self.assertIn("nap_status =", system)
        self.assertIn('"nap_kid"', system)


class AppWiringTest(unittest.TestCase):

    def _run(self, phrase, fetch):
        parse = AsyncMock()
        token = app._CUR_SAT.set("kitchen")
        self.addCleanup(app._CUR_SAT.reset, token)
        with (
            patch.object(app.intent_mod, "parse", new=parse),
            patch.object(nap_status, "_fetch", new=fetch),
            patch.object(app, "_speak_reply", new=AsyncMock()),
            patch.object(app.broadcast_mod, "send", new=AsyncMock()),
            patch.object(app.events, "emit", new=AsyncMock()),
        ):
            result = asyncio.run(app.handle_command(phrase))
        parse.assert_not_called()
        return result

    def test_fast_path_answers_from_lookout(self):
        naps = [_nap("Claire", "11:08", "13:03", "11:21", "12:50")]
        result = self._run("did claire nap today", AsyncMock(return_value=naps))
        self.assertTrue(result["ok"])
        self.assertTrue(result["response"].startswith("Yes, Claire napped from 11:21 to 12:50"))

    def test_lookout_down_refuses(self):
        result = self._run("did the kids nap", AsyncMock(side_effect=OSError("connection refused")))
        self.assertFalse(result["ok"])
        self.assertEqual(result["response"], "Sorry, I couldn't check the nap tracker.")


if __name__ == "__main__":
    unittest.main()
