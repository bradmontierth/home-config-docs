from datetime import datetime
import unittest
from unittest.mock import AsyncMock, patch
from zoneinfo import ZoneInfo

from . import intent, places


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

    def test_distance_is_straight_line_miles(self):
        # One degree of longitude near the equator is about 69 miles.
        self.assertAlmostEqual(
            places._distance_m(0, 0, 0, 1) / 1609.344,
            69.1,
            places=1,
        )

    def test_cache_expires_after_next_hours_transition(self):
        now = datetime(2026, 7, 21, 20, 0, tzinfo=ZoneInfo("UTC"))
        raw = [{"currentOpeningHours": {
            "nextCloseTime": "2026-07-21T22:00:00Z",
        }}]
        self.assertEqual(places._result_cache_ttl(raw, now), 7260.0)

    def test_public_place_includes_schedule_and_evidence(self):
        place = {
            **self.place,
            "id": "home-depot-1",
            "formattedAddress": "123 Main St, Riverton, UT",
            "location": {"latitude": 40.5, "longitude": -112.0},
            "regularOpeningHours": {
                "weekdayDescriptions": [
                    "Monday: 6:00 AM – 10:00 PM",
                    "Tuesday: 6:00 AM – 10:00 PM",
                ],
            },
        }
        public = places._public_place(place, 3218.688, 0, self.now)
        self.assertEqual(public["id"], "home-depot-1")
        self.assertEqual(public["distance_miles"], 2.0)
        self.assertEqual(public["status"], "Open")
        self.assertEqual(public["schedule"][0], {
            "day": "Monday", "hours": "6:00 AM – 10:00 PM",
        })

    def test_exact_store_name_suppresses_same_chain_departments(self):
        raw = [
            {
                "id": "garden",
                "displayName": {"text": "Garden Center at The Home Depot"},
                "location": {"latitude": 40.50, "longitude": -112.0},
            },
            {
                "id": "store",
                "displayName": {"text": "The Home Depot"},
                "location": {"latitude": 40.50, "longitude": -112.0},
            },
            {
                "id": "store-2",
                "displayName": {"text": "The Home Depot"},
                "location": {"latitude": 40.55, "longitude": -112.0},
            },
        ]
        with patch.object(places.config, "HOME_LAT", 40.494), \
                patch.object(places.config, "HOME_LON", -112.0), \
                patch.object(places.config, "PLACES_LOCATION_RADIUS_M", 16093.44):
            nearby = places._matching_nearby("Home Depot", raw)
        self.assertEqual([item[0]["id"] for item in nearby], ["store", "store-2"])

    def test_generic_costco_resolves_wholesale_not_colocated_services(self):
        raw = [
            {
                "id": "tire",
                "displayName": {"text": "Costco Tire Service Center"},
                "formattedAddress": "123 Main St, Riverton, UT",
                "location": {"latitude": 40.50, "longitude": -112.0},
                "primaryType": "tire_shop",
                "types": ["tire_shop", "store"],
            },
            {
                "id": "bakery",
                "displayName": {"text": "Costco Bakery"},
                "formattedAddress": "123 Main St, Riverton, UT",
                "location": {"latitude": 40.50, "longitude": -112.0},
                "primaryType": "bakery",
            },
            {
                "id": "warehouse",
                "displayName": {"text": "Costco Wholesale"},
                "formattedAddress": "123 Main St, Riverton, UT",
                "location": {"latitude": 40.50, "longitude": -112.0},
                "primaryType": "warehouse_store",
            },
            {
                "id": "gas",
                "displayName": {"text": "Costco Gas Station"},
                "formattedAddress": "123 Main St, Riverton, UT",
                "location": {"latitude": 40.5002, "longitude": -112.0},
                "primaryType": "gas_station",
                "containingPlaces": [{"id": "warehouse"}],
            },
        ]
        with patch.object(places.config, "HOME_LAT", 40.494), \
                patch.object(places.config, "HOME_LON", -112.0), \
                patch.object(places.config, "PLACES_LOCATION_RADIUS_M", 16093.44):
            nearby = places._matching_nearby("Costco", raw)
        self.assertEqual([item[0]["id"] for item in nearby], ["warehouse"])

    def test_explicit_costco_service_selects_requested_type(self):
        raw = [
            {
                "id": "warehouse",
                "displayName": {"text": "Costco Wholesale"},
                "formattedAddress": "123 Main St, Riverton, UT",
                "location": {"latitude": 40.50, "longitude": -112.0},
                "primaryType": "warehouse_store",
                "types": ["warehouse_store", "store"],
            },
            {
                "id": "tire",
                "displayName": {"text": "Costco Tire Service Center"},
                "formattedAddress": "123 Main St, Riverton, UT",
                "location": {"latitude": 40.50, "longitude": -112.0},
                "primaryType": "tire_shop",
                "types": ["tire_shop", "store"],
            },
            {
                "id": "gas",
                "displayName": {"text": "Costco Gas Station"},
                "formattedAddress": "123 Main St, Riverton, UT",
                "location": {"latitude": 40.50, "longitude": -112.0},
                "primaryType": "gas_station",
            },
        ]
        with patch.object(places.config, "HOME_LAT", 40.494), \
                patch.object(places.config, "HOME_LON", -112.0), \
                patch.object(places.config, "PLACES_LOCATION_RADIUS_M", 16093.44):
            nearby = places._matching_nearby("Costco", raw, "tire center")
        self.assertEqual([item[0]["id"] for item in nearby], ["tire"])

    def test_generic_walgreens_demotes_embedded_pharmacy(self):
        raw = [
            {
                "id": "pharmacy",
                "displayName": {"text": "Walgreens Pharmacy"},
                "formattedAddress": "1 Main St, Riverton, UT",
                "location": {"latitude": 40.50, "longitude": -112.0},
                "primaryType": "pharmacy",
            },
            {
                "id": "store",
                "displayName": {"text": "Walgreens"},
                "formattedAddress": "1 Main St, Riverton, UT",
                "location": {"latitude": 40.50, "longitude": -112.0},
                "primaryType": "drugstore",
            },
        ]
        with patch.object(places.config, "HOME_LAT", 40.494), \
                patch.object(places.config, "HOME_LON", -112.0), \
                patch.object(places.config, "PLACES_LOCATION_RADIUS_M", 16093.44):
            nearby = places._matching_nearby("Walgreens", raw)
        self.assertEqual([item[0]["id"] for item in nearby], ["store"])

    def test_standalone_service_brand_is_not_discarded(self):
        raw = [
            {
                "id": "near",
                "displayName": {"text": "Discount Tire"},
                "formattedAddress": "1 Main St, Riverton, UT",
                "location": {"latitude": 40.50, "longitude": -112.0},
                "primaryType": "tire_shop",
            },
            {
                "id": "far",
                "displayName": {"text": "Discount Tire"},
                "formattedAddress": "2 Main St, Riverton, UT",
                "location": {"latitude": 40.54, "longitude": -112.0},
                "primaryType": "tire_shop",
            },
        ]
        with patch.object(places.config, "HOME_LAT", 40.494), \
                patch.object(places.config, "HOME_LON", -112.0), \
                patch.object(places.config, "PLACES_LOCATION_RADIUS_M", 16093.44):
            nearby = places._matching_nearby("Discount Tire", raw)
        self.assertEqual([item[0]["id"] for item in nearby], ["near", "far"])

    def test_containing_place_parent_wins_same_site_cluster(self):
        raw = [
            {
                "id": "child",
                "displayName": {"text": "Walmart"},
                "formattedAddress": "1 Main St, Riverton, UT",
                "location": {"latitude": 40.50, "longitude": -112.0},
                "primaryType": "department_store",
                "containingPlaces": [{"id": "parent"}],
            },
            {
                "id": "parent",
                "displayName": {"text": "Walmart Supercenter"},
                "formattedAddress": "1 Main St, Riverton, UT",
                "location": {"latitude": 40.50, "longitude": -112.0},
                "primaryType": "department_store",
            },
        ]
        with patch.object(places.config, "HOME_LAT", 40.494), \
                patch.object(places.config, "HOME_LON", -112.0), \
                patch.object(places.config, "PLACES_LOCATION_RADIUS_M", 16093.44):
            nearby = places._matching_nearby("Walmart", raw)
        self.assertEqual([item[0]["id"] for item in nearby], ["parent"])


class PlacesIntentValidationTest(unittest.TestCase):
    def test_place_modifier_is_normalized(self):
        parsed = intent._validate({
            "intent": "business_hours",
            "query": "Costco",
            "place_modifier": " Tire Center ",
            "hours_when": "close",
        })
        self.assertEqual(parsed["query"], "Costco")
        self.assertEqual(parsed["place_modifier"], "tire center")


class PlacesHandleTest(unittest.IsolatedAsyncioTestCase):
    async def test_one_search_yields_multiple_sorted_map_results(self):
        raw = [
            {
                "id": "far",
                "displayName": {"text": "Chipotle Mexican Grill"},
                "formattedAddress": "2 Far St, Riverton, UT",
                "location": {"latitude": 40.54, "longitude": -112.0},
            },
            {
                "id": "near",
                "displayName": {"text": "Chipotle Mexican Grill"},
                "formattedAddress": "1 Near St, Riverton, UT",
                "location": {"latitude": 40.50, "longitude": -112.0},
            },
        ]
        with patch.object(places, "_search", AsyncMock(return_value=raw)) as search, \
                patch.object(places.config, "HOME_LAT", 40.494), \
                patch.object(places.config, "HOME_LON", -112.0), \
                patch.object(places.config, "PLACES_LOCATION_RADIUS_M", 16093.44):
            result = await places.handle({
                "intent": "place_search", "query": "Chipotle",
                "hours_when": None,
            })
        search.assert_awaited_once_with("Chipotle")
        self.assertEqual(
            [item["id"] for item in result["places_view"]["places"]],
            ["near", "far"],
        )
        self.assertIn("within 10 miles", result["response"])

    async def test_weak_spell_correction_falls_back(self):
        raw = [{
            "id": "labcorp",
            "displayName": {"text": "Labcorp"},
            "location": {"latitude": 40.5, "longitude": -112.0},
        }]
        with patch.object(places, "_search", AsyncMock(return_value=raw)), \
                patch.object(places.config, "HOME_LAT", 40.494), \
                patch.object(places.config, "HOME_LON", -112.0):
            result = await places.handle({
                "intent": "place_search", "query": "blorbcorp",
                "hours_when": None,
            })
        self.assertIsNone(result)
        self.assertLess(
            places.fuzz.WRatio(places._normalized("blorbcorp"),
                               places._normalized("Labcorp")),
            places._MATCH_THRESHOLD)

    async def test_explicit_modifier_uses_base_query_and_no_extra_search(self):
        raw = [{
            "id": "gas",
            "displayName": {"text": "Costco Gas Station"},
            "formattedAddress": "1 Main St, Riverton, UT",
            "location": {"latitude": 40.50, "longitude": -112.0},
            "primaryType": "gas_station",
        }]
        with patch.object(places, "_search", AsyncMock(return_value=raw)) as search, \
                patch.object(places.config, "HOME_LAT", 40.494), \
                patch.object(places.config, "HOME_LON", -112.0), \
                patch.object(places.config, "PLACES_LOCATION_RADIUS_M", 16093.44):
            result = await places.handle({
                "intent": "place_search", "query": "Costco",
                "place_modifier": "gas", "hours_when": None,
            })
        search.assert_awaited_once_with("Costco")
        self.assertEqual(result["places_view"]["query"], "Costco gas")
        self.assertEqual(result["places_view"]["places"][0]["id"], "gas")

    async def test_explicit_modifier_never_substitutes_parent_store(self):
        raw = [{
            "id": "warehouse",
            "displayName": {"text": "Costco Wholesale"},
            "formattedAddress": "1 Main St, Riverton, UT",
            "location": {"latitude": 40.50, "longitude": -112.0},
            "primaryType": "warehouse_store",
        }]
        with patch.object(places, "_search", AsyncMock(return_value=raw)), \
                patch.object(places.config, "HOME_LAT", 40.494), \
                patch.object(places.config, "HOME_LON", -112.0), \
                patch.object(places.config, "PLACES_LOCATION_RADIUS_M", 16093.44):
            result = await places.handle({
                "intent": "business_hours", "query": "Costco",
                "place_modifier": "pharmacy", "hours_when": "close",
            })
        self.assertIsNone(result)


if __name__ == "__main__":
    unittest.main()
