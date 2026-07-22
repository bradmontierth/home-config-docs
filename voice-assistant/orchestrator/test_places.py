from datetime import datetime
import unittest
from zoneinfo import ZoneInfo

from . import places


class PlacesFormattingTest(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 7, 21, 14, 0,
                            tzinfo=ZoneInfo("America/Denver"))
        self.place = {
            "displayName": {"text": "The Home Depot"},
            "currentOpeningHours": {
                "openNow": True,
                "nextCloseTime": "2026-07-22T04:00:00Z",
                "periods": [{
                    "open": {"date": {"year": 2026, "month": 7, "day": 21},
                             "day": 2, "hour": 6, "minute": 0},
                    "close": {"date": {"year": 2026, "month": 7, "day": 21},
                              "day": 2, "hour": 22, "minute": 0},
                }],
            },
        }

    def test_open_now(self):
        self.assertEqual(
            places._answer(self.place, "now", self.now),
            "Yes, The Home Depot is open until 10 PM.")

    def test_close(self):
        self.assertEqual(
            places._answer(self.place, "close", self.now),
            "The Home Depot closes at 10 PM tonight.")

    def test_opened(self):
        self.assertEqual(
            places._answer(self.place, "open", self.now),
            "The Home Depot opened at 6 AM this morning.")

    def test_today(self):
        self.assertEqual(
            places._answer(self.place, "today", self.now),
            "The Home Depot is open 6 AM to 10 PM today.")

    def test_closed_holiday(self):
        place = {
            "displayName": {"text": "Costco"},
            "currentOpeningHours": {
                "openNow": False,
                "nextOpenTime": "2026-07-22T16:00:00Z",
                "periods": [],
            },
        }
        self.assertEqual(places._answer(place, "today", self.now),
                         "Costco is closed all day today.")
        self.assertEqual(places._answer(place, "now", self.now),
                         "No, Costco is closed — it opens at 10 AM tomorrow.")

    def test_name_match_confidence(self):
        self.assertGreaterEqual(
            places.fuzz.WRatio(places._normalized("Home Depot"),
                               places._normalized("The Home Depot")),
            places._MATCH_THRESHOLD)
        self.assertLess(
            places.fuzz.WRatio(places._normalized("blorbcorp"),
                               places._normalized("Labcorp")),
            places._MATCH_THRESHOLD)


if __name__ == "__main__":
    unittest.main()
