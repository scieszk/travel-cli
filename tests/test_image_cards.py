import builtins
import importlib.util
import os
from pathlib import Path
import stat
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def sample_weather():
    return {"place": "杭州", "current": {"temperature_c": 21.4, "apparent_temperature_c": 22,
            "weather_code": 3, "time": "2026-10-18T09:15"},
            "days": [{"date": "2026-10-18", "weather_code": 3, "weather": "阴",
                      "temperature_min_c": 17, "temperature_max_c": 24,
                      "precipitation_probability_pct": 70}],
            "hourly": [{"time": f"2026-10-18T{hour:02}:00", "temperature_c": 20 + hour / 10,
                        "precipitation_probability_pct": (hour - 10) * 10, "weather_code": 3}
                       for hour in range(10, 18)]}


def sample_route():
    return {"origin": "杭州", "destination": "湖州", "distance_km": 98.6, "duration_minutes": 125,
            "departure_at": "2026-10-18T09:00:00+08:00", "arrival_at": "2026-10-18T11:05:00+08:00",
            "stop_minutes": 20, "warning_counts": {"red": 1, "yellow": 0, "unknown": 1},
            "samples": [{"label": name, "arrival_at": at, "stop_minutes": stop, "weather_code": code,
                         "weather": weather, "temperature_c": temp, "precipitation_probability_pct": rain,
                         "weather_status": status, "daylight": daylight}
                        for name, at, stop, code, weather, temp, rain, status, daylight in [
                            ("杭州", "2026-10-18T09:00:00+08:00", 0, 3, "阴", 21, 20, "available", True),
                            ("德清", "2026-10-18T09:50:00+08:00", 20, 65, "大雨", 19, 90, "available", True),
                            ("湖州", "2026-10-18T11:05:00+08:00", 0, None, "未知", None, None, "unavailable", None)]]}


def sample_rail():
    return {"outbound": {"code": "G100", "fromStation": "杭州东", "toStation": "上海虹桥",
                         "startTime": "08:00", "arriveTime": "09:00", "duration": "01:00",
                         "canBuy": True, "seats": {"二等座": "有", "一等座": "0"}},
            "return": {"code": "G101", "fromStation": "上海虹桥", "toStation": "杭州东",
                       "startTime": "18:00", "arriveTime": "19:05", "duration": "01:05",
                       "canBuy": True, "seats": {"二等座": "5"}},
            "outboundDate": "2026-10-18", "returnDate": "2026-10-18",
            "outboundDeparture": "2026-10-18T08:00:00+08:00", "destinationArrival": "2026-10-18T09:00:00+08:00",
            "returnDeparture": "2026-10-18T18:00:00+08:00", "homeArrival": "2026-10-18T19:05:00+08:00",
            "stayMin": 540, "totalTravelMin": 125, "totalElapsedMin": 665}


class ImageCardsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)

    def module(self):
        spec = importlib.util.find_spec("image_cards")
        self.assertIsNotNone(spec, "The image_cards renderer has not been implemented")
        import image_cards
        return image_cards

    def pillow(self):
        try:
            from PIL import Image, ImageFont
        except ImportError:
            self.skipTest("Optional Pillow dependency is not installed")
        return Image, ImageFont

    def render(self, kind, data, **kwargs):
        Image, ImageFont = self.pillow()
        try:
            self.module()._find_fonts(ImageFont, kwargs.get("font_path"))
        except RuntimeError:
            self.skipTest("No installed CJK font for image rendering")
        result = self.module().render_card(kind, data, Path(self.temp.name) / f"{kind}.png",
                                          generated_at="2026-10-18T09:30:00+08:00", **kwargs)
        with Image.open(result["path"]) as image:
            self.assertEqual(image.format, "PNG")
            self.assertEqual(image.width, 1080)
            self.assertGreaterEqual(image.height, 700)
            self.assertLessEqual(image.height, 3200)
            self.assertEqual((result["width"], result["height"]), image.size)
            image.verify()
        with Image.open(result["path"]) as image:
            description = image.info["Description"]
        self.assertTrue(result["caption"])
        self.assertTrue(Path(result["path"]).is_absolute())
        self.assertIn("2026-10-18 09:30", description)
        return result, description

    def test_weather_contains_current_forecast_time_and_zero_rain(self):
        _, description = self.render("weather", sample_weather())
        self.assertIn("21.4", description)
        self.assertIn("0%", description)
        self.assertIn("当前预报", description)
        self.assertIn("数据时间 2026-10-18 09:15", description)
        self.assertIn("Open-Meteo", description)
        self.assertIn("CC BY 4.0", description)

    def test_weather_without_current_is_explicitly_daily_forecast(self):
        data = sample_weather()
        del data["current"]
        _, description = self.render("weather", data)
        self.assertIn("当日预报", description)
        self.assertNotIn("实时", description)

    def test_missing_and_nonfinite_metrics_are_unknown_not_zero(self):
        data = {"place": "测试地点", "current": {"temperature_c": float("nan"),
                "apparent_temperature_c": float("inf"), "weather_code": None}, "days": []}
        _, description = self.render("weather", data)
        self.assertIn("未知", description)
        self.assertNotIn("nan", description)
        self.assertNotIn("inf", description)
        self.assertNotIn("0%", description)

    def test_route_preserves_order_and_reports_incomplete_forecasts(self):
        _, description = self.render("route", sample_route())
        timeline = description[description.index("沿途时间线"):]
        self.assertLess(timeline.index("杭州"), timeline.index("德清"))
        self.assertLess(timeline.index("德清"), timeline.index("湖州"))
        self.assertIn("数据不完整", description)
        self.assertIn("20 分钟", description)
        self.assertIn("高德地图", description)

    def test_overnight_route_timeline_preserves_arrival_dates(self):
        data = sample_route()
        data["departure_at"] = "2026-10-18T23:00:00+08:00"
        data["arrival_at"] = "2026-10-19T01:05:00+08:00"
        arrivals = ["2026-10-18T23:00:00+08:00", "2026-10-19T00:10:00+08:00", "2026-10-19T01:05:00+08:00"]
        for row, arrival in zip(data["samples"], arrivals):
            row["arrival_at"] = arrival
        _, description = self.render("route", data)
        timeline = description[description.index("沿途时间线"):]
        for value in ("10-18 23:00", "10-19 00:10", "10-19 01:05"):
            self.assertIn(value, timeline)

    def test_rail_displays_both_legs_stay_and_ticket_caveat(self):
        result, description = self.render("rail", sample_rail())
        self.assertIn("往返", result["caption"])
        for value in ("G100", "G101", "9 小时", "去程", "返程", "不是车票", "12306"):
            self.assertIn(value, description)

    def test_weather_advice_requires_known_values_and_zero_is_not_missing(self):
        _, unknown = self.render("weather", {"place": "测试", "days": []})
        self.assertIn("天气数据不足", unknown)
        self.assertNotIn("出门带伞", unknown)
        self.assertNotIn("适当添衣", unknown)
        _, dry = self.render("weather", {"place": "测试", "days": [{"weather_code": 0,
                            "precipitation_probability_pct": 0, "temperature_min_c": 15, "temperature_max_c": 22}]})
        self.assertIn("0%", dry)
        self.assertNotIn("天气数据不足", dry)
        self.assertNotIn("出门带伞", dry)
        _, rainy = self.render("weather", sample_weather())
        self.assertIn("出门带伞", rainy)

    def test_unknown_query_time_is_not_replaced_with_render_time(self):
        Image, ImageFont = self.pillow()
        try:
            self.module()._find_fonts(ImageFont, None)
        except RuntimeError:
            self.skipTest("No installed CJK font for image rendering")
        result = self.module().render_card("weather", {}, Path(self.temp.name) / "unknown.png")
        with Image.open(result["path"]) as image:
            description = image.info["Description"]
        self.assertIn("查询时间 未提供", description)
        self.assertIn("制图时间", description)

    def test_invalid_weather_scalars_are_unknown_not_crashes(self):
        _, description = self.render("weather", {"place": "测试", "current": {"weather_code": []},
                "days": [{"weather_code": True, "precipitation_probability_pct": 105}]})
        self.assertIn("天气未知", description)
        self.assertNotIn("105%", description)

    def test_text_stays_inside_image_bounds_for_long_unicode(self):
        self.pillow()
        from PIL import ImageDraw
        boxes = []
        original = ImageDraw.ImageDraw.text
        def record(draw, xy, text, *args, **kwargs):
            boxes.append(draw.textbbox(xy, text, font=kwargs.get("font"), anchor=kwargs.get("anchor")))
            return original(draw, xy, text, *args, **kwargs)
        data = sample_rail()
        data["outbound"]["fromStation"] = "超长站名e\u0301杭州东" * 100
        with patch.object(ImageDraw.ImageDraw, "text", record):
            result, _ = self.render("rail", data, title="超长标题城市" * 100)
        self.assertTrue(boxes)
        for left, top, right, bottom in boxes:
            self.assertGreaterEqual(left, 48)
            self.assertGreaterEqual(top, 0)
            self.assertLessEqual(right, 1032)
            self.assertLessEqual(bottom, result["height"])

    def test_long_unicode_and_many_rows_are_bounded_and_explicitly_omitted(self):
        data = sample_route()
        data["origin"] = "超长地名杭州西湖🚗e\u0301" * 80
        data["samples"] = [{**data["samples"][0], "label": "途经地点" * 100} for _ in range(200)]
        data["samples"][-1]["label"] = "终点必须保留"
        _, description = self.render("route", data, title="特别长的标题" * 100)
        self.assertIn("已省略", description)
        self.assertIn("终点必须保留", description)
        self.assertLess(len(description), 10000)

    def test_partial_png_save_failure_preserves_existing_output_and_cleans_temporary_files(self):
        Image, _ = self.pillow()
        self.render("weather", sample_weather())
        output = Path(self.temp.name) / "weather.png"
        original = output.read_bytes()
        def fail_after_partial_write(image, target, *args, **kwargs):
            if hasattr(target, "write"):
                target.write(b"incomplete PNG")
            else:
                Path(target).write_bytes(b"incomplete PNG")
            raise OSError("synthetic disk failure")
        with patch.object(Image.Image, "save", fail_after_partial_write):
            with self.assertRaises(OSError):
                self.module().render_card("weather", sample_weather(), output)
        self.assertEqual(output.read_bytes(), original)
        self.assertEqual(list(Path(self.temp.name).iterdir()), [output])

    @unittest.skipIf(os.name == "nt", "Windows chmod does not express POSIX permissions")
    def test_new_png_and_directories_are_private_without_changing_existing_parent(self):
        self.render("weather", {})
        root = Path(self.temp.name)
        root.chmod(0o750)
        output = root / "new" / "nested" / "weather.png"
        self.module().render_card("weather", sample_weather(), output)
        self.assertEqual(stat.S_IMODE(root.stat().st_mode), 0o750)
        for directory in (output.parent, output.parent.parent):
            self.assertEqual(stat.S_IMODE(directory.stat().st_mode), 0o700)
        self.assertEqual(stat.S_IMODE(output.stat().st_mode), 0o600)

    def test_replace_failure_keeps_original_and_cleans_temporary_files(self):
        self.render("route", sample_route())
        output = Path(self.temp.name) / "route.png"
        original = output.read_bytes()
        with patch("os.replace", side_effect=OSError("synthetic replace failure")):
            with self.assertRaises(OSError):
                self.module().render_card("route", sample_route(), output)
        self.assertEqual(output.read_bytes(), original)
        self.assertEqual(list(Path(self.temp.name).iterdir()), [output])

    def test_validates_kind_data_shape_and_png_destination(self):
        renderer = self.module().render_card
        for kind, data, output, error in [("other", {}, "out.png", ValueError),
                                          ("weather", [], "out.png", TypeError),
                                          ("weather", {"days": "bad"}, "out.png", ValueError),
                                          ("route", {"samples": ["bad"]}, "out.png", ValueError),
                                          ("rail", {"outbound": []}, "out.png", ValueError),
                                          ("weather", {}, "out.jpg", ValueError)]:
            with self.subTest(kind=kind, data=data), self.assertRaises(error):
                renderer(kind, data, Path(self.temp.name) / output)

    def test_missing_font_gives_actionable_error(self):
        self.pillow()
        with patch.object(self.module(), "_font_candidates", return_value=[]):
            with self.assertRaisesRegex(RuntimeError, "Noto.*font"):
                self.module().render_card("weather", {}, Path(self.temp.name) / "out.png")

    def test_latin_only_custom_font_is_rejected(self):
        self.pillow()
        latin = next((path for path in ("C:/Windows/Fonts/arial.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf") if Path(path).is_file()), None)
        if latin is None:
            self.skipTest("No Latin font fixture installed")
        with self.assertRaisesRegex(RuntimeError, "Noto.*font"):
            self.module().render_card("weather", {}, Path(self.temp.name) / "out.png", font_path=latin)

    def test_unreadable_auto_font_falls_back_to_working_cjk_font(self):
        module = self.module()
        candidates = list(module._font_candidates())
        available = next((entry for entry in candidates if Path(entry[0]).is_file()), None)
        if available is None:
            self.skipTest("No installed CJK font for image rendering")
        broken = Path(self.temp.name) / "broken.ttf"
        broken.write_text("not a font", encoding="utf-8")
        with patch.object(module, "_font_candidates", return_value=[(str(broken), str(broken)), available]):
            self.render("weather", {})

    def test_module_import_does_not_require_pillow(self):
        module = self.module()
        real_import = builtins.__import__
        def without_pillow(name, *args, **kwargs):
            if name == "PIL" or name.startswith("PIL."):
                raise ImportError("test: Pillow unavailable")
            return real_import(name, *args, **kwargs)
        spec = importlib.util.spec_from_file_location("image_cards_without_pillow", module.__file__)
        isolated = importlib.util.module_from_spec(spec)
        with patch("builtins.__import__", side_effect=without_pillow):
            spec.loader.exec_module(isolated)
            with self.assertRaisesRegex(RuntimeError, "pip install Pillow"):
                isolated.render_card("weather", {}, Path(self.temp.name) / "out.png")


if __name__ == "__main__":
    unittest.main()
