import argparse
import contextlib
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
import urllib.error
import urllib.parse

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import travel


class TravelTests(unittest.TestCase):
    def test_network_failure_does_not_expose_credentials(self):
        marker = "unit-test-map-credential"
        url = f"https://example.com/api?key={marker}&address=private-place"
        with patch.object(travel.urllib.request, "urlopen", side_effect=urllib.error.URLError(url)), patch.object(travel.time, "sleep"):
            with self.assertRaises(SystemExit) as error:
                travel.http_json(url, retry=1)
        message = str(error.exception)
        self.assertNotIn(marker, message)
        self.assertNotIn("private-place", message)
        self.assertIn("example.com", message)

    def test_zero_elevation_is_not_dropped(self):
        with patch.object(travel, "http_json", return_value={"daily": {}}) as request:
            travel.om_forecast(30, 120, elev=0)
        query = urllib.parse.parse_qs(urllib.parse.urlsplit(request.call_args.args[0]).query)
        self.assertEqual(query.get("elevation"), ["0"])

    def test_short_route_keeps_destination(self):
        result = travel.sample_route([(0, 0), (0.01, 0)], step_km=40)
        self.assertEqual(result[-1][:2], (0.01, 0))
        self.assertGreater(result[-1][2], 0)

    def test_missing_snow_or_hazardous_weather_never_gets_green(self):
        self.assertEqual(travel.risk_lamp(0, 10, 20, 0, None), "⚪")
        for code in [65, 71, 73, 75, 77, 82, 85, 86, 95, 96, 99]:
            self.assertNotEqual(travel.risk_lamp(code, 10, 20, 0, None), "🟢")

    def test_nearby_route_destination_is_printed_even_with_same_nearest_label(self):
        args = argparse.Namespace(origin="Start", dest="Finish", via=None, speed=60, depart="08:00", step=40)
        day = travel.datetime.now(travel.CHINA_TZ).date().isoformat()
        forecast = {"hourly": {"time": [f"{day}T08:00", f"{day}T09:00"], "weather_code": [0, 0], "temperature_2m": [10, 10], "precipitation_probability": [0, 0]}}
        output = io.StringIO()
        with patch.object(travel, "load_points", return_value={}), patch.object(travel, "resolve", side_effect=[("Start", 120, 30, None), ("Finish", 120.01, 30, None)]), patch.object(travel, "amap_route", return_value=([(120, 30), (120.01, 30)], 1, 0.02)), patch.object(travel, "nearest_point", return_value=("Nearby", None)), patch.object(travel, "om_forecast", return_value=forecast) as request, patch.object(travel.time, "sleep"), contextlib.redirect_stdout(output):
            travel.cmd_route(args)
        self.assertEqual(request.call_count, 2)
        rows = [line for line in output.getvalue().splitlines() if line.startswith("08:")]
        self.assertEqual(len(rows), 2)
        self.assertIn("Finish", rows[-1])
        self.assertIn("⚪", rows[-1])

    def test_missing_forecast_values_are_printed_as_unknown(self):
        daily = {key: [None] for key in ["weather_code", "temperature_2m_max", "temperature_2m_min", "precipitation_probability_max", "precipitation_sum", "snowfall_sum"]}
        daily["time"] = ["2026-10-18"]
        output = io.StringIO()
        with patch.object(travel, "load_points", return_value={}), patch.object(travel, "resolve", return_value=("Sample", 120, 30, None)), patch.object(travel, "om_forecast", return_value={"daily": daily}), contextlib.redirect_stdout(output):
            travel.cmd_weather(argparse.Namespace(place="Sample", days=1))
        self.assertIn("未知", output.getvalue())

    def test_invalid_numeric_options_fail_before_network(self):
        for args in [["route", "A", "B", "--step", "0"], ["route", "A", "B", "--speed", "-1"], ["weather", "A", "--days", "0"], ["weather", "A", "--days", "17"]]:
            with self.subTest(args=args), patch.object(sys, "argv", ["travel", *args]), patch.object(travel, "cmd_route") as route, patch.object(travel, "cmd_weather") as weather, contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit) as error:
                    travel.main()
                self.assertEqual(error.exception.code, 2)
                route.assert_not_called()
                weather.assert_not_called()

    def test_local_key_file_is_used_without_embedding_key(self):
        with tempfile.TemporaryDirectory() as folder:
            key_file = Path(folder) / "map.key"
            key_file.write_text("unit-test-map-credential\n")
            with patch.dict(os.environ, {"AMAP_KEY": "", "AMAP_KEY_FILE": str(key_file)}), patch.object(travel, "http_json", return_value={"status": "1", "geocodes": [{"location": "120,30"}]}) as request:
                travel.resolve("Sample", {})
            query = urllib.parse.parse_qs(urllib.parse.urlsplit(request.call_args.args[0]).query)
            self.assertEqual(query["key"], ["unit-test-map-credential"])

    def test_missing_key_fails_before_request(self):
        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ, {"AMAP_KEY": "", "AMAP_KEY_FILE": str(Path(folder) / "missing.key")}), patch.object(travel, "http_json") as request:
            with self.assertRaisesRegex(SystemExit, "AMAP_KEY"):
                travel.amap_route(120, 30, 121, 31)
            request.assert_not_called()

    def test_missing_personal_points_uses_example(self):
        with tempfile.TemporaryDirectory() as folder:
            example = Path(folder) / "waypoints.example.json"
            example.write_text(json.dumps({"Sample": {"lng": 120, "lat": 30, "elev": 0}}))
            with patch.object(travel, "BASE", folder), patch.dict(os.environ, {"TRAVEL_WAYPOINTS": ""}):
                self.assertIn("Sample", travel.load_points())

    def test_route_rejects_forecast_outside_arrival_window(self):
        args = argparse.Namespace(origin="A", dest="B", via=None, speed=60, depart="08:00", step=40)
        forecast = {"hourly": {"time": ["2000-01-01T00:00"], "weather_code": [0], "temperature_2m": [10], "precipitation_probability": [0]}}
        with patch.object(travel, "load_points", return_value={}), patch.object(travel, "resolve", return_value=("Sample", 120, 30, None)), patch.object(travel, "amap_route", return_value=([(120, 30), (120.5, 30)], 60, 1)), patch.object(travel, "om_forecast", return_value=forecast), contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaisesRegex(SystemExit, "范围"):
                travel.cmd_route(args)


if __name__ == "__main__":
    unittest.main()
