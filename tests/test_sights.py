"""Synthetic, offline tests for bounded scenic discovery and detour estimates."""
import contextlib
import io
import json
from pathlib import Path
import sys
import unittest
from urllib.parse import parse_qs, urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
try:
    import sights
except ModuleNotFoundError:
    sights = None


def sample(index=0):
    return {"longitude": 120 + index * .001, "latitude": 30,
            "distance_km": index * 10, "elapsed_hours": index / 6,
            "label": f"sample-{index}"}


def poi(identifier="P1", distance="200", **changes):
    record = {"id": identifier, "name": f"景点{identifier}",
              "address": "示例街1号", "location": "120.001,30",
              "cityname": "示例市", "distance": distance,
              "type": "风景名胜;风景名胜相关", "typecode": "110000"}
    record.update(changes)
    return record


def response(*pois):
    return {"status": "1", "info": "OK", "infocode": "10000",
            "count": str(len(pois)), "pois": list(pois)}


class SightsTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(sights, "The requested sights module is not implemented")

    def test_empty_samples_need_no_key_or_network(self):
        def forbidden(*args):
            self.fail("An empty route must not use a credential or network")
        result = sights.discover_sights([], forbidden, forbidden)
        self.assertEqual(result["sights"], [])
        self.assertEqual(result["query_points_count"], 0)
        self.assertTrue(result["complete"])
        self.assertIsNone(result["queried_route_km_min"])
        self.assertIsNone(result["queried_route_km_max"])

    def test_requests_are_bounded_evenly_spaced_and_fixed_to_amap(self):
        urls, key_reads = [], []
        def request(url):
            urls.append(url)
            return response()
        def key_provider():
            key_reads.append(True)
            return "unit-test-map-credential"
        result = sights.discover_sights([sample(i) for i in range(20)], request, key_provider)
        self.assertEqual(len(urls), 6)
        self.assertEqual(len(key_reads), 1)
        self.assertEqual(result["query_points_count"], 6)
        locations = []
        for url in urls:
            parsed = urlsplit(url)
            self.assertEqual((parsed.scheme, parsed.netloc, parsed.path),
                             ("https", "restapi.amap.com", "/v3/place/around"))
            query = parse_qs(parsed.query)
            self.assertEqual(query["types"], ["110000|140100"])
            self.assertEqual(query["radius"], ["3000"])
            self.assertEqual(query["offset"], ["20"])
            self.assertEqual(query["page"], ["1"])
            self.assertEqual(query["sortrule"], ["distance"])
            locations.append(tuple(map(float, query["location"][0].split(","))))
        self.assertEqual([round((lng - 120) * 1000) for lng, _ in locations], [0, 4, 8, 11, 15, 19])
        self.assertEqual(result["queried_route_km_min"], 0)
        self.assertEqual(result["queried_route_km_max"], 190)
        self.assertEqual(result["query_radius_m"], 3000)
        self.assertIn("绕行", result["distance_note"])
        self.assertIn("完整", result["coverage_note"])
        self.assertNotIn("unit-test-map-credential", json.dumps(result))

    def test_deduplication_keeps_closest_observation_then_sorts_and_limits(self):
        responses = iter([response(poi("P1", "500"), poi("P2", "50")),
                          response(poi("P1", "100"), poi("P3", "80"))])
        result = sights.discover_sights([sample(), sample(1)], lambda _: next(responses),
                                       lambda: "synthetic", limit=2)
        self.assertEqual([p["id"] for p in result["sights"]], ["P2", "P3"])
        responses = iter([response(poi("P1", "500")), response(poi("P1", "100"))])
        result = sights.discover_sights([sample(), sample(1)], lambda _: next(responses), lambda: "synthetic")
        record = result["sights"][0]
        self.assertEqual(record["distance_from_route_sample_m"], 100)
        self.assertEqual(record["route_km"], 10)
        self.assertEqual(record["longitude"], 120.001)
        self.assertEqual(record["latitude"], 30)
        self.assertEqual(record["city"], "示例市")

    def test_missing_distance_is_computed_and_empty_optional_fields_are_normalized(self):
        result = sights.discover_sights([sample()], lambda _: response(poi(distance=[], address=[], cityname=[])), lambda: "synthetic")
        record = result["sights"][0]
        self.assertGreater(record["distance_from_route_sample_m"], 90)
        self.assertLess(record["distance_from_route_sample_m"], 100)
        self.assertEqual(record["address"], "")
        self.assertEqual(record["city"], "")
        self.assertTrue(result["complete"])

    def test_partial_failure_is_sanitized_and_remaining_queries_continue(self):
        calls = []
        def request(url):
            calls.append(url)
            if len(calls) == 2:
                raise SystemExit("sensitive-example " + url)
            return response(poi(str(len(calls))))
        output = io.StringIO()
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
            result = sights.discover_sights([sample(i) for i in range(3)], request, lambda: "unit-test-map-credential")
        self.assertEqual(result["query_points_count"], 3)
        self.assertFalse(result["complete"])
        self.assertEqual(len(result["sights"]), 2)
        self.assertTrue(result["warning"])
        self.assertEqual(output.getvalue(), "")
        for marker in ["sensitive-example", "unit-test-map-credential", "location="]:
            self.assertNotIn(marker, json.dumps(result))

    def test_empty_success_differs_from_malformed_and_status_zero(self):
        for payload in [response(), {"status": "1", "count": "0", "pois": []}]:
            with self.subTest(payload=payload):
                result = sights.discover_sights([sample()], lambda _: payload, lambda: "synthetic")
                self.assertEqual(result["sights"], [])
                self.assertTrue(result["complete"])
        for payload in [{"status": "0", "info": "private-url"}, {}, [],
                        {"status": "1", "pois": {}}, {"status": "1", "count": "1", "pois": []}]:
            with self.subTest(payload=payload):
                result = sights.discover_sights([sample()], lambda _: payload, lambda: "synthetic")
                self.assertEqual(result["sights"], [])
                self.assertFalse(result["complete"])
                self.assertNotIn("private-url", result["warning"])

    def test_malformed_poi_does_not_hide_valid_poi(self):
        for broken in [poi(location="nan,30"), poi(location="181,30"), poi(id=""), poi(name=[]), "bad"]:
            with self.subTest(broken=broken):
                result = sights.discover_sights([sample()], lambda _: response(broken, poi("valid")), lambda: "synthetic")
                self.assertEqual([p["id"] for p in result["sights"]], ["valid"])
                self.assertFalse(result["complete"])

    def test_invalid_settings_or_samples_fail_before_key_or_request(self):
        def forbidden(*args):
            self.fail("Invalid arguments must not trigger network or credentials")
        for kwargs in [{"radius": 0}, {"radius": 50001}, {"radius": float("nan")},
                       {"limit": 0}, {"limit": 121}, {"limit": 2.5}]:
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                sights.discover_sights([sample()], forbidden, forbidden, **kwargs)
        broken = sample()
        broken["latitude"] = float("inf")
        with self.assertRaises(ValueError):
            sights.discover_sights([broken], forbidden, forbidden)

    def test_credential_provider_failure_is_sanitized(self):
        def broken_key():
            raise RuntimeError("private-key-text")
        with self.assertRaises(ValueError) as error:
            sights.discover_sights([sample()], lambda _: self.fail("No key"), broken_key)
        self.assertNotIn("private-key-text", str(error.exception))

    def test_outing_radius_can_use_the_documented_fifty_kilometre_maximum(self):
        urls = []
        def request(url):
            urls.append(url)
            return response()
        result = sights.discover_sights([sample()], request, lambda: "synthetic", radius=50000)
        self.assertEqual(result["query_radius_m"], 50000)
        self.assertEqual(parse_qs(urlsplit(urls[0]).query)["radius"], ["50000"])

    def test_interest_category_uses_actual_type_or_curated_entry_not_name_guessing(self):
        payload = response(
            poi("museum", typecode="140100", type=[]),
            poi("park", typecode="110101", type="风景名胜;公园广场;公园"),
            poi("unknown", name="名字含有公园的未分类场所"),
            poi("known", name="故宫博物院"),
            poi("square", typecode="110105", type="风景名胜;公园广场;城市广场"),
        )
        result = sights.discover_sights([sample()], lambda _: payload, lambda: "synthetic")
        by_id = {p["id"]: p for p in result["sights"]}
        self.assertEqual(by_id["museum"]["category"], "culture")
        self.assertEqual(by_id["park"]["category"], "nature")
        self.assertEqual(by_id["unknown"]["category"], "any")
        self.assertEqual(by_id["known"]["category"], "culture")
        self.assertEqual(by_id["known"]["category_source"], "builtin_catalogue")
        self.assertEqual(by_id["square"]["category"], "any")


class RankSightsTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(sights, "The requested sights module is not implemented")

    def candidates(self, count=3):
        return [{"id": str(i), "name": f"place-{i}", "longitude": 120 + i * .001,
                 "latitude": 30, "route_km": i * 10} for i in range(count)]

    def test_reroutes_through_poi_and_ranks_actual_extra_driving(self):
        calls = []
        durations = iter([140, 110, 120])
        def route(*args, **kwargs):
            calls.append((args, kwargs))
            return [], 150, next(durations) / 60
        result = sights.rank_sights(self.candidates(), (119, 30), (121, 31), 100, route,
                                   max_detour_minutes=30, visit_minutes=60, limit=3)
        self.assertEqual([r["id"] for r in result["sights"]], ["1", "2"])
        record = result["sights"][0]
        self.assertAlmostEqual(record["extra_driving_minutes"], 10)
        self.assertAlmostEqual(record["total_extra_minutes"], 70)
        self.assertEqual(record["visit_minutes"], 60)
        self.assertEqual(record["route_distance_km"], 150)
        self.assertEqual(calls[0][0], (119, 30, 121, 31))
        self.assertEqual(tuple(map(float, calls[0][1]["waypoints"].split(","))), (120, 30))
        self.assertEqual(result["assessed_count"], 3)
        self.assertTrue(result["complete"])
        self.assertEqual(result["failures"], [])

    def test_assessment_requests_and_output_are_bounded(self):
        calls = []
        def route(*args, **kwargs):
            calls.append(True)
            return [], 100, 2
        result = sights.rank_sights(self.candidates(10), (119, 30), (121, 31), 100, route)
        self.assertEqual(len(calls), 5)
        self.assertEqual(result["assessed_count"], 5)
        self.assertEqual(len(result["sights"]), 3)
        self.assertEqual(result["candidate_count"], 10)
        self.assertEqual(result["unassessed_count"], 5)
        self.assertIn("5", result["assessment_note"])

    def test_shorter_or_failed_recalculation_is_not_a_zero_detour(self):
        count = 0
        def route(*args, **kwargs):
            nonlocal count
            count += 1
            if count == 1:
                return [], 70, 1
            if count == 2:
                raise SystemExit("private-route-and-key")
            return [], 110, 2
        result = sights.rank_sights(self.candidates(), (119, 30), (121, 31), 100, route)
        self.assertFalse(result["complete"])
        self.assertEqual([r["id"] for r in result["sights"]], ["2"])
        self.assertEqual([r["id"] for r in result["failures"]], ["0", "1"])
        self.assertIn("基准", result["failures"][0]["reason"])
        self.assertNotIn("private-route-and-key", json.dumps(result))

    def test_nonfinite_route_result_is_reported_as_failure(self):
        for hours in [float("nan"), float("inf"), 1e308]:
            with self.subTest(hours=hours):
                result = sights.rank_sights(self.candidates(1), (119, 30), (121, 31), 100,
                                           lambda *a, **k: ([], 100, hours))
                self.assertEqual(result["sights"], [])
                self.assertFalse(result["complete"])
                self.assertEqual(len(result["failures"]), 1)

    def test_rank_validates_settings_before_reroute(self):
        for kwargs in [{"max_detour_minutes": -1}, {"visit_minutes": -1},
                       {"visit_minutes": float("inf")}, {"limit": 0}, {"limit": 4}]:
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                sights.rank_sights(self.candidates(), (119, 30), (121, 31), 100,
                                   lambda *a, **k: self.fail("Invalid setting"), **kwargs)


class BuiltinSightsTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(sights, "The requested sights module is not implemented")

    def test_catalogue_has_official_addresses_and_transparent_visit_estimates(self):
        records = sights.load_builtin_sights()
        self.assertGreaterEqual(len(records), 4)
        self.assertLessEqual(len(records), 6)
        for record in records:
            for key in ["name", "city", "address", "source_url", "visit_minutes_note"]:
                self.assertTrue(record[key], key)
            self.assertIsInstance(record["aliases"], list)
            self.assertIn(record["category"], ["nature", "culture", "any"])
            self.assertGreater(record["visit_minutes"], 0)
            self.assertIn("估算", record["visit_minutes_note"])
            self.assertTrue(record["source_url"].startswith("https://"))
            self.assertNotIn("longitude", record)
            self.assertNotIn("latitude", record)

    def test_names_and_aliases_match_exactly_and_case_insensitively(self):
        record = sights.match_builtin_sight("  故宫  ")
        self.assertEqual(record["name"], "故宫博物院")
        self.assertEqual(sights.match_builtin_sight("PALACE MUSEUM")["name"], record["name"])
        self.assertIsNone(sights.match_builtin_sight("故"))
        self.assertIsNone(sights.match_builtin_sight("unknown-site"))

    def test_callers_cannot_mutate_future_catalogue_reads(self):
        records = sights.load_builtin_sights()
        name = records[0]["name"]
        records[0]["aliases"].append("injected-alias")
        records[0]["name"] = "changed"
        self.assertEqual(sights.load_builtin_sights()[0]["name"], name)
        self.assertIsNone(sights.match_builtin_sight("injected-alias"))


if __name__ == "__main__":
    unittest.main()
