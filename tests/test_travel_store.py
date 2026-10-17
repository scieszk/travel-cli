import copy
import json
import os
from pathlib import Path
import stat
import sys
import tempfile
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit
import re

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
try:
    import travel_store
except ImportError:
    travel_store = None


def plan():
    return {"origin": "A", "destination": "B", "departure_at": "2026-10-19T08:00:00+08:00",
            "arrival_at": "2026-10-19T10:00:00+08:00", "distance_km": 100,
            "duration_minutes": 120, "stop_minutes": 30, "complete": True,
            "warning_counts": {"red": 0, "yellow": 0, "green": 2, "unknown": 0},
            "night_samples": 0, "daylight_complete": True,
            "samples": [
                {"label": "A", "latitude": 30, "longitude": 120,
                 "arrival_at": "2026-10-19T08:00:00+08:00", "departure_at": "2026-10-19T08:00:00+08:00",
                 "stop_minutes": 0, "weather": "晴", "lamp": "🟢", "weather_status": "available"},
                {"label": "B", "latitude": 31, "longitude": 121,
                 "arrival_at": "2026-10-19T10:00:00+08:00", "departure_at": "2026-10-19T10:30:00+08:00",
                 "stop_minutes": 30, "weather": "晴", "lamp": "🟢", "weather_status": "available"}]}


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(travel_store, "private travel workflow module is required")
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "private" / "preferences.json"

    def test_missing_preferences_merge_defaults_without_writing(self):
        values = travel_store.load_preferences(self.path)
        self.assertEqual(values, {"max_detour_minutes": 30, "visit_minutes": 60,
                                  "buffer_minutes": 30, "interests": "any", "avoid_night_driving": False})
        self.assertFalse(self.path.exists())
        values["visit_minutes"] = 90
        self.assertEqual(travel_store.load_preferences(self.path)["visit_minutes"], 60)

    def test_preferences_merge_existing_and_explicit_values_then_reset(self):
        travel_store.save_preferences({"interests": "culture", "visit_minutes": 90}, self.path)
        values = travel_store.save_preferences({"visit_minutes": 120}, self.path)
        self.assertEqual(values["interests"], "culture")
        self.assertEqual(values["visit_minutes"], 120)
        self.assertEqual(travel_store.load_preferences(self.path), values)
        self.assertEqual(travel_store.reset_preferences(self.path)["visit_minutes"], 60)
        self.assertEqual(travel_store.load_preferences(self.path)["interests"], "any")

    def test_preferences_environment_path(self):
        with patch.dict(os.environ, {"TRAVEL_PREFERENCES": str(self.path)}):
            travel_store.save_preferences({"avoid_night_driving": True})
            self.assertTrue(travel_store.load_preferences()["avoid_night_driving"])

    def test_invalid_preferences_are_rejected_without_partial_write(self):
        invalid = [{"api_key": "secret"}, {"visit_minutes": -1}, {"visit_minutes": 1441},
                   {"buffer_minutes": float("nan")}, {"max_detour_minutes": float("inf")},
                   {"visit_minutes": True}, {"avoid_night_driving": "false"}, {"interests": "shopping"}]
        for values in invalid:
            with self.subTest(values=values), self.assertRaises(ValueError):
                travel_store.save_preferences(values, self.path)
        self.assertFalse(self.path.exists())

    def test_corrupt_file_errors_do_not_disclose_data_or_path(self):
        self.path.parent.mkdir()
        for contents in ('secret-location-not-json', '{"private_address":"secret-location"}', '[]'):
            self.path.write_text(contents, encoding="utf-8")
            with self.assertRaises(ValueError) as caught:
                travel_store.load_preferences(self.path)
            self.assertNotIn("secret-location", str(caught.exception))
            self.assertNotIn(str(self.path), str(caught.exception))

    def test_parse_cli_updates_validates_types(self):
        self.assertEqual(travel_store.parse_updates(["visit_minutes=90", "interests=nature", "avoid_night_driving=false"]),
                         {"visit_minutes": 90, "interests": "nature", "avoid_night_driving": False})
        for items in (["visit_minutes"], ["unknown=true"], ["avoid_night_driving=1"], ["visit_minutes=nan"],
                      ["interests=nature", "interests=culture"]):
            with self.subTest(items=items), self.assertRaises(ValueError):
                travel_store.parse_updates(items)

    @unittest.skipIf(os.name == "nt", "Windows chmod does not express POSIX permissions")
    def test_private_files_and_only_new_directories_get_restricted_modes(self):
        existing = Path(self.tmp.name)
        existing.chmod(0o755)
        travel_store.save_preferences({}, self.path)
        self.assertEqual(stat.S_IMODE(self.path.stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(self.path.parent.stat().st_mode), 0o700)
        self.assertEqual(stat.S_IMODE(existing.stat().st_mode), 0o755)
        self.path.chmod(0o644)
        travel_store.save_preferences({"visit_minutes": 90}, self.path)
        self.assertEqual(stat.S_IMODE(self.path.stat().st_mode), 0o600)

    def test_atomic_write_failure_preserves_previous_preferences(self):
        travel_store.save_preferences({"visit_minutes": 90}, self.path)
        with patch.object(travel_store.os, "replace", side_effect=OSError("private path secret")):
            with self.assertRaises(ValueError) as caught:
                travel_store.save_preferences({"visit_minutes": 100}, self.path)
        self.assertNotIn("private path secret", str(caught.exception))
        self.assertEqual(travel_store.load_preferences(self.path)["visit_minutes"], 90)
        self.assertEqual(list(self.path.parent.iterdir()), [self.path])

    def test_existing_shared_parent_permissions_are_never_changed(self):
        target = Path(self.tmp.name) / "preferences.json"
        with patch.object(travel_store.os, "chmod", wraps=os.chmod) as chmod:
            travel_store.save_preferences({}, target)
        self.assertTrue(chmod.called)
        self.assertTrue(all(Path(call.args[0]) != target.parent for call in chmod.call_args_list))
        self.assertTrue(all(call.args[1] == 0o600 for call in chmod.call_args_list))

    def test_card_contains_estimates_attribution_and_fixed_gcj02_map_links(self):
        data = plan()
        data["samples"][1]["label"] = "景点 A & B](javascript:evil)"
        card = travel_store.render_card(data)
        self.assertIn("预计", card)
        self.assertIn("北京时间", card)
        self.assertIn("Open-Meteo", card)
        self.assertIn("CC BY 4.0", card)
        self.assertIn("GCJ02", card)
        urls = re.findall(r"https://uri\.amap\.com/marker\?[^)\s]+", card)
        self.assertEqual(len(urls), 2)
        parsed = parse_qs(urlsplit(urls[1]).query)
        self.assertEqual(parsed["name"], [data["samples"][1]["label"]])
        self.assertEqual(parsed["position"], ["121,31"])
        self.assertEqual(parsed["coordinate"], ["gaode"])
        self.assertNotIn("](javascript:", card)

    def test_card_missing_numbers_and_weather_never_show_nan_or_good_weather(self):
        data = plan()
        data.update(distance_km=float("nan"), complete=False)
        data["samples"][0].update(longitude=float("inf"), weather=None, lamp="⚪")
        card = travel_store.render_card(data)
        self.assertNotIn("nan", card.lower())
        self.assertNotIn("inf", card.lower())
        self.assertIn("未知不代表天气良好", card)
        self.assertEqual(card.count("https://uri.amap.com/marker?"), 1)

    def test_card_map_coordinates_preserve_point_precision(self):
        data = plan()
        data["samples"][0].update(longitude=116.397128, latitude=39.916527)
        url = re.search(r"https://uri\.amap\.com/marker\?[^)\s]+", travel_store.render_card(data)).group()
        self.assertEqual(parse_qs(urlsplit(url).query)["position"], ["116.397128,39.916527"])

    def test_watch_persists_only_validated_options_and_minimal_baseline(self):
        data = plan()
        data["api_key"] = "do-not-save"
        data["samples"][0]["raw_forecast"] = {"secret": "do-not-save"}
        options = {"origin": "A", "dest": "B", "via": [], "stop": [], "date": "2026-10-19",
                   "depart": "08:00", "speed": 0, "step": 40,
                   "waypoint_overrides": {"B": {"lng": 121, "lat": 31, "elev": None, "coord_system": "gcj02"}}}
        target = travel_store.save_watch(Path(self.tmp.name) / "outing.json", data, options)
        self.assertEqual(target.name, "outing.json.watch.json")
        saved = travel_store.load_watch(target)
        self.assertEqual(saved["options"], options)
        self.assertNotIn("do-not-save", target.read_text(encoding="utf-8"))
        self.assertNotIn("raw_forecast", saved["baseline"]["samples"][0])
        self.assertEqual(travel_store.compare_watch(saved["baseline"], data)["state"], "unchanged")
        if os.name != "nt":
            self.assertEqual(stat.S_IMODE(target.stat().st_mode), 0o600)

    def test_watch_rejects_credentials_invalid_coordinates_and_malformed_schema(self):
        for options in ({"origin": "A", "dest": "B", "api_key": "secret"},
                        {"origin": "A", "dest": "B", "waypoint_overrides": {"B": {"lng": float("nan"), "lat": 31}}}):
            with self.subTest(options=options), self.assertRaises(ValueError):
                travel_store.save_watch(Path(self.tmp.name) / "bad", plan(), options)
        target = Path(self.tmp.name) / "bad.watch.json"
        target.write_text('{"schema_version": 999, "secret": "private"}', encoding="utf-8")
        with self.assertRaises(ValueError) as caught:
            travel_store.load_watch(target)
        self.assertNotIn("private", str(caught.exception))

    def test_watch_changed_weather_daylight_and_arrival_threshold(self):
        before = plan()
        current = copy.deepcopy(before)
        self.assertEqual(travel_store.compare_watch(before, current), {"state": "unchanged", "changes": [], "notify": False})
        current["arrival_at"] = "2026-10-19T10:29:00+08:00"
        self.assertEqual(travel_store.compare_watch(before, current)["state"], "unchanged")
        current["arrival_at"] = "2026-10-19T10:30:00+08:00"
        self.assertEqual(travel_store.compare_watch(before, current)["state"], "changed")
        current = copy.deepcopy(before)
        current["samples"][1]["lamp"] = "🟡"
        result = travel_store.compare_watch(before, current)
        self.assertEqual(result["state"], "changed")
        self.assertTrue(result["notify"])
        current = copy.deepcopy(before)
        current["night_samples"] = 1
        self.assertEqual(travel_store.compare_watch(before, current)["state"], "changed")

    def test_missing_weather_or_samples_is_unavailable_even_when_complete_claimed(self):
        for update in ({"complete": False}, {"samples": []}, {"arrival_at": None}, {"daylight_complete": False}):
            current = plan()
            current.update(update)
            with self.subTest(update=update):
                self.assertEqual(travel_store.compare_watch(plan(), current)["state"], "unavailable")
        current = plan()
        current["samples"][0]["lamp"] = "⚪"
        self.assertEqual(travel_store.compare_watch(plan(), current)["state"], "unavailable")
        self.assertEqual(travel_store.compare_watch(plan(), None)["state"], "unavailable")

    def test_watch_detects_night_location_change_with_unchanged_night_count(self):
        before, current = plan(), plan()
        before.update(night_samples=1)
        current.update(night_samples=1)
        before["samples"][0]["daylight"] = True
        before["samples"][1]["daylight"] = False
        current["samples"][0]["daylight"] = False
        current["samples"][1]["daylight"] = True
        self.assertEqual(travel_store.compare_watch(before, current)["state"], "changed")

    def test_unknown_or_removed_daylight_is_unavailable(self):
        before, current = plan(), plan()
        before["samples"][0]["daylight"] = True
        current["samples"][0]["daylight"] = None
        self.assertEqual(travel_store.compare_watch(before, current)["state"], "unavailable")
        del current["samples"][0]["daylight"]
        self.assertEqual(travel_store.compare_watch(before, current)["state"], "unavailable")

    def test_watch_dedup_preserves_baseline_and_options_and_allows_new_events(self):
        target = travel_store.save_watch(Path(self.tmp.name) / "watch", plan(), {"origin": "A", "dest": "B"})
        initial = travel_store.load_watch(target)
        current = plan()
        current["samples"][0]["lamp"] = "🟡"
        result = travel_store.compare_watch(initial["baseline"], current)
        self.assertTrue(travel_store.record_watch_check(target, result)["notify"])
        self.assertFalse(travel_store.record_watch_check(target, result)["notify"])
        unavailable = travel_store.compare_watch(initial["baseline"], None)
        self.assertTrue(travel_store.record_watch_check(target, unavailable)["notify"])
        self.assertFalse(travel_store.record_watch_check(target, unavailable)["notify"])
        unchanged = travel_store.compare_watch(initial["baseline"], plan())
        recovered = travel_store.record_watch_check(target, unchanged)
        self.assertTrue(recovered["notify"])
        self.assertIn("恢复", recovered["changes"][0])
        self.assertFalse(travel_store.record_watch_check(target, unchanged)["notify"])
        self.assertTrue(travel_store.record_watch_check(target, result)["notify"])
        saved = travel_store.load_watch(target)
        self.assertEqual(saved["baseline"], initial["baseline"])
        self.assertEqual(saved["options"], initial["options"])


if __name__ == "__main__":
    unittest.main()
