"""Private preferences, bounded sightseeing plans, cards and change checks."""
import argparse
from datetime import datetime, timedelta
import math
from types import SimpleNamespace

from planning import CHINA_TZ, ToolError, build_schedule, parse_clock, parse_date
from sights import discover_sights, rank_sights
import travel_store as store


def add_plan_options(parser):
    parser.add_argument("--max-detour", type=int, dest="max_detour_minutes", help="景点额外驾车分钟上限，默认读取私有偏好或30")
    parser.add_argument("--visit-minutes", type=int, help="景点预计停留分钟，默认读取私有偏好或60")
    parser.add_argument("--buffer-minutes", type=int, help="小旅行预留分钟，默认读取私有偏好或30")
    parser.add_argument("--interests", choices=("any", "nature", "culture"))
    night = parser.add_mutually_exclusive_group()
    night.add_argument("--avoid-night-driving", dest="avoid_night_driving", action="store_true", default=None)
    night.add_argument("--allow-night-driving", dest="avoid_night_driving", action="store_false")
    parser.add_argument("--card", action="store_true", help="输出可复制的 Markdown 行程卡片")
    parser.add_argument("--select", type=int, default=1, help="卡片/保存检查所选方案序号，从1开始")
    parser.add_argument("--save-watch", metavar="FILE", help="保存私有行程基准，自动使用 .watch.json 后缀")


def add_parsers(sub, parsers):
    outing = sub.add_parser("outing", help="按总时间预算筛选往返小旅行")
    outing.add_argument("origin")
    outing.add_argument("--date")
    outing.add_argument("--depart", default="09:00")
    budget = outing.add_mutually_exclusive_group(required=True)
    budget.add_argument("--hours", type=float, help="包括往返、游玩和预留的总小时数，最多24")
    budget.add_argument("--return-by", help="同一天的最晚返回时刻 HH:MM")
    outing.add_argument("--radius", type=int, default=20000, help="起点附近搜索半径米，最多50000")
    outing.add_argument("--step", type=int, default=40)
    add_plan_options(outing)
    pref = sub.add_parser("preferences", help="查看或设置私有偏好")
    pref.add_argument("action", choices=("show", "set", "reset"))
    pref.add_argument("updates", nargs="*", metavar="key=value")
    watch = sub.add_parser("watch", help="检查已保存行程的变化；不发送消息、不创建定时任务")
    watch.add_argument("action", choices=("check",))
    watch.add_argument("path")
    parsers.extend([outing, pref, watch])


def validate(args):
    if args.cmd in ("route", "compare", "outing"):
        if args.card and args.structured:
            raise ToolError("INVALID_ARGUMENT", "--card 和 --structured 请选择一种输出格式", 2)
        if args.select < 1:
            raise ToolError("INVALID_ARGUMENT", "--select 必须从1开始", 2)
        prefs = store.load_preferences()
        for name, default in prefs.items():
            if getattr(args, name, None) is None:
                setattr(args, name, default)
        for name, maximum in (("max_detour_minutes", 240), ("visit_minutes", 1440), ("buffer_minutes", 240)):
            if not 0 <= getattr(args, name) <= maximum:
                raise ToolError("INVALID_ARGUMENT", "绕行、停留或预留分钟超出允许范围", 2)
        if not 1 <= args.radius <= (50000 if args.cmd == "outing" else 5000):
            raise ToolError("INVALID_ARGUMENT", "景点查询半径超出允许范围", 2)
    if args.cmd == "outing":
        parse_date(args.date or datetime.now(CHINA_TZ).date().isoformat(), datetime.now(CHINA_TZ).date())
        start = parse_clock(args.depart)
        if not 1 <= args.step <= 1000:
            raise ToolError("INVALID_ARGUMENT", "--step 须为1–1000公里", 2)
        if args.hours is not None and (not math.isfinite(args.hours) or not 0 < args.hours <= 24):
            raise ToolError("INVALID_ARGUMENT", "--hours 须大于0且不超过24", 2)
        if args.return_by and parse_clock(args.return_by) <= start:
            raise ToolError("INVALID_ARGUMENT", "--return-by 须晚于同日出发时刻；跨日请使用 --hours", 2)
    if args.cmd == "preferences" and ((args.action == "set") != bool(args.updates)):
        raise ToolError("INVALID_ARGUMENT", "set 需要 key=value；show/reset 不接收设置项", 2)


def daylight_at(daily, arrival):
    try:
        index = daily["time"].index(arrival.date().isoformat())
        sunrise, sunset = (datetime.fromisoformat(daily[key][index]) for key in ("sunrise", "sunset"))
        sunrise, sunset = (value.replace(tzinfo=CHINA_TZ) if value.tzinfo is None else value.astimezone(CHINA_TZ) for value in (sunrise, sunset))
        if sunrise.date() != arrival.date() or sunset.date() != arrival.date() or sunrise >= sunset:
            return None
        return sunrise <= arrival < sunset
    except (KeyError, ValueError, TypeError, IndexError, AttributeError):
        return None


def snapshot_options(args, places, plan):
    names = [args.origin, *(args.via or []), args.dest]
    overrides = {name: {"lng": place[1], "lat": place[2], "elev": place[3], "coord_system": "gcj02"}
                 for name, place in zip(names, places)}
    return {"origin": args.origin, "dest": args.dest, "via": args.via or [], "stop": getattr(args, "stop", []) or [],
            "date": plan["departure_at"][:10], "depart": plan["departure_at"][11:16],
            "speed": args.speed, "step": args.step, "waypoint_overrides": overrides}


def route_sights(api, args, schedule):
    found = discover_sights(schedule["samples"], lambda url: api.http_json(url, retry=1), api.get_amap_key, radius=args.radius, limit=20)
    candidates = [p for p in found["sights"] if args.interests == "any" or p.get("category") == args.interests]
    origin, destination = schedule["places"][0], schedule["places"][-1]
    def via_route(a, b, c, d, waypoints):
        poi = next(p for p in candidates if f"{p['longitude']:.6f},{p['latitude']:.6f}" == waypoints)
        ordered = [(km, f"{place[1]},{place[2]}") for km, place in schedule["via_positions"]]
        ordered.append((poi["route_km"], waypoints))
        ordered.sort(key=lambda item: item[0])
        # Match baseline speed override on the recalculated path as well.
        line, km, hours = api.amap_route(a, b, c, d, waypoints=";".join(item[1] for item in ordered))
        return line, km, km / args.speed if args.speed else hours
    ranked = rank_sights(candidates, origin[1:3], destination[1:3],
                         schedule["duration_hours"] * 60 - schedule["stop_minutes"], via_route,
                         args.max_detour_minutes, args.visit_minutes)
    return {**ranked, "discovery": {k: v for k, v in found.items() if k != "sights"},
            "complete": found["complete"] and ranked["complete"]}


def finish(api, command, data, args):
    plans = data.get("plans", [data])
    if getattr(args, "card", False) or getattr(args, "save_watch", None):
        if args.select > len(plans):
            raise ToolError("NO_PLAN", "没有对应序号的可用方案，无法生成卡片或保存检查", 2)
        selected = plans[args.select - 1]
        if args.save_watch:
            path = store.save_watch(args.save_watch, selected, selected["watch_options"])
            data["saved_watch"] = str(path)
        if args.card:
            print(store.render_card(selected))
            return data
    return api.present(command, data, args)


def outing(api, args):
    origin = api.resolve(args.origin, api.load_points())
    day = parse_date(args.date or datetime.now(CHINA_TZ).date().isoformat(), datetime.now(CHINA_TZ).date())
    departure = datetime.combine(day, parse_clock(args.depart), CHINA_TZ)
    available = args.hours * 60 if args.hours is not None else (datetime.combine(day, parse_clock(args.return_by), CHINA_TZ) - departure).total_seconds() / 60
    found = discover_sights([{"longitude": origin[1], "latitude": origin[2], "distance_km": 0}],
                            lambda url: api.http_json(url, retry=1), api.get_amap_key, radius=args.radius, limit=20)
    candidates = [p for p in found["sights"] if args.interests == "any" or p.get("category") == args.interests]
    feasible, failures, rejected = [], [], 0
    for poi in candidates[:5]:
        try:
            destination = (poi["name"], poi["longitude"], poi["latitude"], None)
            legs = []
            for start, end in ((origin, destination), (destination, origin)):
                line, km, hours = api.amap_route(*start[1:3], *end[1:3])
                legs.append({"origin": start, "destination": end, "points": line, "distance_km": km, "duration_hours": hours})
            schedule = build_schedule(legs, {0: args.visit_minutes}, step=args.step)
            if schedule["duration_hours"] * 60 + args.buffer_minutes > available:
                rejected += 1
                continue
            schedule.update(origin=origin[0], destination=origin[0], places=[origin, destination, origin])
            feasible.append((schedule, poi))
        except ToolError:
            failures.append({"name": poi["name"], "reason": "无法取得有效的往返驾车路线"})
    feasible.sort(key=lambda item: item[0]["duration_hours"])
    plans, cache = [], {}
    for schedule, poi in feasible:
        if len(plans) >= 3:
            break
        days = (departure + timedelta(hours=schedule["duration_hours"], minutes=30)).date() - datetime.now(CHINA_TZ).date()
        if days.days >= 16:
            failures.append({"name": poi["name"], "reason": "返回时间超出预报范围"})
            continue
        plan = api.forecast_plan(schedule, departure, days.days + 1, cache, args.avoid_night_driving)
        if plan["meets_preferences"] is False:
            rejected += 1
            continue
        route_args = SimpleNamespace(origin=args.origin, dest=args.origin, via=[poi["name"]], stop=[f"{poi['name']}={args.visit_minutes}"], speed=0, step=args.step)
        plan.update(sight=poi, budget_used_minutes=schedule["duration_hours"] * 60 + args.buffer_minutes,
                    buffer_minutes=args.buffer_minutes, spare_minutes=available - schedule["duration_hours"] * 60 - args.buffer_minutes)
        plan["watch_options"] = snapshot_options(route_args, schedule["places"], plan)
        plans.append(plan)
    data = {"plans": plans, "available_minutes": available, "buffer_minutes": args.buffer_minutes,
            "assessed_count": min(5, len(candidates)), "unassessed_count": max(0, len(candidates) - 5),
            "rejected_count": rejected, "failures": failures, "discovery": {k: v for k, v in found.items() if k != "sights"},
            "complete": found["complete"] and not failures and all(p["complete"] and p["meets_preferences"] is not None for p in plans),
            "planning_note": "往返驾车、预计游玩和预留时间均计入预算；按总耗时排序，最多评估5处、展示3处。营业、预约、停车及实时交通需出发前确认。"}
    return finish(api, "outing", data, args)


def execute(api, args):
    if args.cmd == "preferences":
        if args.action == "set":
            result = store.save_preferences(store.parse_updates(args.updates))
        elif args.action == "reset":
            result = store.reset_preferences()
        else:
            result = store.load_preferences()
        return api.present("preferences", {"preferences": result}, args)
    if args.cmd == "outing":
        return outing(api, args)
    document = store.load_watch(args.path)
    options = document["options"]
    route_args = SimpleNamespace(**options, cmd="route", sights=False, avoid_night_driving=False)
    try:
        current = api.cmd_route(route_args, emit=False)
        result = store.compare_watch(document["baseline"], current)
    except ToolError:
        result = {"state": "unavailable", "changes": ["当前路线或天气无法重新查询"], "notify": True}
    result = store.record_watch_check(args.path, result)
    api.present("watch", result, args)
    if result["state"] == "unavailable":
        raise SystemExit(1)
    return result
