#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
travel-cli — 自驾旅行决策工具（旅游模块 v1）
子命令：
  points                        列出预设点位
  weather <地点> [--days N]     单点天气预报（海拔订正）
  route  <起> <终> [--depart HH:MM] [--speed KMH]  沿途天气时间轴
仅依赖 Python3 标准库。
"""
import argparse
import json
import math
import os
from pathlib import Path
import time
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

BASE = os.path.dirname(os.path.abspath(__file__))
TZ = "Asia/Shanghai"
CHINA_TZ = timezone(timedelta(hours=8))

WMO = {
    0:("晴","☀️"),1:("大部晴","🌤️"),2:("多云","⛅"),3:("阴","☁️"),
    45:("雾","🌫️"),48:("雾凇","🌫️"),51:("毛毛雨","🌦️"),53:("毛毛雨","🌦️"),55:("毛毛雨","🌦️"),
    61:("小雨","🌧️"),63:("中雨","🌧️"),65:("大雨","🌧️"),
    71:("小雪","🌨️"),73:("中雪","❄️"),75:("大雪","❄️"),77:("雪粒","🌨️"),
    80:("阵雨","🌦️"),81:("强阵雨","🌧️"),82:("暴雨","⛈️"),
    85:("阵雪","🌨️"),86:("强阵雪","❄️"),95:("雷暴","⛈️"),96:("雷暴冰雹","⛈️"),99:("雷暴冰雹","⛈️"),
}

# ---------- 基础工具 ----------
def load_points():
    path = Path(os.environ.get("TRAVEL_WAYPOINTS") or Path(BASE) / "waypoints.json")
    if not path.exists() and not os.environ.get("TRAVEL_WAYPOINTS"):
        path = Path(BASE) / "waypoints.example.json"
    with path.open(encoding="utf-8") as f:
        return json.load(f)

def get_amap_key():
    """Read the map key from the environment or a local ignored secret file."""
    key = os.environ.get("AMAP_KEY", "").strip()
    if not key:
        path = Path(os.environ.get("AMAP_KEY_FILE") or Path(BASE) / ".secrets/amap.key")
        if path.is_file():
            key = path.read_text(encoding="utf-8").strip()
    if not key:
        raise SystemExit("此操作需要高德密钥：设置 AMAP_KEY，或将密钥保存至 AMAP_KEY_FILE 指定的私有文件。")
    return key

def http_json(url, retry=3):
    last = None
    for i in range(retry):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "travel-cli/1.0"})
            with urllib.request.urlopen(req, timeout=20) as response:
                return json.loads(response.read().decode("utf-8-sig"))
        except Exception as e:
            last = e
            if i + 1 < retry:
                time.sleep(0.8 * (i + 1))
    endpoint = urllib.parse.urlsplit(url)
    # Exceptions can echo the entire request URL, including keys and private places.
    raise SystemExit(f"请求失败: {endpoint.hostname}{endpoint.path}（{type(last).__name__}）")

def resolve(name, points):
    """名称→(显示名, lng, lat, elev)。先查预设（含别名），再走高德地理编码。"""
    for k, v in points.items():
        if name == k or name in v.get("alias", []):
            return k, v["lng"], v["lat"], v["elev"]
    query = urllib.parse.urlencode({"key": get_amap_key(), "address": name})
    d = http_json(f"https://restapi.amap.com/v3/geocode/geo?{query}")
    if d.get("status") == "0":
        raise SystemExit("高德地理编码请求未成功，请检查私有密钥和服务权限。")
    geo = d.get("geocodes") or []
    if not geo:
        raise SystemExit(f"找不到地点：{name}（可在 waypoints.json 里添加）")
    lng, lat = map(float, geo[0]["location"].split(","))
    return name, lng, lat, None  # elev=None → 让 Open-Meteo 用地形模型

def haversine(lng1, lat1, lng2, lat2):
    R = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lng2 - lng1)
    a = math.sin(dp/2)**2 + math.cos(p1)*math.cos(p2)*math.sin(dl/2)**2
    return 2 * R * math.asin(math.sqrt(a))

def cjk_pad(s, width):
    w = sum(2 if ord(c) > 127 else 1 for c in s)
    return s + " " * max(0, width - w)

def nearest_point(lng, lat, points, max_km=15):
    best, bd = None, 1e9
    for k, v in points.items():
        d = haversine(lng, lat, v["lng"], v["lat"])
        if d < bd: best, bd = k, d
    return (best, points[best]["elev"]) if bd <= max_km else (None, None)

# ---------- 原子能力：天气 ----------
def om_forecast(lat, lng, elev=None, days=6, hourly=False):
    p = {
        "latitude": lat, "longitude": lng, "timezone": TZ,
        "daily": "weather_code,temperature_2m_max,temperature_2m_min,precipitation_probability_max,precipitation_sum,snowfall_sum",
        "forecast_days": days,
    }
    if hourly:
        p["hourly"] = "temperature_2m,precipitation_probability,precipitation,weather_code,snowfall"
        p["forecast_days"] = min(days, 4)
    if elev is not None: p["elevation"] = elev
    d = http_json("https://api.open-meteo.com/v1/forecast?" + urllib.parse.urlencode(p))
    if d.get("error") or "daily" not in d:
        raise SystemExit("天气接口未返回有效预报，请稍后重试。")
    return d

def risk_lamp(code, tmin, tmax, prob, snow):
    if snow is not None and snow >= 0.5: return "🔴"   # ≥0.5cm 才算威胁（轿车无链）
    if snow is not None and snow > 0: return "🟡"        # 雪籽/微量：氛围组
    if prob is not None and prob >= 60 and (tmin is not None and tmin <= 1): return "🔴"  # 降水+冰点=暗冰
    if prob is not None and prob >= 60: return "🟡"
    if tmin is not None and tmin <= 0: return "🟡"
    if code in (65, 71, 73, 75, 77, 82, 85, 86, 95, 96, 99): return "🟡"
    if tmin is None or prob is None or snow is None or code is None: return "⚪"
    return "🟢"

def number_text(value, decimals=0):
    """Render missing observations explicitly instead of inventing numeric values."""
    return "未知" if value is None else f"{value:.{decimals}f}"

def cmd_weather(a):
    points = load_points()
    name, lng, lat, elev = resolve(a.place, points)
    d = om_forecast(lat, lng, elev, days=a.days)
    da = d["daily"]
    print(f"\n📍 {name}  (海拔 {d.get('elevation','?')}m)\n")
    print(f"{'日期':<12}{'天气':<10}{'气温':<12}{'降水%':<8}{'量级mm':<8}{'雪cm':<7}灯")
    for i, day in enumerate(da["time"]):
        code = da["weather_code"][i]; txt, ico = WMO.get(code, ("?", "❓"))
        tmax, tmin = da["temperature_2m_max"][i], da["temperature_2m_min"][i]
        prob = da["precipitation_probability_max"][i]; pr = da["precipitation_sum"][i]
        snow = da.get("snowfall_sum", [None]*len(da["time"]))[i]
        lamp = risk_lamp(code, tmin, tmax, prob, snow)
        snow_s = number_text(snow, 1)
        temp = f"{number_text(tmin)}~{number_text(tmax)}°C"
        rain = f"{number_text(prob)}%" if prob is not None else "未知"
        print(f"{day:<12}{ico}{txt:<8}{temp:<12}{rain:<7}{number_text(pr, 1):<8}{snow_s:<7}{lamp}")
    print()

# ---------- 组合能力：沿途天气时间轴 ----------
def amap_route(o_lng, o_lat, d_lng, d_lat, waypoints=""):
    q = {"key": get_amap_key(), "origin": f"{o_lng},{o_lat}",
         "destination": f"{d_lng},{d_lat}", "strategy": 0}
    if waypoints: q["waypoints"] = waypoints
    d = http_json(f"https://restapi.amap.com/v3/direction/driving?{urllib.parse.urlencode(q)}")
    if d.get("status") == "0":
        raise SystemExit("高德路线请求未成功，请检查私有密钥和服务权限。")
    path = (d.get("route", {}).get("paths") or [None])[0]
    if not path: raise SystemExit("高德未返回路线")
    pts = []
    for st in path.get("steps", []):
        for seg in st.get("polyline", "").split(";"):
            try:
                x, y = seg.split(","); pts.append((float(x), float(y)))
            except ValueError: pass
    return pts, float(path["distance"]) / 1000.0, float(path["duration"]) / 3600.0

def sample_route(pts, step_km=40):
    """沿折线每 step_km 采一点，返回 [(lng,lat,累计里程km)]，含起终点"""
    if step_km <= 0: raise ValueError("采样间隔必须大于 0")
    if not pts: return []
    out, acc, prev = [(pts[0][0], pts[0][1], 0.0)], 0.0, pts[0]
    for p in pts[1:]:
        d = haversine(prev[0], prev[1], p[0], p[1]); acc += d; prev = p
        if acc >= step_km * len(out):
            out.append((p[0], p[1], acc))
    if out[-1][:2] != pts[-1]:
        total = acc
        out.append((pts[-1][0], pts[-1][1], total))
    return out

def cmd_route(a):
    points = load_points()
    o_name, o_lng, o_lat, _ = resolve(a.origin, points)
    d_name, d_lng, d_lat, _ = resolve(a.dest, points)
    via_pts, via_names = [], []
    for v in (a.via or []):
        vn, vlng, vlat, _ = resolve(v, points)
        via_pts.append(f"{vlng},{vlat}"); via_names.append(vn)
    pts, dist_km, amap_h = amap_route(o_lng, o_lat, d_lng, d_lat, ";".join(via_pts))
    # 用高德时长修正均速；用户可覆盖
    speed = a.speed or (dist_km / amap_h if amap_h > 0 else 60)
    today = datetime.now(CHINA_TZ).date()
    depart = datetime.combine(today, datetime.strptime(a.depart, "%H:%M").time(), CHINA_TZ)
    samples = sample_route(pts, step_km=a.step)
    via_s = f"（途经：{'→'.join(via_names)}）" if via_names else ""
    print(f"\n🚗 {o_name} → {d_name}{via_s}   全程 {dist_km:.0f}km · 高德参考 {amap_h:.1f}h · 均速按 {speed:.0f}km/h")
    print(f"🕐 出发 {depart.strftime('%Y-%m-%d %H:%M')}（北京时间）  采样 {len(samples)} 点（每 ~{a.step}km）\n")
    print(f"{'到达':<6}{cjk_pad('位置', 14)}{cjk_pad('海拔', 8)}{cjk_pad('天气', 10)}{'气温':<8}{'降水%':<7}灯 提示")
    alerts = []
    prev_label = None
    for sample_index, (lng, lat, km) in enumerate(samples):
        eta = depart + timedelta(hours=km / speed)
        near, near_elev = nearest_point(lng, lat, points)
        label = near or f"{lat:.2f},{lng:.2f}"
        endpoint = sample_index in (0, len(samples) - 1)
        if sample_index == 0: label = o_name
        elif sample_index == len(samples) - 1: label = d_name
        if not endpoint and label == prev_label: continue
        prev_label = label
        d = om_forecast(lat, lng, near_elev, days=3, hourly=True)
        H = d.get("hourly", {}); times = H.get("time", [])
        # 找最近整点
        forecast_times = [datetime.fromisoformat(t).replace(tzinfo=CHINA_TZ) for t in times]
        if not forecast_times or not forecast_times[0] <= eta <= forecast_times[-1] + timedelta(minutes=30):
            raise SystemExit(f"{label} 的预计到达时间超出当前小时预报范围，不能用其他时段代替。")
        idx = min(range(len(times)), key=lambda i: abs(forecast_times[i] - eta))
        code = H["weather_code"][idx]; txt, ico = WMO.get(code, ("?", "❓"))
        t = H["temperature_2m"][idx]; prob = H["precipitation_probability"][idx]
        snow = (H.get("snowfall") or [None]*len(times))[idx]
        lamp = risk_lamp(code, t, t, prob, snow)
        elev = d.get("elevation", "?")
        elev_s = f"{int(round(elev))}m" if isinstance(elev, (int, float)) else "?"
        note = ""
        if snow and snow >= 0.5: note = "降雪！减速/评估绕行"
        elif t is not None and t <= 1 and (prob or 0) >= 60: note = "低温降水，注意结冰可能"
        elif isinstance(elev, (int, float)) and elev >= 3800: note = "高海拔，勿剧烈活动"
        if note: alerts.append(f"{eta.strftime('%H:%M')} {label}：{note}")
        print(f"{eta.strftime('%H:%M'):<6}{cjk_pad(label, 14)}{cjk_pad(elev_s, 8)}{cjk_pad(ico + txt, 10)}{number_text(t) + '°C':<8}{number_text(prob) + '%':<7}{lamp} {note}")
        time.sleep(0.15)  # 对 Open-Meteo 温柔一点
    if alerts:
        print("\n⚠️  风险提示：")
        for x in alerts: print("  - " + x)
    print()

# ---------- 入口 ----------
def main():
    ap = argparse.ArgumentParser(prog="travel", description="travel-cli 旅游模块 v1")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("points", help="列出预设点位")
    w = sub.add_parser("weather", help="单点天气预报")
    w.add_argument("place"); w.add_argument("--days", type=int, default=6)
    r = sub.add_parser("route", help="沿途天气时间轴")
    r.add_argument("origin"); r.add_argument("dest")
    r.add_argument("--via", nargs="+", help="途经点（锁定路线，如：川主寺 瓦切）")
    r.add_argument("--depart", default="08:00", help="出发时刻 HH:MM（默认今天 08:00）")
    r.add_argument("--speed", type=float, default=0, help="均速 km/h（默认用高德时长推算）")
    r.add_argument("--step", type=int, default=40, help="采样间隔 km（默认 40）")
    a = ap.parse_args()
    if a.cmd == "weather" and not 1 <= a.days <= 16:
        ap.error("--days 必须在 1 到 16 之间")
    if a.cmd == "route":
        if a.step <= 0 or not math.isfinite(a.speed) or a.speed < 0:
            ap.error("--step 必须大于 0；--speed 必须是非负有限数值，0 表示自动估算")
        try:
            datetime.strptime(a.depart, "%H:%M")
        except ValueError:
            ap.error("--depart 必须是有效的 HH:MM 时间")
    if a.cmd == "points":
        pts = load_points()
        print(f"\n共 {len(pts)} 个预设点位：")
        for k, v in pts.items():
            al = f"（别名：{'/'.join(v['alias'])}）" if v.get("alias") else ""
            elev = "地形模型" if v.get("elev") is None else f"{v['elev']}m"
            print(f"  {k:<8} {v['lng']},{v['lat']}  {elev} {al}")
        print()
    elif a.cmd == "weather": cmd_weather(a)
    elif a.cmd == "route": cmd_route(a)

if __name__ == "__main__":
    main()
