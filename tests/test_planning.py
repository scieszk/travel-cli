import contextlib
from datetime import timedelta
import io
import json
from pathlib import Path
import sys
import unittest
import urllib.parse
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import travel


def forecast(days=16, missing=False):
    start = travel.datetime.now(travel.CHINA_TZ).replace(hour=0, minute=0, second=0, microsecond=0)
    times = [start + timedelta(hours=i) for i in range(days * 24)]
    dates = [(start + timedelta(days=i)).date().isoformat() for i in range(days)]
    return {"elevation": 100, "daily": {"time": dates, "sunrise": [d + "T06:30" for d in dates], "sunset": [d + "T18:00" for d in dates]}, "hourly": {
        "time": [t.strftime("%Y-%m-%dT%H:%M") for t in times],
        "temperature_2m": [10] * len(times),
        "weather_code": [73 if t.hour >= 10 else 0 for t in times],
        "precipitation_probability": [80 if t.hour >= 10 else 0 for t in times],
        "snowfall": [None if missing else (1 if t.hour >= 10 else 0) for t in times],
    }}


POINTS = {name: {"lng": 120 + index * 0.5, "lat": 30, "elev": 100}
          for index, name in enumerate(("A", "B", "C"))}


class PlanningTests(unittest.TestCase):
    def test_weather_request_converts_map_coordinates_to_wgs84(self):
        with patch.object(travel, "http_json", return_value={"daily": {}}) as request:
            travel.om_forecast(39.916527, 116.397128)
        query = urllib.parse.parse_qs(urllib.parse.urlsplit(request.call_args.args[0]).query)
        self.assertAlmostEqual(float(query["longitude"][0]), 116.39088350597522, places=8)
        self.assertAlmostEqual(float(query["latitude"][0]), 39.91512325075406, places=8)

    def test_wgs84_waypoints_are_converted_for_amap(self):
        point = {"Sample": {"lng": 116.397128, "lat": 39.916527, "elev": None, "coord_system": "wgs84"}}
        _, lng, lat, _ = travel.resolve("Sample", point)
        self.assertAlmostEqual(lng, 116.40337249402477, places=8)
        self.assertAlmostEqual(lat, 39.91793074924595, places=8)

    def test_route_sampling_interpolates_long_segments(self):
        points = travel.sample_route([(0, 0), (1, 0)], 40)
        self.assertEqual(len(points), 4)
        self.assertAlmostEqual(points[1][2], 40)
        self.assertAlmostEqual(points[2][2], 80)
        self.assertEqual(points[-1][:2], (1, 0))

    def invoke(self, args):
        output, errors = io.StringIO(), io.StringIO()
        status = 0
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors), patch.object(sys, "argv", ["travel", *args]):
            try:
                travel.main()
            except SystemExit as error:
                status = error.code
        return status, output.getvalue(), errors.getvalue()

    def test_points_structured_has_contract_and_no_terminal_table(self):
        with patch.object(travel, "load_points", return_value=POINTS):
            status, stdout, stderr = self.invoke(["points", "--structured"])
        self.assertEqual(status, 0, stderr)
        result = json.loads(stdout)
        self.assertEqual(result["schema_version"], "1.0")
        self.assertTrue(result["ok"])
        self.assertEqual(result["command"], "points")
        self.assertEqual(len(result["data"]["points"]), 3)
        self.assertIn("queried_at", result)
        self.assertEqual(stderr, "")

    def test_invalid_options_return_structured_errors_before_http(self):
        for extra in [["--date", "2026-02-30"], ["--date", "2000-01-01"], ["--depart", "7:00"], ["--stop", "B=-1"], ["--stop", "B=30"], ["--speed", "nan"]]:
            with self.subTest(extra=extra), patch.object(travel, "http_json") as request:
                status, stdout, stderr = self.invoke(["route", "A", "C", "--structured", *extra])
            self.assertNotEqual(status, 0)
            result = json.loads(stdout)
            self.assertFalse(result["ok"])
            self.assertEqual(result["error"]["code"], "INVALID_ARGUMENT")
            self.assertEqual(stderr, "")
            request.assert_not_called()

    def test_stops_and_each_leg_duration_shift_downstream_arrival(self):
        day = (travel.datetime.now(travel.CHINA_TZ) + timedelta(days=1)).date().isoformat()
        legs = [([(120, 30), (120.5, 30)], 60, 1), ([(120.5, 30), (121, 30)], 60, 2)]
        with patch.object(travel, "load_points", return_value=POINTS), patch.object(travel, "amap_route", side_effect=legs) as route, patch.object(travel, "om_forecast", return_value=forecast()):
            status, stdout, stderr = self.invoke(["route", "A", "C", "--via", "B", "--stop", "B=45", "--date", day, "--depart", "08:00", "--structured"])
        self.assertEqual(status, 0, stderr + stdout)
        data = json.loads(stdout)["data"]
        self.assertEqual(data["arrival_at"], day + "T11:45:00+08:00")
        self.assertEqual(data["stop_minutes"], 45)
        self.assertEqual(data["distance_km"], 120)
        self.assertEqual(route.call_count, 2)
        stop = next(sample for sample in data["samples"] if sample["label"] == "B")
        self.assertEqual(stop["arrival_at"], day + "T09:00:00+08:00")
        self.assertEqual(stop["departure_at"], day + "T09:45:00+08:00")

    def test_compare_reuses_requests_and_reports_earlier_weather_window(self):
        day = (travel.datetime.now(travel.CHINA_TZ) + timedelta(days=1)).date().isoformat()
        with patch.object(travel, "load_points", return_value=POINTS), patch.object(travel, "amap_route", return_value=([(120, 30), (121, 30)], 120, 2)) as route, patch.object(travel, "om_forecast", return_value=forecast()) as weather:
            status, stdout, stderr = self.invoke(["compare", "A", "C", "--date", day, "--departures", "07:00", "10:00", "--structured"])
        self.assertEqual(status, 0, stderr + stdout)
        data = json.loads(stdout)["data"]
        self.assertEqual(len(data["plans"]), 2)
        self.assertEqual(data["preferred_departures"], [day + "T07:00:00+08:00"])
        self.assertEqual(route.call_count, 1)
        self.assertEqual(weather.call_count, len(data["plans"][0]["samples"]))

    def test_unknown_weather_prevents_automatic_preference(self):
        with patch.object(travel, "load_points", return_value=POINTS), patch.object(travel, "amap_route", return_value=([(120, 30), (121, 30)], 120, 2)), patch.object(travel, "om_forecast", return_value=forecast(missing=True)):
            status, stdout, stderr = self.invoke(["compare", "A", "C", "--departures", "07:00", "10:00", "--json"])
        self.assertEqual(status, 0, stderr + stdout)
        data = json.loads(stdout)["data"]
        self.assertEqual(data["preferred_departures"], [])
        self.assertFalse(data["comparison_complete"])

    def test_weather_failure_remains_visible_in_route_results(self):
        with patch.object(travel, "load_points", return_value=POINTS), patch.object(travel, "amap_route", return_value=([(120, 30), (121, 30)], 120, 2)), patch.object(travel, "om_forecast", side_effect=SystemExit("temporary upstream failure")):
            status, stdout, stderr = self.invoke(["route", "A", "C", "--structured"])
        self.assertEqual(status, 0, stderr + stdout)
        data = json.loads(stdout)["data"]
        self.assertFalse(data["complete"])
        self.assertTrue(all(row["weather_status"] == "unavailable" for row in data["samples"]))
        self.assertTrue(all(row["temperature_c"] is None for row in data["samples"]))

    def test_excessive_departure_choices_are_rejected(self):
        status, stdout, _ = self.invoke(["compare", "A", "B", "--departures", *[f"{i:02}:00" for i in range(7)], "--structured"])
        self.assertNotEqual(status, 0)
        self.assertEqual(json.loads(stdout)["error"]["code"], "INVALID_ARGUMENT")


if __name__ == "__main__":
    unittest.main()
