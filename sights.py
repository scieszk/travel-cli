"""Bounded Amap scenic discovery, optional driving detours, and a small catalogue.

All coordinates accepted/returned here are GCJ-02 (longitude, latitude).
The Amap request schema and distance meaning follow the official documentation:
https://lbs.amap.com/api/webservice/guide/api-advanced/search
The via-POI driving request follows:
https://lbs.amap.com/api/webservice/guide/api/direction
POI type codes were checked against Amap's official downloadable classification:
https://lbs.amap.com/api/webservice/download
The supplied request_json callable performs one HTTP request and returns decoded
JSON; pass a no-retry adapter if the caller normally retries requests. Credentials
are obtained once per discovery call, used only in HTTPS requests, and never
persisted or included in returned diagnostics by this module.

Catalogue names and addresses are factual transcriptions from the venue URLs in
builtin_sights.json, checked on 2026-10-18. Categories and visit durations are
editorial estimates, not venue promises. No coordinates are curated: callers
should geocode addresses through Amap only when needed. Addresses need not be
driving entrances; opening, booking, parking and access require separate checks.
"""
import json
import math
from pathlib import Path
from urllib.parse import urlencode


MAX_QUERY_POINTS = 6
MAX_POIS_PER_QUERY = 20
MAX_DETOUR_CANDIDATES = 5
AMAP_AROUND_URL = "https://restapi.amap.com/v3/place/around"
_DISCOVERY_WARNING = "部分景点查询失败或返回数据不完整；已保留可用结果。"


def _number(value):
    if isinstance(value, bool):
        raise ValueError("Expected a finite number")
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        raise ValueError("Expected a finite number") from None
    if not math.isfinite(number):
        raise ValueError("Expected a finite number")
    return number


def _coordinates(longitude, latitude):
    longitude, latitude = _number(longitude), _number(latitude)
    if not -180 <= longitude <= 180 or not -90 <= latitude <= 90:
        raise ValueError("Coordinates are out of range")
    return longitude, latitude


def _integer_option(value, minimum, maximum, name):
    number = _number(value)
    if number != int(number) or not minimum <= number <= maximum:
        raise ValueError(f"{name} must be an integer between {minimum} and {maximum}")
    return int(number)


def _text(value):
    # Amap represents some absent string fields as [] instead of an empty string.
    return value.strip() if isinstance(value, str) else ""


def _distance_m(lng1, lat1, lng2, lat2):
    lat1, lat2 = math.radians(lat1), math.radians(lat2)
    dlat, dlng = lat2 - lat1, math.radians(lng2 - lng1)
    hav = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlng / 2) ** 2
    return 6371000 * 2 * math.asin(math.sqrt(min(1.0, max(0.0, hav))))


def _category(poi, name):
    """Map actual provider types or a curated entry; never guess from name text."""
    builtin = match_builtin_sight(name)
    if builtin:
        return builtin["category"], "builtin_catalogue"
    codes = set(_text(poi.get("typecode")).split("|"))
    types = set(_text(poi.get("type")).replace("|", ";").split(";"))
    culture_codes = {"140100", "110204", "110205", "110206", "110207"}
    culture_types = {"博物馆", "美术馆", "纪念馆", "寺庙道观", "教堂", "回教寺", "清真寺"}
    nature_codes = {"110101", "110102", "110103", "110104"}
    nature_types = {"公园", "动物园", "植物园", "水族馆", "森林公园", "湿地公园", "国家公园", "自然保护区"}
    if codes & culture_codes or types & culture_types:
        return "culture", "amap_type"
    if codes & nature_codes or types & nature_types:
        return "nature", "amap_type"
    return "any", "unclassified"


def _poi_record(poi, center):
    if not isinstance(poi, dict):
        raise ValueError("Malformed POI")
    identifier, name = _text(poi.get("id")), _text(poi.get("name"))
    if not identifier or not name:
        raise ValueError("POI identity is missing")
    location = _text(poi.get("location")).split(",")
    if len(location) != 2:
        raise ValueError("POI location is missing")
    longitude, latitude = _coordinates(*location)
    try:
        distance = _number(poi.get("distance"))
        if distance < 0:
            raise ValueError("Negative distance")
    except ValueError:
        distance = _distance_m(center["longitude"], center["latitude"], longitude, latitude)
    category, category_source = _category(poi, name)
    return {
        "id": identifier, "name": name, "address": _text(poi.get("address")),
        "longitude": longitude, "latitude": latitude,
        "city": _text(poi.get("cityname")),
        "distance_from_route_sample_m": distance,
        "route_km": center["distance_km"],
        "category": category, "category_source": category_source,
        "type": _text(poi.get("type")), "typecode": _text(poi.get("typecode")),
    }


def _pois_from_response(payload):
    if not isinstance(payload, dict) or str(payload.get("status")) != "1":
        raise ValueError("Unsuccessful POI response")
    pois = payload.get("pois")
    if not isinstance(pois, list):
        raise ValueError("Missing POI list")
    if "count" in payload:
        count = _number(payload["count"])
        if count != int(count) or count < len(pois) or (not pois and count != 0):
            raise ValueError("Inconsistent POI count")
    return pois


def discover_sights(samples, request_json, key_provider, *, radius=3000, limit=8):
    """Find scenic POIs near <=6 evenly spaced samples, without measuring detours.

    samples contains GCJ-02 longitude/latitude and distance_km; labels and elapsed
    hours may be supplied but are not sent to Amap. radius is 1..50000 metres and
    limit is 1..120. Route CLI callers should enforce their narrower 5000m cap;
    outing callers may use the wider radius. Each query uses scenic and museum
    types=110000|140100, offset=20 and page=1.
    Duplicate POI IDs retain the closest returned query-center observation.
    Results sort by route_km then sample distance before applying limit.

    complete means all attempted requests/records were usable, not exhaustive
    route coverage. query_points_count counts calls to request_json. Failures
    preserve other results and emit a generic warning without exception text.
    """
    radius = _integer_option(radius, 1, 50000, "radius")
    limit = _integer_option(limit, 1, MAX_QUERY_POINTS * MAX_POIS_PER_QUERY, "limit")
    normalized = []
    try:
        for item in samples:
            longitude, latitude = _coordinates(item["longitude"], item["latitude"])
            distance = _number(item["distance_km"])
            if distance < 0:
                raise ValueError("Negative route distance")
            normalized.append({"longitude": longitude, "latitude": latitude, "distance_km": distance})
    except (TypeError, KeyError, ValueError):
        raise ValueError("景点查询样点必须包含有效 GCJ-02 经纬度和非负累计里程。") from None
    count = min(len(normalized), MAX_QUERY_POINTS)
    indices = [round(i * (len(normalized) - 1) / (count - 1)) for i in range(count)] if count > 1 else list(range(count))
    centers = [normalized[index] for index in indices]
    distances = [center["distance_km"] for center in centers]
    result = {
        "sights": [], "query_points_count": 0, "complete": True,
        "query_radius_m": radius,
        "queried_route_km_min": min(distances) if distances else None,
        "queried_route_km_max": max(distances) if distances else None,
        "distance_note": "距离为景点到沿途查询样点的附近距离，不是到整条路线的最短距离或驾车绕行距离。",
        "coverage_note": "最多查询6个沿途样点，每点仅第一页20条；不代表完整沿线景点，查询里程范围不表示连续覆盖。",
        "category_note": "兴趣类别按高德POI类型或内置目录归类；any表示无法可靠细分，不根据景点名称猜测。",
    }
    if not centers:
        return result
    try:
        key = key_provider()
        if not isinstance(key, str) or not key.strip():
            raise ValueError("Missing key")
        key = key.strip()
    except (Exception, SystemExit):
        raise ValueError("景点查询需要可用的高德 Web 服务密钥。") from None

    unique = {}
    for center in centers:
        query = urlencode({
            "key": key,
            "location": f"{center['longitude']:.6f},{center['latitude']:.6f}",
            "types": "110000|140100", "radius": radius, "offset": MAX_POIS_PER_QUERY,
            "page": 1, "sortrule": "distance", "extensions": "base", "output": "JSON",
        })
        result["query_points_count"] += 1
        try:
            pois = _pois_from_response(request_json(AMAP_AROUND_URL + "?" + query))
        except (Exception, SystemExit):
            result["complete"] = False
            continue
        if len(pois) > MAX_POIS_PER_QUERY:
            result["complete"] = False
        for poi in pois[:MAX_POIS_PER_QUERY]:
            try:
                record = _poi_record(poi, center)
            except (TypeError, ValueError):
                result["complete"] = False
                continue
            previous = unique.get(record["id"])
            order = lambda item: (item["distance_from_route_sample_m"], item["route_km"])
            if previous is None or order(record) < order(previous):
                unique[record["id"]] = record
    result["sights"] = sorted(unique.values(), key=lambda item: (
        item["route_km"], item["distance_from_route_sample_m"], item["id"]))[:limit]
    if not result["complete"]:
        result["warning"] = _DISCOVERY_WARNING
    return result


def rank_sights(sights, origin_tuple, destination_tuple, baseline_minutes, route_provider,
                max_detour_minutes=30, visit_minutes=60, limit=3):
    """Assess the first <=5 candidates using actual via-POI driving routes.

    route_provider(origin_lng, origin_lat, destination_lng, destination_lat,
    waypoints='poiLng,poiLat') returns (polyline, kilometres, hours). All coordinates
    are GCJ-02. max_detour_minutes caps added driving, excluding the uniform
    visit_minutes estimate. total_extra_minutes includes that visit estimate.
    The fastest <=3 qualifying candidates are returned. complete concerns the
    attempted assessments; unassessed_count explicitly reports the fixed cap.

    A recalculated trip shorter than the baseline is inconsistent for this
    comparison (traffic/route choices may differ): report it as a failed estimate,
    never silently call it a zero-minute detour. No opening/parking/queuing time
    or on-site walking is included in the driving duration.
    """
    origin = _coordinates(*origin_tuple)
    destination = _coordinates(*destination_tuple)
    baseline = _number(baseline_minutes)
    max_detour = _number(max_detour_minutes)
    visit = _number(visit_minutes)
    if min(baseline, max_detour, visit) < 0:
        raise ValueError("基准时长、绕行上限和停留时间必须为非负有限数值。")
    limit = _integer_option(limit, 1, 3, "limit")
    candidates = list(sights)
    result = {
        "sights": [], "assessed_count": 0, "candidate_count": len(candidates),
        "unassessed_count": max(0, len(candidates) - MAX_DETOUR_CANDIDATES),
        "complete": True, "failures": [],
        "assessment_note": "最多对前5个候选分别重算经过景点的驾车路线；额外总时间含估算停留，不含排队、停车和入园步行。",
    }
    for candidate in candidates[:MAX_DETOUR_CANDIDATES]:
        reason = None
        try:
            longitude, latitude = _coordinates(candidate["longitude"], candidate["latitude"])
            result["assessed_count"] += 1
            _, kilometres, hours = route_provider(
                *origin, *destination, waypoints=f"{longitude:.6f},{latitude:.6f}")
            driving = _number(_number(hours) * 60)
            kilometres = _number(kilometres)
            if driving < 0 or kilometres < 0:
                raise ValueError("Negative route result")
            if driving < baseline:
                reason = "重算驾车时间低于基准，无法可靠估算绕行时间。"
            else:
                extra = driving - baseline
                if extra <= max_detour:
                    result["sights"].append({
                        **candidate, "driving_minutes": driving,
                        "extra_driving_minutes": extra, "visit_minutes": visit,
                        "total_extra_minutes": _number(extra + visit),
                        "route_distance_km": kilometres,
                    })
        except (Exception, SystemExit):
            reason = "景点驾车路线重算失败或数据无效。"
        if reason:
            result["complete"] = False
            result["failures"].append({
                "id": _text(candidate.get("id")) if isinstance(candidate, dict) else "",
                "name": _text(candidate.get("name")) if isinstance(candidate, dict) else "",
                "reason": reason,
            })
    result["sights"].sort(key=lambda item: item["total_extra_minutes"])
    result["sights"] = result["sights"][:limit]
    if not result["complete"]:
        result["warning"] = "部分候选无法可靠估算绕行时间，已单独列入失败项。"
    return result


def load_builtin_sights():
    """Read a fresh list of public addresses; callers cannot mutate future reads."""
    with Path(__file__).with_name("builtin_sights.json").open(encoding="utf-8") as stream:
        return json.load(stream)


def match_builtin_sight(name):
    """Return an exact name/alias match (case-insensitive), or None; never geocode."""
    target = name.strip().casefold()
    for record in load_builtin_sights():
        if target in [text.casefold() for text in [record["name"], *record["aliases"]]]:
            return record
    return None
