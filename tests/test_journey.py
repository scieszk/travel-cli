import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import MagicMock
from unittest.mock import patch

import test_planning
import travel


POINTS, forecast = test_planning.POINTS, test_planning.forecast


class JourneyTests(unittest.TestCase):
    invoke = test_planning.PlanningTests.invoke
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.env = patch.dict(os.environ, {"TRAVEL_PREFERENCES": str(Path(self.tmp.name) / "preferences.json")})
        self.env.start()
        self.addCleanup(self.env.stop)

    def test_builtin_addresses_can_be_listed_without_network(self):
        with patch.object(travel, "http_json") as request:
            status, stdout, _ = self.invoke(["points", "--sights", "--structured"])
        self.assertEqual(status, 0, stdout)
        self.assertGreaterEqual(len(json.loads(stdout)["data"]["sights"]), 4)
        request.assert_not_called()

    def test_night_preference_and_explicit_override(self):
        status, stdout, _ = self.invoke(["preferences", "set", "avoid_night_driving=true", "--structured"])
        self.assertEqual(status, 0, stdout)
        data = forecast()
        dates = sorted({v[:10] for v in data["hourly"]["time"]})
        data["daily"] = {"time": dates, "sunrise": [d + "T06:30" for d in dates], "sunset": [d + "T18:00" for d in dates]}
        with patch.object(travel, "load_points", return_value=POINTS), patch.object(travel, "amap_route", return_value=([(120, 30), (121, 30)], 60, 1)), patch.object(travel, "om_forecast", return_value=data):
            status, stdout, _ = self.invoke(["route", "A", "C", "--depart", "19:00", "--structured"])
            self.assertEqual(status, 0, stdout)
            self.assertFalse(json.loads(stdout)["data"]["meets_preferences"])
            status, stdout, _ = self.invoke(["route", "A", "C", "--depart", "19:00", "--allow-night-driving", "--structured"])
            self.assertTrue(json.loads(stdout)["data"]["meets_preferences"])

    def test_fractional_minute_preference_rejected_without_file(self):
        status, stdout, _ = self.invoke(["preferences", "set", "visit_minutes=60.5", "--structured"])
        self.assertNotEqual(status, 0)
        self.assertFalse(json.loads(stdout)["ok"])
        self.assertFalse((Path(self.tmp.name) / "preferences.json").exists())

    def test_outing_counts_both_drives_visit_and_buffer(self):
        poi = {"id": "P", "name": "公园", "longitude": 120.5, "latitude": 30, "category": "nature", "address": "示例路", "route_km": 0}
        def route(a, b, c, d, **kw):
            return [(a, b), (c, d)], 40, 1
        with patch.object(travel, "load_points", return_value=POINTS), patch.object(travel, "amap_route", side_effect=route), patch.object(travel, "om_forecast", return_value=forecast()), patch("journey.discover_sights", return_value={"sights": [poi], "complete": True}):
            status, stdout, _ = self.invoke(["outing", "A", "--hours", "3", "--structured"])
            self.assertEqual(status, 0, stdout)
            self.assertEqual(json.loads(stdout)["data"]["plans"], [])
            status, stdout, _ = self.invoke(["outing", "A", "--hours", "4", "--structured"])
            plan = json.loads(stdout)["data"]["plans"][0]
            self.assertEqual(plan["duration_minutes"], 180)
            self.assertEqual(plan["budget_used_minutes"], 210)

    def test_sight_detours_keep_original_via(self):
        poi = {"id": "P", "name": "公园", "longitude": 120.7, "latitude": 30, "category": "nature", "route_km": 80}
        calls = []
        def route(a, b, c, d, **kw):
            calls.append(kw.get("waypoints", ""))
            return [(a, b), (c, d)], 140 if kw else 60, 2.2 if kw else 1
        with patch.object(travel, "load_points", return_value=POINTS), patch.object(travel, "amap_route", side_effect=route), patch.object(travel, "om_forecast", return_value=forecast()), patch("journey.discover_sights", return_value={"sights": [poi], "complete": True}):
            status, stdout, _ = self.invoke(["route", "A", "C", "--via", "B", "--sights", "--structured"])
        self.assertEqual(status, 0, stdout)
        self.assertIn("120.5,30", calls[-1])
        self.assertIn("120.700000,30.000000", calls[-1])
        self.assertAlmostEqual(json.loads(stdout)["data"]["sights"]["sights"][0]["extra_driving_minutes"], 12)

    def test_card_conflicts_with_json_before_requests(self):
        with patch.object(travel, "http_json") as request:
            status, stdout, _ = self.invoke(["route", "A", "C", "--card", "--structured"])
        self.assertNotEqual(status, 0)
        self.assertEqual(json.loads(stdout)["error"]["code"], "INVALID_ARGUMENT")
        request.assert_not_called()

    def test_overnight_stop_checks_resumed_driving(self):
        with patch.object(travel, "load_points", return_value=POINTS), patch.object(travel, "amap_route", side_effect=[([(120, 30), (120.5, 30)], 10, 1), ([(120.5, 30), (121, 30)], 10, 1)]), patch.object(travel, "om_forecast", return_value=forecast()):
            status, stdout, _ = self.invoke(["route", "A", "C", "--via", "B", "--stop", "B=720", "--depart", "16:00", "--avoid-night-driving", "--structured"])
        self.assertEqual(status, 0, stdout)
        data = json.loads(stdout)["data"]
        self.assertFalse(data["meets_preferences"])
        stop = next(row for row in data["samples"] if row["label"] == "B")
        self.assertFalse(stop["departure_daylight"])

    def test_late_arrival_requests_next_day_hour(self):
        def get_weather(*args, days=1, **kwargs):
            return forecast(days=days)
        with patch.object(travel, "load_points", return_value=POINTS), patch.object(travel, "amap_route", return_value=([(120, 30), (121, 30)], 10, 1 / 6)), patch.object(travel, "om_forecast", side_effect=get_weather) as weather:
            status, stdout, _ = self.invoke(["route", "A", "C", "--depart", "23:40", "--structured"])
        self.assertEqual(status, 0, stdout)
        self.assertEqual(weather.call_args.kwargs["days"], 2)

    def test_malformed_geocode_is_one_structured_error(self):
        with patch.object(travel, "load_points", return_value={}), patch.object(travel, "get_amap_key", return_value="synthetic"), patch.object(travel, "http_json", return_value={"status": "1", "geocodes": [{"location": []}]}):
            status, stdout, stderr = self.invoke(["weather", "Example", "--structured"])
        self.assertNotEqual(status, 0)
        self.assertFalse(json.loads(stdout)["ok"])
        self.assertEqual(stderr, "")

    def test_amap_requests_are_paced_and_qps_response_retried(self):
        limited = {"status": "0", "infocode": "10021"}
        good = {"status": "1", "infocode": "10000"}
        responses = []
        for value in (limited, good):
            response = MagicMock()
            response.__enter__.return_value.read.return_value = json.dumps(value).encode()
            responses.append(response)
        with patch.object(travel.urllib.request, "urlopen", side_effect=responses) as request, patch.object(travel.time, "monotonic", return_value=100), patch.object(travel.time, "sleep") as sleep, patch.object(travel, "_AMAP_LAST_REQUEST", 100, create=True):
            result = travel.http_json("https://restapi.amap.com/v3/direction/driving?key=synthetic")
        self.assertEqual(result, good)
        self.assertEqual(request.call_count, 2)
        self.assertTrue(sleep.called)

    def test_single_attempt_scenic_request_does_not_retry_qps_error(self):
        response = MagicMock()
        response.__enter__.return_value.read.return_value = b'{"status":"0","infocode":"10021"}'
        with patch.object(travel.urllib.request, "urlopen", return_value=response) as request, patch.object(travel.time, "sleep"), self.assertRaises(travel.ToolError) as error:
            travel.http_json("https://restapi.amap.com/v3/place/around?key=synthetic", retry=1)
        self.assertEqual(error.exception.error_code, "UPSTREAM_RATE_LIMIT")
        self.assertEqual(request.call_count, 1)

    def test_saved_watch_reuses_coordinates_and_deduplicates(self):
        path = Path(self.tmp.name) / "trip.watch.json"
        with patch.object(travel, "load_points", return_value=POINTS), patch.object(travel, "amap_route", return_value=([(120, 30), (121, 30)], 120, 2)), patch.object(travel, "om_forecast", return_value=forecast()):
            status, stdout, _ = self.invoke(["route", "A", "C", "--save-watch", str(path), "--structured"])
        self.assertEqual(status, 0, stdout)
        with patch.object(travel, "load_points", return_value={}), patch.object(travel, "amap_route", return_value=([(120, 30), (121, 30)], 120, 3)), patch.object(travel, "om_forecast", return_value=forecast()):
            status, stdout, _ = self.invoke(["watch", "check", str(path), "--structured"])
            self.assertEqual(status, 0, stdout)
            first = json.loads(stdout)["data"]
            status, stdout, _ = self.invoke(["watch", "check", str(path), "--structured"])
            second = json.loads(stdout)["data"]
        self.assertEqual(first["state"], "changed")
        self.assertTrue(first["notify"])
        self.assertFalse(second["notify"])
