#!/usr/bin/env python3
"""Travel weather and date-aware driving plans, using Python's standard library."""
import argparse
from datetime import datetime, timedelta
import json
import math
import os
from pathlib import Path
import re
import sys
import time
import urllib.parse
import urllib.request

from planning import CHINA_TZ, ToolError, build_schedule, haversine, hourly_index, parse_clock, parse_date, sample_route, value_at
from output import emit_json, envelope, number as number_text, render
from coordinates import gcj02_to_wgs84, wgs84_to_gcj02
from sights import match_builtin_sight, load_builtin_sights

BASE = str(Path(__file__).resolve().parent)
TZ = "Asia/Shanghai"
VERSION = "0.3.0"
_AMAP_LAST_REQUEST = None
WMO = {0: ("晴", "☀️"), 1: ("大部晴", "🌤️"), 2: ("多云", "⛅"), 3: ("阴", "☁️"),
       45: ("雾", "🌫️"), 48: ("雾凇", "🌫️"), 51: ("毛毛雨", "🌦️"), 53: ("毛毛雨", "🌦️"), 55: ("毛毛雨", "🌦️"),
       61: ("小雨", "🌧️"), 63: ("中雨", "🌧️"), 65: ("大雨", "🌧️"), 71: ("小雪", "🌨️"), 73: ("中雪", "❄️"),
       75: ("大雪", "❄️"), 77: ("雪粒", "🌨️"), 80: ("阵雨", "🌦️"), 81: ("强阵雨", "🌧️"), 82: ("暴雨", "⛈️"),
       85: ("阵雪", "🌨️"), 86: ("强阵雪", "❄️"), 95: ("雷暴", "⛈️"), 96: ("雷暴冰雹", "⛈️"), 99: ("雷暴冰雹", "⛈️")}


def load_points():
    path = Path(os.environ.get("TRAVEL_WAYPOINTS") or Path(BASE) / "waypoints.json")
    if not path.exists() and not os.environ.get("TRAVEL_WAYPOINTS"):
        path = Path(BASE) / "waypoints.example.json"
    try:
        points = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(points, dict):
            raise ValueError()
        for point in points.values():
            if not isinstance(point, dict) or not valid_coordinates(point.get("lng"), point.get("lat")):
                raise ValueError()
            if point.get("coord_system", "gcj02") not in ("gcj02", "wgs84"):
                raise ValueError()
            if point.get("elev") is not None and (not isinstance(point["elev"], (int, float)) or not math.isfinite(point["elev"])):
                raise ValueError()
        return points
    except (OSError, ValueError, TypeError):
        raise ToolError("CONFIGURATION", "点位文件无法读取或格式不正确，请参考 waypoints.example.json") from None


def valid_coordinates(lng, lat):
    return all(isinstance(v, (float, int)) and not isinstance(v, bool) and math.isfinite(v) for v in (lng, lat)) and -180 <= lng <= 180 and -90 <= lat <= 90


def get_amap_key():
    key = os.environ.get("AMAP_KEY", "").strip()
    if not key:
        path = Path(os.environ.get("AMAP_KEY_FILE") or Path(BASE) / ".secrets/amap.key")
        try:
            if path.is_file():
                key = path.read_text(encoding="utf-8").strip()
        except OSError:
            raise ToolError("CONFIGURATION", "地图密钥文件无法读取，请检查本地权限") from None
    if not key:
        raise ToolError("MISSING_CREDENTIAL", "此操作需要高德密钥：设置 AMAP_KEY，或将密钥保存至 AMAP_KEY_FILE 指定的私有文件")
    return key


def http_json(url, retry=3):
    global _AMAP_LAST_REQUEST
    endpoint = urllib.parse.urlsplit(url)
    is_amap = endpoint.hostname == "restapi.amap.com"
    for attempt in range(retry):
        try:
            if is_amap:
                if _AMAP_LAST_REQUEST is not None:
                    remaining = 1.05 - (time.monotonic() - _AMAP_LAST_REQUEST)
                    if remaining > 0:
                        time.sleep(remaining)
                _AMAP_LAST_REQUEST = time.monotonic()
            req = urllib.request.Request(url, headers={"User-Agent": "travel-cli/" + VERSION})
            with urllib.request.urlopen(req, timeout=20) as response:
                payload = json.loads(response.read().decode("utf-8-sig"))
            if is_amap and isinstance(payload, dict) and str(payload.get("infocode")) in ("10004", "10014", "10015", "10019", "10020", "10021", "10029"):
                if attempt + 1 < retry and str(payload.get("infocode")) != "10004":
                    time.sleep(1.2 * (attempt + 1))
                    continue
                raise ToolError("UPSTREAM_RATE_LIMIT", "高德接口请求频率受限，请稍后重试或减少同时运行的查询")
            return payload
        except Exception:
            if attempt + 1 < retry:
                time.sleep(0.8 * (attempt + 1))
    raise ToolError("UPSTREAM_REQUEST", f"请求失败: {endpoint.hostname}{endpoint.path}，请稍后重试")


def resolve(name, points):
    for key, value in points.items():
        if name == key or name in value.get("alias", []):
            lng, lat = value["lng"], value["lat"]
            if value.get("coord_system", "gcj02") == "wgs84":
                lng, lat = wgs84_to_gcj02(lng, lat)
            return key, lng, lat, value.get("elev")
    builtin = match_builtin_sight(name)
    query = urllib.parse.urlencode({"key": get_amap_key(), "address": builtin["address"] if builtin else name,
                                   **({"city": builtin["city"]} if builtin else {})})
    data = http_json(f"https://restapi.amap.com/v3/geocode/geo?{query}")
    if not isinstance(data, dict) or data.get("status") != "1":
        raise ToolError("UPSTREAM_RESPONSE", "高德地理编码请求未成功，请检查私有密钥和服务权限")
    if not data.get("geocodes"):
        raise ToolError("PLACE_NOT_FOUND", "找不到地点，可在私有 waypoints.json 中配置准确点位")
    try:
        lng, lat = map(float, data["geocodes"][0]["location"].split(","))
        if not valid_coordinates(lng, lat):
            raise ValueError()
    except (ValueError, KeyError, TypeError, AttributeError, IndexError):
        raise ToolError("UPSTREAM_RESPONSE", "地图接口未返回有效地点坐标") from None
    return name, lng, lat, None


def nearest_point(lng, lat, points, max_km=15):
    # Retained for callers of v0.1; route forecasts no longer borrow a nearby point's elevation.
    best = min(points, key=lambda key: haversine(lng, lat, points[key]["lng"], points[key]["lat"]), default=None)
    if best and haversine(lng, lat, points[best]["lng"], points[best]["lat"]) <= max_km:
        return best, points[best].get("elev")
    return None, None


def om_forecast(lat, lng, elev=None, days=6, hourly=False, current=False, require_hourly=True):
    lng, lat = gcj02_to_wgs84(lng, lat)
    params = {"latitude": lat, "longitude": lng, "timezone": TZ,
              "daily": "weather_code,temperature_2m_max,temperature_2m_min,precipitation_probability_max,precipitation_sum,snowfall_sum,sunrise,sunset",
              "forecast_days": days}
    if hourly:
        params["hourly"] = "temperature_2m,precipitation_probability,precipitation,weather_code,snowfall"
    if current:
        params["current"] = "temperature_2m,apparent_temperature,weather_code,is_day,relative_humidity_2m,wind_speed_10m"
    if elev is not None:
        params["elevation"] = elev
    data = http_json("https://api.open-meteo.com/v1/forecast?" + urllib.parse.urlencode(params))
    if not isinstance(data, dict) or data.get("error") or not isinstance(data.get("daily"), dict) or (hourly and require_hourly and not isinstance(data.get("hourly"), dict)):
        raise ToolError("UPSTREAM_RESPONSE", "天气接口未返回有效预报，请稍后重试")
    return data


def risk_lamp(code, tmin, tmax, prob, snow):
    if snow is not None and snow >= 0.5:
        return "🔴"
    if snow is not None and snow > 0:
        return "🟡"
    if prob is not None and prob >= 60 and tmin is not None and tmin <= 1:
        return "🔴"
    if (prob is not None and prob >= 60) or (tmin is not None and tmin <= 0) or code in (45, 48, 65, 71, 73, 75, 77, 82, 85, 86, 95, 96, 99):
        return "🟡"
    if tmin is None or prob is None or snow is None or code not in WMO:
        return "⚪"
    return "🟢"


def present(command, data, args):
    if getattr(args, "structured", False):
        emit_json(envelope(command, data))
    else:
        render(command, data)
    return data


def cmd_weather(args):
    name, lng, lat, elevation = resolve(args.place, load_points())
    image_path = getattr(args, "image", None)
    detail = bool(image_path or getattr(args, "details", False))
    forecast = om_forecast(lat, lng, elevation, days=max(2, args.days), hourly=True, current=True, require_hourly=False) if detail else om_forecast(lat, lng, elevation, days=args.days)
    daily = forecast["daily"]
    if not isinstance(daily.get("time"), list) or not daily["time"]:
        raise ToolError("UPSTREAM_RESPONSE", "天气接口未返回有效的每日预报")
    days = []
    for index, day in enumerate(daily["time"][:args.days]):
        code = value_at(daily, "weather_code", index)
        low, high = value_at(daily, "temperature_2m_min", index), value_at(daily, "temperature_2m_max", index)
        prob, snow = value_at(daily, "precipitation_probability_max", index), value_at(daily, "snowfall_sum", index)
        days.append({"date": day, "weather_code": code, "weather": WMO.get(code, ("未知", ""))[0],
                     "temperature_min_c": low, "temperature_max_c": high, "precipitation_probability_pct": prob,
                     "precipitation_mm": value_at(daily, "precipitation_sum", index), "snowfall_cm": snow,
                     "lamp": risk_lamp(code, low, high, prob, snow)})
    data = {"place": name, "elevation_m": forecast.get("elevation"), "days": days}
    if detail:
        from weather_detail import add_details
        add_details(data, forecast, datetime.now(CHINA_TZ), WMO)
    if image_path:
        from image_support import attach_image
        attach_image(data, "weather", data, image_path)
    return present("weather", data, args)


def amap_route(o_lng, o_lat, d_lng, d_lat, waypoints=""):
    query = {"key": get_amap_key(), "origin": f"{o_lng},{o_lat}", "destination": f"{d_lng},{d_lat}", "strategy": 0}
    if waypoints:
        query["waypoints"] = waypoints
    data = http_json("https://restapi.amap.com/v3/direction/driving?" + urllib.parse.urlencode(query))
    if not isinstance(data, dict) or data.get("status") != "1":
        raise ToolError("UPSTREAM_RESPONSE", "高德路线请求未成功，请检查私有密钥和服务权限")
    try:
        path = data["route"]["paths"][0]
        points = []
        for step in path.get("steps", []):
            for segment in step.get("polyline", "").split(";"):
                lng, lat = map(float, segment.split(","))
                if not valid_coordinates(lng, lat):
                    raise ValueError()
                points.append((lng, lat))
        return points, float(path["distance"]) / 1000, float(path["duration"]) / 3600
    except (ValueError, TypeError, KeyError, IndexError, AttributeError):
        raise ToolError("UPSTREAM_RESPONSE", "地图接口未返回有效的路线数据") from None


def prepare_schedule(args):
    points = load_points()
    points.update(getattr(args, "waypoint_overrides", {}) or {})
    places = [resolve(name, points) for name in [args.origin, *(args.via or []), args.dest]]
    stops = {}
    for item in getattr(args, "stop", []) or []:
        place, minutes = item.rsplit("=", 1)
        stops[(args.via or []).index(place)] = int(minutes)
    legs = []
    for origin, destination in zip(places, places[1:]):
        line, distance, hours = amap_route(origin[1], origin[2], destination[1], destination[2])
        legs.append({"origin": origin, "destination": destination, "points": line,
                     "distance_km": distance, "duration_hours": hours})
    schedule = build_schedule(legs, stops, args.speed, args.step)
    distance = 0
    via_positions = []
    for leg, place in zip(legs[:-1], places[1:-1]):
        distance += leg["distance_km"]
        via_positions.append((distance, place))
    schedule.update(origin=places[0][0], destination=places[-1][0], places=places, via_positions=via_positions)
    return schedule


def forecast_plan(schedule, departure, forecast_days, cache, avoid_night=False):
    rows = []
    counts = {"red": 0, "yellow": 0, "green": 0, "unknown": 0}
    for sample in schedule["samples"]:
        arrival = departure + timedelta(hours=sample["elapsed_hours"])
        row = {key: value for key, value in sample.items() if key != "elapsed_hours"}
        row.update(arrival_at=arrival.isoformat(timespec="seconds"),
                   departure_at=(arrival + timedelta(minutes=sample["stop_minutes"])).isoformat(timespec="seconds"),
                   temperature_c=None, precipitation_probability_pct=None, snowfall_cm=None, weather_code=None,
                   weather="未知", lamp="⚪", weather_status="unavailable", daylight=None, daylight_warning=None, departure_daylight=None)
        key = (round(sample["latitude"], 6), round(sample["longitude"], 6), sample["elevation_m"], forecast_days)
        if key not in cache:
            try:
                cache[key] = om_forecast(key[0], key[1], key[2], days=forecast_days, hourly=True)
            except SystemExit:
                cache[key] = None
        forecast = cache[key]
        if forecast is not None:
            from journey import daylight_at
            row["daylight"] = daylight_at(forecast.get("daily", {}), arrival)
            departure_daylight = daylight_at(forecast.get("daily", {}), arrival + timedelta(minutes=sample["stop_minutes"]))
            row["departure_daylight"] = departure_daylight
            # A via-point represents both arrival and resumption of driving.
            row["daylight"] = False if row["daylight"] is False or departure_daylight is False else None if row["daylight"] is None or departure_daylight is None else True
            row["daylight_warning"] = "night" if row["daylight"] is False else None
            hourly = forecast.get("hourly", {})
            try:
                index = hourly_index(hourly.get("time", []), arrival)
                code = value_at(hourly, "weather_code", index)
                temp = value_at(hourly, "temperature_2m", index)
                prob = value_at(hourly, "precipitation_probability", index)
                snow = value_at(hourly, "snowfall", index)
                complete = code in WMO and all(value is not None for value in (temp, prob, snow))
                row.update(weather_code=code, weather=WMO.get(code, ("未知", ""))[0], temperature_c=temp,
                           precipitation_probability_pct=prob, snowfall_cm=snow, elevation_m=forecast.get("elevation"),
                           lamp=risk_lamp(code, temp, temp, prob, snow), weather_status="available" if complete else "incomplete")
            except ToolError as error:
                if error.error_code == "FORECAST_RANGE":
                    raise
        if row["weather_status"] != "available":
            counts["unknown"] += 1
        counts[{"🔴": "red", "🟡": "yellow", "🟢": "green", "⚪": "unknown"}[row["lamp"]]] += int(row["lamp"] != "⚪")
        rows.append(row)
    nights = [row["label"] for row in rows if row["daylight"] is False]
    daylight_complete = all(row["daylight"] is not None for row in rows)
    return {"origin": schedule["origin"], "destination": schedule["destination"],
            "departure_at": departure.isoformat(timespec="seconds"),
            "arrival_at": (departure + timedelta(hours=schedule["duration_hours"])).isoformat(timespec="seconds"),
            "distance_km": round(schedule["distance_km"], 3), "duration_minutes": round(schedule["duration_hours"] * 60, 2),
            "stop_minutes": schedule["stop_minutes"], "complete": counts["unknown"] == 0,
            "warning_counts": counts, "samples": rows, "coordinate_system": "gcj02",
            "night_samples": len(nights), "daylight_warnings": nights, "daylight_complete": daylight_complete,
            "meets_preferences": (False if nights else True if daylight_complete else None) if avoid_night else True}


def cmd_route(args, emit=True):
    from journey import finish, route_sights, snapshot_options
    schedule = prepare_schedule(args)
    today = datetime.now(CHINA_TZ).date()
    day = parse_date(getattr(args, "date", None) or today.isoformat(), today)
    clocks = getattr(args, "departures", None) or [args.depart]
    departures = [datetime.combine(day, parse_clock(clock), CHINA_TZ) for clock in clocks]
    last_arrival = max(departures) + timedelta(hours=schedule["duration_hours"])
    forecast_days = ((last_arrival + timedelta(minutes=30)).date() - today).days + 1
    if forecast_days > 16:
        raise ToolError("FORECAST_RANGE", "预计到达时间超出最多 16 天的预报范围，请调整日期或路线")
    cache = {}
    plans = [forecast_plan(schedule, departure, forecast_days, cache, getattr(args, "avoid_night_driving", False)) for departure in departures]
    for plan in plans:
        plan["watch_options"] = snapshot_options(args, schedule["places"], plan)
    if getattr(args, "sights", False):
        recommendations = route_sights(sys.modules[__name__], args, schedule)
        for plan in plans:
            plan["sights"] = recommendations
    if getattr(args, "cmd", "route") != "compare":
        return finish(sys.modules[__name__], "route", plans[0], args) if emit else plans[0]
    complete = all(plan["complete"] and plan["meets_preferences"] is not None for plan in plans)
    scores = [(plan["warning_counts"]["red"], plan["warning_counts"]["yellow"]) for plan in plans]
    eligible = [score for plan, score in zip(plans, scores) if plan["meets_preferences"]]
    preferred = [plan["departure_at"] for plan, score in zip(plans, scores) if complete and eligible and plan["meets_preferences"] and score == min(eligible)]
    data = {"plans": plans, "comparison_complete": complete, "preferred_departures": preferred,
            "comparison_basis": "同一组路线采样点，优先比较红色提示点数，再比较黄色点数；并列全部保留。数据缺失时不自动选择。提示不代表道路通行或驾驶安全结论。"}
    return finish(sys.modules[__name__], "compare", data, args) if emit else data


class Parser(argparse.ArgumentParser):
    def error(self, message):
        raise ToolError("INVALID_ARGUMENT", "命令参数无效，请查看相应命令的 --help", 2)


def main():
    import journey
    raw = sys.argv[1:]
    structured = "--structured" in raw or "--json" in raw
    command = next((word for word in raw if word in ("points", "weather", "route", "compare", "outing", "preferences", "watch")), "unknown")
    parser = Parser(prog="travel", description="旅行天气与路线时间规划 v" + VERSION)
    parser.add_argument("--version", action="version", version=VERSION)
    parser.add_argument("--structured", "--json", action="store_true")
    sub = parser.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("points", help="列出本地预设点位")
    p.add_argument("--sights", action="store_true", help="列出内置公共景点地址与来源")
    w = sub.add_parser("weather", help="单点每日天气")
    w.add_argument("--image", metavar="FILE.png", help="生成天气图片；失败时保留文字/结构化天气")
    w.add_argument("--details", action="store_true", help="同时查询当前预报和未来逐小时天气")
    w.add_argument("place")
    w.add_argument("--days", type=int, default=6)
    parsers = [p, w]
    for name in ("route", "compare"):
        route = sub.add_parser(name, help="路线逐点天气" if name == "route" else "比较不同出发时间的沿途天气")
        route.add_argument("origin")
        route.add_argument("dest")
        route.add_argument("--date", help="北京时间 YYYY-MM-DD，默认今天")
        route.add_argument("--via", nargs="+", help="按行驶顺序指定途经点，最多 10 个")
        route.add_argument("--stop", action="append", default=[], help="在途经点停留，例如 --via 济南 --stop 济南=45")
        route.add_argument("--speed", type=float, default=0, help="公里/小时；0 使用各路段的地图参考时长")
        route.add_argument("--step", type=int, default=40, help="采样间距公里，默认 40，最多共 200 点")
        if name == "route":
            route.add_argument("--depart", default="08:00", help="HH:MM，默认 08:00")
        else:
            route.add_argument("--departures", nargs="+", required=True, help="2–6 个不同的 HH:MM 出发时间")
        journey.add_plan_options(route)
        route.add_argument("--sights", action="store_true", help="查询附近景点并重算真实驾车绕行时间")
        route.add_argument("--radius", type=int, default=3000, help="沿途样点的景点查询半径米，1–5000")
        parsers.append(route)
    journey.add_parsers(sub, parsers)
    for child in parsers:
        child.add_argument("--structured", "--json", action="store_true", default=argparse.SUPPRESS, help="输出含数据来源和查询时间的 JSON；失败时也输出 JSON")
    try:
        args = parser.parse_args()
        journey.validate(args)
        if args.cmd == "weather" and not 1 <= args.days <= 16:
            raise ToolError("INVALID_ARGUMENT", "--days 必须在 1 到 16 之间", 2)
        if args.cmd in ("route", "compare"):
            if not 1 <= args.step <= 1000 or not math.isfinite(args.speed) or not 0 <= args.speed <= 200:
                raise ToolError("INVALID_ARGUMENT", "--step 须为 1–1000 公里；--speed 须为 0–200 的有限数值", 2)
            today = datetime.now(CHINA_TZ).date()
            parse_date(args.date or today.isoformat(), today)
            clocks = args.departures if args.cmd == "compare" else [args.depart]
            if args.cmd == "compare" and not 2 <= len(set(clocks)) == len(clocks) <= 6:
                raise ToolError("INVALID_ARGUMENT", "--departures 需要 2–6 个不同的出发时刻", 2)
            for clock in clocks:
                parse_clock(clock)
            via = args.via or []
            if len(via) > 10 or len(set(via)) != len(via):
                raise ToolError("INVALID_ARGUMENT", "途经点最多 10 个，且不能重复", 2)
            seen = set()
            for item in args.stop:
                match = re.fullmatch(r"(.+)=(\d{1,4})", item)
                if not match or match[1] not in via or not 0 <= int(match[2]) <= 1440 or match[1] in seen:
                    raise ToolError("INVALID_ARGUMENT", "--stop 必须为途经点名称=停留分钟，分钟范围 0–1440，每个途经点只设置一次", 2)
                seen.add(match[1])
        if args.cmd == "points":
            if args.sights:
                present("points", {"sights": load_builtin_sights()}, args)
            else:
                points = [{"name": key, "longitude": value["lng"], "latitude": value["lat"], "elevation_m": value.get("elev"), "aliases": value.get("alias", []), "coordinate_system": value.get("coord_system", "gcj02")} for key, value in load_points().items()]
                present("points", {"points": points}, args)
        elif args.cmd == "weather":
            cmd_weather(args)
        elif args.cmd in ("route", "compare"):
            cmd_route(args)
        else:
            journey.execute(sys.modules[__name__], args)
    except ToolError as error:
        if structured:
            emit_json(envelope(command, error={"code": error.error_code, "message": str(error)}))
        else:
            print(str(error), file=sys.stderr)
        raise SystemExit(error.status) from None
    except (OSError, ValueError, TypeError, KeyError, IndexError, AttributeError):
        message = "配置或上游数据格式异常，请检查配置并稍后重试"
        if structured:
            emit_json(envelope(command, error={"code": "INVALID_DATA", "message": message}))
        else:
            print(message, file=sys.stderr)
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
