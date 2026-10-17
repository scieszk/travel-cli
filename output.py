"""Human-readable and structured presentations of travel results."""
import json
from datetime import datetime
from planning import CHINA_TZ

SOURCES = [{"name": "Open-Meteo", "url": "https://open-meteo.com/", "license": "CC BY 4.0", "license_url": "https://creativecommons.org/licenses/by/4.0/"},
           {"name": "Amap", "url": "https://lbs.amap.com/"}]


def envelope(command, data=None, error=None):
    result = {"schema_version": "1.0", "ok": error is None, "command": command,
              "queried_at": datetime.now(CHINA_TZ).isoformat(timespec="seconds"),
              "sources": [] if command in ("points", "preferences") else SOURCES}
    result["error" if error else "data"] = error if error else data
    return result


def emit_json(result):
    print(json.dumps(result, ensure_ascii=False, allow_nan=False))


def number(value, decimals=0):
    return "未知" if value is None else f"{value:.{decimals}f}"


def render(command, data):
    if command == "points":
        if "sights" in data:
            for sight in data["sights"]:
                print(f"{sight['name']} · {sight['city']} · {sight['address']}\n  地址来源：{sight['source_url']}")
            return
        print(f"共 {len(data['points'])} 个预设点位：")
        for point in data["points"]:
            print(f"  {point['name']}  {point['longitude']},{point['latitude']}  海拔 {number(point['elevation_m'])}m")
    elif command == "weather":
        print(f"📍 {data['place']}  海拔 {number(data['elevation_m'])}m")
        if "current" in data:
            now = data["current"]
            print(f"当前预报（数据时间 {now.get('time') or '未知'}）：{now['weather']} {number(now['temperature_c'])}°C，体感 {number(now['apparent_temperature_c'])}°C")
        print("日期        天气    气温°C      降水%  降水mm  雪cm  提示")
        for day in data["days"]:
            print(f"{day['date']}  {day['weather']}  {number(day['temperature_min_c'])}~{number(day['temperature_max_c'])}  "
                  f"{number(day['precipitation_probability_pct'])}  {number(day['precipitation_mm'], 1)}  {number(day['snowfall_cm'], 1)}  {day['lamp']}")
    elif command == "route":
        render_route(data)
    elif command == "compare":
        print("相同路线的出发时间比较（按北京时间；提示数量不是安全概率）")
        print("出发              到达              红色  黄色  未知  数据完整")
        for plan in data["plans"]:
            counts = plan["warning_counts"]
            print(f"{plan['departure_at'][:16]}  {plan['arrival_at'][:16]}  {counts['red']}  {counts['yellow']}  {counts['unknown']}  {plan['complete']}")
        if data["preferred_departures"]:
            print("天气提示较少的出发时刻：" + "、".join(data["preferred_departures"]))
        else:
            print("数据不完整或没有满足偏好的时刻，暂不自动选择。")
        print(data["comparison_basis"])
        if data["plans"] and "sights" in data["plans"][0]:
            render_sights(data["plans"][0]["sights"])
    elif command == "outing":
        print(f"可用 {data['available_minutes']:.0f} 分钟，预留 {data['buffer_minutes']:.0f} 分钟，找到 {len(data['plans'])} 个方案")
        for index, plan in enumerate(data["plans"], 1):
            print(f"方案 {index}：{plan['sight']['name']} · {plan['sight'].get('address', '')}")
            render_route(plan)
            print(f"含预留用时 {plan['budget_used_minutes']:.0f} 分钟；剩余 {plan['spare_minutes']:.0f} 分钟")
        print(data["planning_note"])
        if not data["complete"]:
            print("部分查询或预报不完整，请查看结构化输出中的失败项。")
    elif command == "preferences":
        for name, value in data["preferences"].items():
            print(f"{name}={str(value).lower()}")
    elif command == "watch":
        print(f"检查状态：{data['state']}；本次需要提醒：{data['notify']}")
        for change in data["changes"]:
            print(f"- {change}")
    if command in ("weather", "route", "compare", "outing"):
        print("天气数据：Open-Meteo https://open-meteo.com/ · CC BY 4.0 https://creativecommons.org/licenses/by/4.0/；天气提示为本工具的估算。")
    if "image" in data:
        print("图片：" + data["image"]["path"])
    if "image_error" in data:
        print(data["image_error"]["message"])


def render_route(data):
    print(f"🚗 {data['origin']} → {data['destination']}  {data['distance_km']:.1f}km  总计 {data['duration_minutes']:.0f} 分钟（停留 {data['stop_minutes']} 分钟）")
    print(f"出发 {data['departure_at']}  到达 {data['arrival_at']}（北京时间）")
    print("到达   日期        位置  气温°C  降水%  提示")
    for row in data["samples"]:
        print(f"{row['arrival_at'][11:16]}  {row['arrival_at'][:10]}  {row['label']}  {number(row['temperature_c'])}  "
              f"{number(row['precipitation_probability_pct'])}  {row['lamp']} {row['weather']}"
              + (f"；停留 {row['stop_minutes']} 分钟，{row['departure_at'][11:16]} 离开" if row['stop_minutes'] else ""))
    if not data["complete"]:
        print("部分天气数据缺失或查询失败，未知不代表天气良好。")
    if data.get("night_samples"):
        print(f"日出日落预报显示 {data['night_samples']} 个采样点处于夜间。")
    if not data.get("daylight_complete", True):
        print("部分日照时段数据缺失，无法完整判断是否涉及夜间驾驶。")
    if data.get("meets_preferences") is False:
        print("此时刻不满足避免夜间驾驶的偏好。")
    if "sights" in data:
        render_sights(data["sights"])


def render_sights(data):
    print("沿途景点候选（尚未加入路线）：")
    for sight in data["sights"]:
        print(f"  {sight['name']} · {sight['address']}：额外驾驶 {sight['extra_driving_minutes']:.0f} 分钟，含游玩额外 {sight['total_extra_minutes']:.0f} 分钟")
    if not data["sights"]:
        print("  本次有限查询未找到符合绕行上限的景点。")
    print(data["assessment_note"])
    print(data["discovery"].get("coverage_note", ""))
    if not data["complete"]:
        print("  部分景点查询/路线估算失败，不代表沿途没有其他景点。")
