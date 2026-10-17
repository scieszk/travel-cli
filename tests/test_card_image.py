import contextlib
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
from types import ModuleType
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import card_image


class CardImageTests(unittest.TestCase):
    def setUp(self):
        self.cli = card_image
        self.renderer = ModuleType("image_cards")
        self.metadata = {"path": "card.png", "width": 1080, "height": 1200,
                         "caption": "示例天气", "paths": ["card.png"]}
        self.renderer.render_card = Mock(return_value=self.metadata)
        self.weather = {"place": "示例地点", "days": [{"date": "2026-10-18", "weather": "晴"}]}
        self.route = {"origin": "A", "destination": "B", "samples": []}
        self.rail = {"outbound": {"code": "G100", "fromStation": "甲", "toStation": "乙"},
                     "return": {"code": "G101", "fromStation": "乙", "toStation": "甲"}}

    def invoke(self, payload=None, *, kind="weather", extra=(), raw=None, renderer=None):
        if raw is None:
            raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        stdin = io.TextIOWrapper(io.BytesIO(raw), encoding="utf-8")
        stdout, stderr = io.StringIO(), io.StringIO()
        module = self.renderer if renderer is None else renderer
        with patch.dict(sys.modules, {"image_cards": module}), patch.object(sys, "stdin", stdin), \
                contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            status = self.cli.main(["--kind", kind, "--input", "-", "--output", "card.png", *extra])
        return status, stdout.getvalue(), stderr.getvalue()

    def assert_error(self, result, code=None):
        status, stdout, stderr = result
        self.assertNotEqual(status, 0)
        self.assertEqual(stderr, "")
        self.assertEqual(len(stdout.splitlines()), 1)
        value = json.loads(stdout)
        self.assertEqual(value["schema_version"], "1.0")
        self.assertEqual(value["command"], "image")
        self.assertIs(value["ok"], False)
        if code:
            self.assertEqual(value["error"]["code"], code)
        self.assertNotIn("private-marker", stdout)
        self.assertNotIn("Traceback", stdout)
        return value

    def test_raw_weather_returns_renderer_metadata_in_one_json_document(self):
        status, stdout, stderr = self.invoke(self.weather)
        self.assertEqual(status, 0, stdout)
        self.assertEqual(stderr, "")
        self.assertEqual(json.loads(stdout), {"schema_version": "1.0", "ok": True,
                                             "command": "image", "data": {"image": self.metadata}})
        self.assertEqual(len(stdout.splitlines()), 1)
        args, kwargs = self.renderer.render_card.call_args
        self.assertEqual(args, ("weather", self.weather, "card.png"))
        self.assertIsNone(kwargs["generated_at"])

    def test_structured_weather_preserves_original_query_time_and_sources(self):
        source = [{"name": "Open-Meteo", "url": "https://open-meteo.com/"}]
        queried_at = "2020-01-01T08:00:00+08:00"
        payload = {"ok": True, "command": "weather", "data": self.weather,
                   "queried_at": queried_at, "sources": source}
        status, stdout, _ = self.invoke(payload)
        self.assertEqual(status, 0, stdout)
        data = self.renderer.render_card.call_args.args[1]
        self.assertEqual(data["sources"], source)
        self.assertEqual(data["queried_at"], queried_at)
        self.assertEqual(self.renderer.render_card.call_args.kwargs["generated_at"], queried_at)

    def test_raw_data_query_timestamp_is_forwarded(self):
        self.invoke({**self.weather, "queried_at": "2021-04-01T12:00:00Z"})
        self.assertEqual(self.renderer.render_card.call_args.kwargs["generated_at"], "2021-04-01T12:00:00Z")

    def test_route_compare_and_outing_select_the_requested_plan(self):
        plans = [self.route, {**self.route, "destination": "C", "source_note": "plan source"}]
        for command in ("route", "compare", "outing"):
            with self.subTest(command=command):
                result = self.invoke({"ok": True, "command": command,
                                      "data": {"plans": plans, "source_note": "parent source"}},
                                     kind="route", extra=("--select", "2"))
                self.assertEqual(result[0], 0, result[1])
                selected = self.renderer.render_card.call_args.args[1]
                self.assertEqual(selected["destination"], "C")
                self.assertEqual(selected["source_note"], "plan source")

    def test_rail_accepts_raw_plan_arrays_and_structured_plan_collections(self):
        second = {**self.rail, "stayMin": 150}
        for payload in ([self.rail, second], {"ok": True, "data": [self.rail, second]},
                        {"plans": [self.rail, second]}, {"ok": True, "data": {"plans": [self.rail, second]}}):
            with self.subTest(payload=payload):
                result = self.invoke(payload, kind="rail", extra=("--select", "2"))
                self.assertEqual(result[0], 0, result[1])
                self.assertEqual(self.renderer.render_card.call_args.args[1], second)

    def test_raw_rail_and_route_objects_default_to_the_single_plan(self):
        for kind, payload in (("rail", self.rail), ("route", self.route)):
            with self.subTest(kind=kind):
                self.assertEqual(self.invoke(payload, kind=kind)[0], 0)
                self.assertEqual(self.renderer.render_card.call_args.args[1], payload)

    def test_explicit_font_is_forwarded(self):
        self.assertEqual(self.invoke(self.weather, extra=("--font", "custom.ttf"))[0], 0)
        self.assertEqual(self.renderer.render_card.call_args.kwargs["font_path"], "custom.ttf")

    def test_utf8_bom_and_file_input_are_supported(self):
        raw = b"\xef\xbb\xbf" + json.dumps(self.weather, ensure_ascii=False).encode("utf-8")
        self.assertEqual(self.invoke(raw=raw)[0], 0)
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "input.json"
            path.write_bytes(raw)
            result = self.invoke(raw=b"", extra=("--input", str(path)))
        self.assertEqual(result[0], 0, result[1])

    def test_invalid_arguments_are_one_sanitized_json_error(self):
        for extra in (("--select", "0"), ("--select", "-1"), ("--select", "1.5"),
                      ("--kind", "private-marker"), ("--private-marker",), ("--output", "private-marker.jpg")):
            with self.subTest(extra=extra):
                self.assert_error(self.invoke(self.weather, extra=extra), "INVALID_ARGUMENT")
        self.renderer.render_card.assert_not_called()

    def test_failed_upstream_response_is_rejected_without_echo(self):
        self.assert_error(self.invoke({"ok": False, "error": {"message": "private-marker"},
                                       "data": self.weather}), "UPSTREAM_ERROR")
        self.renderer.render_card.assert_not_called()

    def test_wrong_kind_shapes_are_rejected_before_rendering(self):
        for kind, payload in (("weather", []), ("weather", {}), ("weather", self.route),
                              ("weather", {"place": "A", "days": "private-marker"}),
                              ("route", self.weather), ("route", [self.route]),
                              ("route", {**self.route, "samples": [False]}),
                              ("rail", {}), ("rail", {"outbound": [], "return": {}}),
                              ("rail", {"plans": "private-marker"}),
                              ("weather", {"ok": "true", "data": self.weather}),
                              ("weather", {"ok": True})):
            with self.subTest(kind=kind, payload=payload):
                self.assert_error(self.invoke(payload, kind=kind), "INVALID_INPUT")
        self.renderer.render_card.assert_not_called()

    def test_empty_choices_and_out_of_range_selection_fail(self):
        for kind, payload, select in (("route", {"plans": []}, "1"), ("rail", [], "1"),
                                     ("rail", {"plans": []}, "1"),
                                     ("route", {"plans": [self.route]}, "2"),
                                     ("rail", [self.rail], "2"), ("weather", self.weather, "2"),
                                     ("route", self.route, "2")):
            with self.subTest(kind=kind, select=select):
                self.assert_error(self.invoke(payload, kind=kind, extra=("--select", select)), "INVALID_SELECTION")
        self.renderer.render_card.assert_not_called()

    def test_bad_json_and_encoding_do_not_echo_input(self):
        for raw in (b'{"private-marker":', b"\xffprivate-marker", b'{"days": NaN}',
                    b'[' * 2000 + b'0' + b']' * 2000):
            with self.subTest(raw=raw[:30]):
                self.assert_error(self.invoke(raw=raw), "INVALID_INPUT")
        self.renderer.render_card.assert_not_called()

    def test_input_size_limit_applies_to_stdin_and_files(self):
        raw = b" " * (4 * 1024 * 1024 + 1)
        self.assert_error(self.invoke(raw=raw), "INPUT_TOO_LARGE")
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "large.json"
            path.write_bytes(raw)
            self.assert_error(self.invoke(raw=b"", extra=("--input", str(path))), "INPUT_TOO_LARGE")
        self.renderer.render_card.assert_not_called()

    def test_exactly_four_mib_is_accepted(self):
        encoded = json.dumps(self.weather).encode("utf-8")
        raw = encoded + b" " * (4 * 1024 * 1024 - len(encoded))
        result = self.invoke(raw=raw)
        self.assertEqual(result[0], 0, result[1])

    def test_missing_input_file_is_a_sanitized_error(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "private-marker.json"
            self.assert_error(self.invoke(raw=b"", extra=("--input", str(path))), "INPUT_ERROR")
        self.renderer.render_card.assert_not_called()

    def test_renderer_exception_is_sanitized(self):
        self.renderer.render_card.side_effect = ValueError("private-marker")
        self.assert_error(self.invoke(self.weather), "IMAGE_ERROR")

    def test_missing_renderer_dependency_is_sanitized(self):
        self.renderer.render_card.side_effect = ModuleNotFoundError("private-marker")
        self.assert_error(self.invoke(self.weather), "IMAGE_ERROR")

    def test_import_and_invalid_input_do_not_load_pillow_or_renderer(self):
        code = "import sys; sys.modules['image_cards'] = None; sys.modules['PIL'] = None; import card_image; raise SystemExit(card_image.main(['--kind', 'weather', '--input', '-', '--output', 'card.png']))"
        result = subprocess.run([sys.executable, "-c", code], input="{}", text=True, capture_output=True,
                                cwd=Path(__file__).resolve().parents[1], check=False)
        self.assert_error((result.returncode, result.stdout, result.stderr), "INVALID_INPUT")


if __name__ == "__main__":
    unittest.main()
