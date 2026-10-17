import argparse
import contextlib
from datetime import datetime
import io
import json
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import patch
import urllib.parse

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import journey
import travel


class ImageIntegrationTests(unittest.TestCase):
    def forecast(self):
        return {"daily": {"time": ["2026-10-18", "2026-10-19"], "weather_code": [61, 0]},
                "current": {"time": "2026-10-18T10:00", "temperature_2m": 25,
                            "apparent_temperature": None, "weather_code": 3},
                "hourly": {"time": ["2026-10-18T09:00", "2026-10-18T10:00", "2026-10-18T11:00"],
                           "temperature_2m": [24, 25, 26], "precipitation_probability": [80, None, 60]}}

    def test_image_requests_current_and_future_hours_but_keeps_requested_days(self):
        renderer = types.SimpleNamespace(render_card=lambda *a, **kw: {"path": "weather.png"})
        args = argparse.Namespace(place="Sample", days=1, image="weather.png", structured=True)
        out = io.StringIO()
        with patch.object(travel, "load_points", return_value={}), patch.object(travel, "resolve", return_value=("Sample", 120, 30, None)), patch.object(travel, "om_forecast", return_value=self.forecast()) as request, patch.object(travel, "datetime") as clock, patch.dict(sys.modules, {"image_cards": renderer}), contextlib.redirect_stdout(out):
            clock.now.return_value = datetime(2026, 10, 18, 10, 12, tzinfo=travel.CHINA_TZ)
            travel.cmd_weather(args)
        self.assertTrue(request.call_args.kwargs["hourly"])
        self.assertTrue(request.call_args.kwargs["current"])
        self.assertGreaterEqual(request.call_args.kwargs["days"], 2)
        data = json.loads(out.getvalue())["data"]
        self.assertEqual(len(data["days"]), 1)
        self.assertEqual(data["current"]["weather"], "阴")
        self.assertIsNone(data["current"]["apparent_temperature_c"])
        self.assertEqual([h["time"] for h in data["hourly"]], ["2026-10-18T10:00", "2026-10-18T11:00"])
        self.assertIsNone(data["hourly"][0]["precipitation_probability_pct"])

    def test_current_request_fields_are_explicit(self):
        with patch.object(travel, "http_json", return_value={"daily": {}, "hourly": {}}) as request:
            travel.om_forecast(30, 120, hourly=True, current=True)
        query = urllib.parse.parse_qs(urllib.parse.urlsplit(request.call_args.args[0]).query)
        self.assertIn("apparent_temperature", query["current"][0])

    def test_missing_optional_hourly_does_not_discard_daily_weather(self):
        args = argparse.Namespace(place="Sample", days=1, image=None, details=True, structured=True)
        daily_only = {"daily": {"time": ["2026-10-18"], "weather_code": [0]}}
        with patch.object(travel, "load_points", return_value={}), patch.object(travel, "resolve", return_value=("Sample", 120, 30, None)), patch.object(travel, "http_json", return_value=daily_only), contextlib.redirect_stdout(io.StringIO()):
            data = travel.cmd_weather(args)
        self.assertEqual(data["days"][0]["weather"], "晴")
        self.assertEqual(data["hourly"], [])
        self.assertIsNone(data["current"]["temperature_c"])

    def test_renderer_failure_keeps_weather_and_single_json_without_exception_details(self):
        def fail(*a, **kw):
            raise OSError("sensitive-runtime-details")
        args = argparse.Namespace(place="Sample", days=1, image="weather.png", structured=True)
        out = io.StringIO()
        with patch.object(travel, "load_points", return_value={}), patch.object(travel, "resolve", return_value=("Sample", 120, 30, None)), patch.object(travel, "om_forecast", return_value=self.forecast()), patch.dict(sys.modules, {"image_cards": types.SimpleNamespace(render_card=fail)}), contextlib.redirect_stdout(out):
            travel.cmd_weather(args)
        result = json.loads(out.getvalue())
        self.assertTrue(result["ok"])
        self.assertTrue(result["data"]["days"])
        self.assertEqual(result["data"]["image_error"]["code"], "IMAGE_UNAVAILABLE")
        self.assertNotIn("sensitive-runtime-details", out.getvalue())

    def test_selected_plan_is_rendered_without_mutating_source(self):
        captured = []
        def render(kind, data, path, **kw):
            captured.append((kind, data))
            return {"path": path}
        args = argparse.Namespace(card=False, image="trip.png", save_watch=None, select=2, structured=True)
        data = {"plans": [{"origin": "First"}, {"origin": "Second"}]}
        with patch.dict(sys.modules, {"image_cards": types.SimpleNamespace(render_card=render)}), contextlib.redirect_stdout(io.StringIO()):
            journey.finish(travel, "compare", data, args)
        self.assertEqual(captured[0][0], "route")
        self.assertEqual(captured[0][1]["origin"], "Second")
        self.assertNotIn("image", data["plans"][1])
        self.assertEqual(data["image"]["path"], "trip.png")

    def test_unavailable_selection_is_rejected_before_rendering(self):
        args = argparse.Namespace(card=False, image="trip.png", save_watch=None, select=2, structured=True)
        with self.assertRaisesRegex(SystemExit, "序号"):
            journey.finish(travel, "compare", {"plans": []}, args)


if __name__ == "__main__":
    unittest.main()
