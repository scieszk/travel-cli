"""Offline, bounded PNG cards for travel.py and cr12306 roundtrip results.

Pillow is optional: it is imported only when render_card is called. A CJK font
is required, because Pillow's bitmap fallback cannot render Chinese reliably.
The PNG Description contains the displayed text, never the original payload.
"""

from datetime import datetime, timedelta, timezone
import math
import os
from pathlib import Path
import re
import tempfile
import unicodedata

WIDTH, MAX_HEIGHT = 1080, 3200
CHINA_TZ = timezone(timedelta(hours=8))
BG, PAPER, NAVY = "#F0F4F5", "#FFFFFF", "#183249"
SLATE, MUTED, LINE = "#526778", "#718493", "#DFE8EB"
TEAL, TEAL_PALE, CORAL, CORAL_PALE = "#087E83", "#E3F3F1", "#CC654F", "#FFF0E9"
WMO = {0: "晴", 1: "大部晴朗", 2: "多云", 3: "阴", 45: "雾", 48: "冻雾",
       51: "小毛毛雨", 53: "毛毛雨", 55: "强毛毛雨", 56: "冻毛毛雨", 57: "强冻毛毛雨",
       61: "小雨", 63: "中雨", 65: "大雨", 66: "冻雨", 67: "强冻雨",
       71: "小雪", 73: "中雪", 75: "大雪", 77: "雪粒", 80: "小阵雨", 81: "阵雨",
       82: "强阵雨", 85: "阵雪", 86: "强阵雪", 95: "雷暴", 96: "雷暴伴冰雹", 99: "强雷暴伴冰雹"}


def _font_candidates():
    return [
        ("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc", "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc"),
        ("/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc", "/usr/share/fonts/truetype/noto/NotoSansCJK-Bold.ttc"),
        ("/usr/share/fonts/truetype/wqy/wqy-microhei.ttc", ""),
        ("/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc", ""),
        ("C:/Windows/Fonts/msyh.ttc", "C:/Windows/Fonts/msyhbd.ttc"),
        ("C:/Windows/Fonts/simhei.ttf", ""),
        ("/System/Library/Fonts/PingFang.ttc", ""),
    ]


def _find_fonts(image_font, font_path):
    candidates = [(str(font_path), str(font_path))] if font_path else _font_candidates()
    for regular, bold in candidates:
        try:
            probe = image_font.truetype(regular, 28)
            # A Latin-only font usually returns the same .notdef glyph for both.
            masks = [probe.getmask(char) for char in ("中", "文", "\u0378")]
            glyphs = [(mask.size, bytes(mask)) for mask in masks]
            if glyphs[0] == glyphs[1] or glyphs[0] == glyphs[2] or glyphs[1] == glyphs[2]:
                continue
            try:
                image_font.truetype(bold or regular, 28)
            except (OSError, ValueError):
                bold = regular
            return regular, bold or regular
        except (OSError, ValueError):
            continue
    raise RuntimeError("未找到可用中文字体。请安装 Noto Sans CJK（Linux: fonts-noto-cjk），或通过 font_path / --font 指定中文 .ttf/.ttc 字体。")


def _number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _value(value, unit="", decimals=1):
    if not _number(value):
        return "未知"
    return f"{value:.{decimals}f}".rstrip("0").rstrip(".") + unit if decimals else f"{value:.0f}{unit}"


def _probability(value):
    return _value(value, "%") if _number(value) and 0 <= value <= 100 else "未知"


def _code(row):
    value = row.get("weather_code")
    return value if _number(value) and value in WMO else None


def _duration(value):
    if not _number(value) or value < 0:
        return "未知"
    hours, minutes = divmod(int(round(value)), 60)
    return (f"{hours} 小时" if hours else "") + (f" {minutes} 分钟" if hours and minutes else f"{minutes} 分钟" if not hours else "")


def _clean(value):
    if value is None or value == "":
        return "未知"
    text = str(value)
    return " ".join("".join(c for c in text if unicodedata.category(c) not in ("Cc", "Cs")).split()) or "未知"


def _stamp(value):
    if not value:
        return "未知"
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed.tzinfo:
            parsed = parsed.astimezone(CHINA_TZ)
        return parsed.strftime("%Y-%m-%d %H:%M")
    except (TypeError, ValueError):
        return "未知"


def _clock(value):
    stamp = _stamp(value)
    return stamp[-5:] if stamp != "未知" else "未知"


def _weather(row):
    label = row.get("weather")
    return _clean(label) if isinstance(label, str) and label else WMO.get(_code(row), "天气未知")


def _weather_advice(data):
    days = data.get("days", [])
    first = days[0] if days else {}
    rows = [data.get("current") or {}, first, *data.get("hourly", [])[:8]]
    advice = []
    probabilities = [row.get("precipitation_probability_pct") for row in rows]
    rain_likely = any(_number(value) and 50 <= value <= 100 for value in probabilities)
    rainy_codes = {51, 53, 55, 56, 57, 61, 63, 65, 66, 67, 80, 81, 82, 95, 96, 99}
    if rain_likely or any(_code(row) in rainy_codes for row in rows):
        advice.append("有降雨预报，出门带伞")
    low, high = first.get("temperature_min_c"), first.get("temperature_max_c")
    if _number(low) and low < 10:
        advice.append("早晚偏凉，适当添衣")
    if _number(high) and high >= 30:
        advice.append("气温较高，注意补水防晒")
    if advice:
        return "出行提醒：" + "；".join(advice) + "。"
    known = any(_code(row) is not None for row in rows) or any(_number(value) and 0 <= value <= 100 for value in probabilities) or _number(low) or _number(high)
    return "出行提醒：按预报时段安排活动，出行前复核天气变化。" if known else "天气数据不足，出行前请复核预报。"


class _Card:
    def __init__(self, image, draw, image_font, font_paths):
        self.image, self.draw, self.image_font = image, draw, image_font
        self.font_paths, self.fonts, self.displayed = font_paths, {}, []
        self.y, self.omitted = 60, False

    def font(self, size, bold=False):
        key = (size, bold)
        if key not in self.fonts:
            self.fonts[key] = self.image_font.truetype(self.font_paths[int(bold)], size)
        return self.fonts[key]

    def text(self, value, x, y, width, size=30, color=NAVY, bold=False, lines=1):
        """Draw wrapped grapheme-ish runs with a strict box and visible ellipsis."""
        original = _clean(value)
        text = original[:2048]
        font = self.font(size, bold)
        clusters = []
        for char in text:
            if clusters and (unicodedata.combining(char) or char in ("\ufe0f", "\u200d") or clusters[-1].endswith("\u200d")):
                clusters[-1] += char
            else:
                clusters.append(char)
        wrapped, current = [], ""
        for index, cluster in enumerate(clusters):
            candidate = current + cluster
            if self.draw.textlength(candidate, font=font) <= width:
                current = candidate
                continue
            if current:
                wrapped.append(current.rstrip())
            current = cluster.lstrip()
            if len(wrapped) == lines:
                break
        else:
            if current:
                wrapped.append(current.rstrip())
            index = len(clusters)
        truncated = index < len(clusters) or len(wrapped) > lines or len(original) > len(text)
        wrapped = wrapped[:lines]
        if truncated:
            self.omitted = True
            last = wrapped[-1] if wrapped else ""
            while last and self.draw.textlength(last + "…", font=font) > width:
                last = last[:-1]
            wrapped = [*wrapped[:-1], last + "…"]
        line_height = round(size * 1.45)
        for i, line in enumerate(wrapped):
            self.draw.text((x, y + i * line_height), line, font=font, fill=color, anchor="lt")
        self.displayed.append("\n".join(wrapped))
        return max(1, len(wrapped)) * line_height

    def panel(self, y, height, fill=PAPER):
        self.draw.rounded_rectangle((48, y, 1032, y + height), radius=30, fill=fill)

    def section(self, label, eyebrow=None):
        if eyebrow:
            self.text(eyebrow, 72, self.y, 920, 22, TEAL, True)
            self.y += 44
        self.text(label, 72, self.y, 920, 33, NAVY, True)
        self.y += 65

    def header(self, kind, title):
        eyebrow = {"weather": "WEATHER / 天气", "route": "DRIVE / 自驾行程", "rail": "RAIL / 往返行程"}[kind]
        self.draw.rounded_rectangle((60, 59, 66, 89), radius=3, fill=TEAL)
        self.text(eyebrow, 82, 60, 900, 24, TEAL, True)
        self.y = 116
        self.y += self.text(title, 60, self.y, 960, 54, NAVY, True, 2) + 32

    def metrics(self, entries, y, height=150):
        self.panel(y, height)
        cell = 936 / len(entries)
        for index, (label, value) in enumerate(entries):
            x = 72 + index * cell
            self.text(label, x + 12, y + 28, cell - 24, 23, MUTED)
            self.text(value, x + 12, y + 72, cell - 24, 34, NAVY, True)
            if index:
                self.draw.line((x - 12, y + 30, x - 12, y + height - 30), fill=LINE, width=2)
        self.y = y + height + 26

    def note(self, text, color=SLATE, fill=TEAL_PALE, lines=3):
        # Reserve the maximum number of lines, then tighten the background.
        y = self.y
        font = self.font(26)
        maximum = max(1, lines)
        self.panel(y, maximum * 38 + 40, fill)
        self.text(text, 76, y + 22, 928, 26, color, lines=maximum)
        # Keep this predictable in height even when a long sentence wraps.
        self.y += maximum * 38 + 62

    def icon(self, code, x, y, size=150):
        """Weather illustrations are geometry, independent of emoji fonts."""
        scale = size / 140
        def box(coords):
            return tuple((x if i % 2 == 0 else y) + value * scale for i, value in enumerate(coords))
        def ellipse(coords, color):
            self.draw.ellipse(box(coords), fill=color)
        def line(coords, color, width=5):
            self.draw.line(box(coords), fill=color, width=max(1, int(width * scale)))
        if code not in WMO:
            self.draw.ellipse(box((24, 24, 116, 116)), outline=LINE, width=max(2, int(5 * scale)))
            self.draw.text((x + size * .38, y + size * .25), "?", font=self.font(max(18, int(61 * scale))), fill=MUTED, anchor="lt")
            return
        if code in (0, 1, 2):
            ellipse((36, 25, 98, 87), "#F0B85B")
            for angle in range(0, 360, 45):
                radians = math.radians(angle)
                line((67 + 40 * math.cos(radians), 56 + 40 * math.sin(radians), 67 + 49 * math.cos(radians), 56 + 49 * math.sin(radians)), "#F0B85B", 4)
            if code == 0:
                return
        if code in (45, 48):
            for yy, xx in ((48, 25), (69, 12), (90, 25)):
                line((xx, yy, 140 - xx, yy), "#9BAFBA", 8)
            return
        cloud_color = "#BACDD4" if code in (1, 2, 3) else "#89A8B7"
        ellipse((20, 57, 80, 105), cloud_color)
        ellipse((45, 35, 105, 102), cloud_color)
        ellipse((82, 57, 128, 105), cloud_color)
        self.draw.rectangle(box((46, 75, 105, 104)), fill=cloud_color)
        if code in (71, 73, 75, 77, 85, 86):
            for xx in (36, 71, 108):
                line((xx - 5, 118, xx + 5, 128), TEAL, 3)
                line((xx + 5, 118, xx - 5, 128), TEAL, 3)
        elif code >= 95:
            self.draw.polygon([tuple(box(p)) for p in ((71, 90), (56, 119), (73, 119), (65, 140), (92, 108), (74, 108))], fill=CORAL)
        elif code >= 51:
            for xx in (40, 75, 110):
                line((xx, 115, xx - 8, 132), TEAL, 5)


def _weather_card(card, data):
    days, hourly = data.get("days", []), data.get("hourly", [])
    current = data.get("current") or {}
    first = days[0] if days else {}
    has_current = bool(current)
    hero = current if has_current else first
    y = card.y
    card.panel(y, 330, NAVY)
    card.text("当前预报" if has_current else "当日预报 · 最低 / 最高", 80, y + 32, 650, 25, "#B9D4DE")
    if has_current:
        temperature = _value(current.get("temperature_c"), "°")
    else:
        temperature = _value(first.get("temperature_min_c"), "°") + " / " + _value(first.get("temperature_max_c"), "°")
    card.text(temperature, 76, y + 90, 690, 110 if has_current else 74, PAPER, True)
    card.text(_weather(hero), 84, y + 220, 630, 33, PAPER, True)
    card.icon(_code(hero), 790, y + 80, 170)
    timestamp = "数据时间 " + _stamp(current.get("time")) if has_current else "预报日期 " + _clean(first.get("date"))
    card.text(timestamp, 84, y + 280, 900, 23, "#B9D4DE")
    card.y = y + 356
    card.metrics([("体感温度" if has_current else "降水量", _value(current.get("apparent_temperature_c"), "°C") if has_current else _value(first.get("precipitation_mm"), " mm")),
                  ("当日低 / 高", _value(first.get("temperature_min_c"), "°") + " / " + _value(first.get("temperature_max_c"), "°")),
                  ("当日降雨概率", _probability(first.get("precipitation_probability_pct")))], card.y)
    if hourly:
        card.section("逐小时降雨概率", "HOURLY FORECAST")
        shown = hourly[:8]
        y = card.y
        card.panel(y, 275)
        step = 920 / len(shown)
        for index, row in enumerate(shown):
            x = 80 + index * step
            probability = row.get("precipitation_probability_pct")
            known = _number(probability) and 0 <= probability <= 100
            card.text(_value(probability, "%") if known else "未知", x, y + 28, step - 10, 24, TEAL if known else MUTED, True)
            card.draw.rounded_rectangle((x + 12, y + 76, x + 44, y + 176), radius=12, fill=TEAL_PALE)
            if known and probability > 0:
                card.draw.rounded_rectangle((x + 12, y + 176 - probability, x + 44, y + 176), radius=min(12, max(1, probability / 2)), fill=TEAL)
            elif not known:
                card.draw.line((x + 17, y + 126, x + 39, y + 126), fill=MUTED, width=3)
            card.text(_clock(row.get("time")), x - 4, y + 200, step - 6, 23, SLATE)
        card.y = y + 303
        if len(hourly) > 8:
            card.text(f"已省略其余 {len(hourly) - 8} 个小时；按输入顺序展示。", 76, card.y, 920, 23, MUTED)
            card.y += 54
    card.note(_weather_advice(data), lines=2)
    card.section("每日预报", "DAILY OUTLOOK")
    shown = days[:7]
    if not shown:
        card.note("每日预报未知：未提供有效日期数据。", lines=1)
    for index, row in enumerate(shown):
        y = card.y
        card.panel(y, 110)
        card.text(_clean(row.get("date")), 78, y + 25, 206, 26, NAVY, True)
        card.icon(_code(row), 303, y + 23, 59)
        card.text(_weather(row), 378, y + 24, 206, 26, SLATE)
        card.text(_value(row.get("temperature_min_c"), "°") + " / " + _value(row.get("temperature_max_c"), "°"), 610, y + 24, 225, 28, NAVY, True)
        card.text(_probability(row.get("precipitation_probability_pct")), 862, y + 24, 140, 27, TEAL, True)
        if index == 0:
            card.text("最低 / 最高", 610, y + 69, 220, 19, MUTED)
            card.text("降雨概率", 862, y + 69, 140, 19, MUTED)
        card.y += 124
    if len(days) > 7:
        card.text(f"已省略其余 {len(days) - 7} 天预报。", 76, card.y, 920, 23, MUTED)
        card.y += 52


def _route_rows(rows, limit=10):
    if len(rows) <= limit:
        return rows
    # Keep endpoints and named waypoints before intermediate coordinate samples.
    priorities = [0]
    coordinate = re.compile(r"^-?\d+(?:\.\d+)?,\s*-?\d+(?:\.\d+)?$")
    for index, row in enumerate(rows[1:-1], 1):
        if row.get("stop_minutes") or not coordinate.match(str(row.get("label", ""))):
            priorities.append(index)
    selected = priorities[:limit - 1] + [len(rows) - 1]
    for index in range(1, len(rows) - 1):
        if len(selected) >= limit:
            break
        if index not in selected:
            selected.append(index)
    return [rows[index] for index in sorted(selected)]


def _route_card(card, data):
    card.metrics([("全程距离", _value(data.get("distance_km"), " km")),
                  ("预计全程", _duration(data.get("duration_minutes"))),
                  ("途中停留", _duration(data.get("stop_minutes")))], card.y)
    card.text("出发 " + _stamp(data.get("departure_at")), 76, card.y, 920, 27, SLATE)
    card.text("预计抵达 " + _stamp(data.get("arrival_at")), 76, card.y + 44, 920, 27, SLATE)
    card.y += 112
    rows = data.get("samples", [])
    counts = data.get("warning_counts") or {}
    warnings = []
    if _number(counts.get("red")) and counts["red"] > 0:
        warnings.append(f"{int(counts['red'])} 个采样点存在高风险天气标记")
    if _number(counts.get("yellow")) and counts["yellow"] > 0:
        warnings.append(f"{int(counts['yellow'])} 个采样点需留意天气")
    if any(row.get("daylight") is False for row in rows):
        warnings.append("部分路段预计在天黑后行驶")
    if not rows or counts.get("unknown", 0) or any(row.get("weather_status") in ("incomplete", "unavailable") or not _number(row.get("temperature_c")) or _probability(row.get("precipitation_probability_pct")) == "未知" or _code(row) is None for row in rows):
        warnings.append("天气数据不完整，未知路段不能视作晴好")
    if warnings:
        card.note("；".join(warnings) + "。", CORAL, CORAL_PALE, lines=3)
    card.section("沿途时间线", "ORDERED ROUTE")
    shown = _route_rows(rows)
    stamps = [_stamp(data.get("departure_at")), _stamp(data.get("arrival_at")), *(_stamp(row.get("arrival_at")) for row in rows)]
    spans_dates = len({stamp[:10] for stamp in stamps if stamp != "未知"}) > 1
    if not shown:
        card.note("沿途时间线未知：未提供路线采样点。", lines=1)
    for index, row in enumerate(shown):
        y = card.y
        card.panel(y, 142)
        if index < len(shown) - 1:
            card.draw.line((97, y + 73, 97, y + 157), fill=LINE, width=4)
        card.draw.ellipse((81, y + 29, 113, y + 61), fill=TEAL)
        card.draw.text((97, y + 44), str(index + 1), font=card.font(18, True), fill=PAPER, anchor="mm")
        card.text(row.get("label"), 138, y + 24, 620, 30, NAVY, True)
        stamp = _stamp(row.get("arrival_at"))
        arrival = stamp[5:] if spans_dates and stamp != "未知" else _clock(row.get("arrival_at"))
        card.text(arrival, 810, y + 26, 192, 24 if spans_dates else 29, TEAL, True)
        details = _weather(row) + "  ·  " + _value(row.get("temperature_c"), "°C") + "  ·  降雨 " + _probability(row.get("precipitation_probability_pct"))
        card.text(details, 138, y + 73, 860, 25, SLATE)
        stop = row.get("stop_minutes")
        if _number(stop) and stop > 0:
            card.text("停留 " + _duration(stop), 138, y + 108, 820, 22, CORAL)
        card.y += 157
    if len(rows) > len(shown):
        card.text(f"已省略 {len(rows) - len(shown)} 个采样点；保留起终点，优先展示途经点。", 76, card.y, 920, 23, MUTED, lines=2)
        card.y += 76


def _rail_leg(card, label, ticket, departure, arrival, date):
    y = card.y
    card.panel(y, 360)
    card.draw.rounded_rectangle((77, y + 28, 174, y + 72), radius=12, fill=TEAL_PALE)
    card.text(label, 96, y + 36, 80, 25, TEAL, True)
    card.text(ticket.get("code"), 204, y + 29, 330, 36, NAVY, True)
    card.text(_stamp(departure)[:10] if _stamp(departure) != "未知" else _clean(date), 678, y + 36, 320, 26, MUTED)
    # Dates are shown for both ends, including a return arriving the next day.
    card.text(_clock(departure), 80, y + 111, 335, 60, NAVY, True)
    card.text(_clock(arrival), 667, y + 111, 335, 60, NAVY, True)
    card.draw.line((420, y + 148, 614, y + 148), fill=TEAL, width=4)
    card.draw.polygon(((614, y + 148), (600, y + 139), (600, y + 157)), fill=TEAL)
    card.text(ticket.get("fromStation"), 80, y + 194, 388, 32, NAVY, True, 2)
    card.text(ticket.get("toStation"), 667, y + 194, 335, 32, NAVY, True, 2)
    arrival_date = _stamp(arrival)[:10] if _stamp(arrival) != "未知" else "未知"
    card.text("抵达日期 " + arrival_date, 80, y + 293, 470, 23, MUTED)
    duration = ticket.get("duration")
    if isinstance(duration, str) and re.fullmatch(r"\d{1,3}:[0-5]\d", duration):
        hours, minutes = map(int, duration.split(":"))
        duration = hours * 60 + minutes
    else:
        duration = None
    card.text("历时 " + _duration(duration), 667, y + 293, 335, 23, MUTED)
    card.y += 386
    seats = ticket.get("seats") or {}
    if seats:
        entries = list(seats.items())[:4]
        availability = " / ".join(_clean(name) + " " + _clean(value) for name, value in entries)
        if len(seats) > 4:
            availability += f"（已省略 {len(seats) - 4} 种席别）"
    else:
        availability = "余票未知"
    if ticket.get("canBuy") is False:
        availability = "当前不可购 · " + availability
    card.text(availability, 78, card.y, 920, 25, SLATE, lines=2)
    card.y += 88


def _rail_card(card, data):
    card.metrics([("目的地停留", _duration(data.get("stayMin"))),
                  ("两程乘车", _duration(data.get("totalTravelMin")))], card.y)
    _rail_leg(card, "去程", data.get("outbound") or {}, data.get("outboundDeparture"), data.get("destinationArrival"), data.get("outboundDate"))
    _rail_leg(card, "返程", data.get("return") or {}, data.get("returnDeparture"), data.get("homeArrival"), data.get("returnDate"))
    if data.get("seat"):
        card.text("筛选席别 " + _clean(data["seat"]), 76, card.y, 920, 25, TEAL)
        card.y += 55
    card.note("余票为查询时状态，可能变化。本卡不是车票，也不保证购票成功；请以 12306 与车站公告为准。", SLATE, TEAL_PALE, lines=3)


def _validate(kind, data, output_path):
    if kind not in ("weather", "route", "rail"):
        raise ValueError("kind 必须是 weather、route 或 rail")
    if not isinstance(data, dict):
        raise TypeError("data 必须是 JSON 对象 / dict")
    if Path(output_path).suffix.lower() != ".png":
        raise ValueError("图片输出路径必须使用 .png 扩展名")
    arrays = {"weather": ("days", "hourly"), "route": ("samples",), "rail": ()}[kind]
    for field in arrays:
        if field in data and (not isinstance(data[field], list) or any(not isinstance(row, dict) for row in data[field])):
            raise ValueError(f"{field} 必须是对象数组")
    objects = {"weather": ("current",), "route": ("warning_counts",), "rail": ("outbound", "return")}[kind]
    for field in objects:
        if field in data and data[field] is not None and not isinstance(data[field], dict):
            raise ValueError(f"{field} 必须是对象")
    if kind == "rail":
        for field in ("outbound", "return"):
            seats = (data.get(field) or {}).get("seats")
            if seats is not None and not isinstance(seats, dict):
                raise ValueError(f"{field}.seats 必须是对象")


def _save_private_png(image, output, metadata):
    """Publish a complete private PNG without changing existing parent modes."""
    missing = []
    parent = output.parent
    while not parent.exists():
        missing.append(parent)
        parent = parent.parent
    for directory in reversed(missing):
        try:
            directory.mkdir(mode=0o700)
        except FileExistsError:
            continue
        directory.chmod(0o700)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w+b", prefix=".travel-card-", suffix=".tmp", dir=output.parent, delete=False) as handle:
            temporary = Path(handle.name)
            temporary.chmod(0o600)
            image.save(handle, format="PNG", pnginfo=metadata, optimize=True)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, output)
        temporary = None
    finally:
        if temporary is not None:
            try:
                temporary.unlink()
            except OSError:
                pass


def render_card(kind, data, output_path, *, title=None, font_path=None, generated_at=None):
    """Render one 1080px PNG locally and return path, width, height and caption.

    ``generated_at`` is the upstream query timestamp (ISO 8601 or datetime),
    not a substitute for the weather data time. Without it, queried_at
    in data is used; absent query times are explicitly marked as unavailable.
    Long fields and excess rows are truncated visibly. The data is not changed.
    """
    _validate(kind, data, output_path)
    try:
        from PIL import Image, ImageDraw, ImageFont, PngImagePlugin
    except ImportError as error:
        raise RuntimeError("图片导出需要 Pillow；请运行 python -m pip install Pillow。文本命令无需安装。") from error
    fonts = _find_fonts(ImageFont, font_path)
    image = Image.new("RGB", (WIDTH, MAX_HEIGHT), BG)
    card = _Card(image, ImageDraw.Draw(image), ImageFont, fonts)
    if title is None:
        if kind == "weather":
            title = _clean(data.get("place")) + " · 天气"
        elif kind == "route":
            title = _clean(data.get("origin")) + " → " + _clean(data.get("destination"))
        else:
            outbound = data.get("outbound") or {}
            title = _clean(outbound.get("fromStation")) + " — " + _clean(outbound.get("toStation")) + " · 往返"
    card.header(kind, title)
    {"weather": _weather_card, "route": _route_card, "rail": _rail_card}[kind](card, data)
    if card.omitted:
        card.text("部分长文本已省略，以原始查询结果为准。", 76, card.y, 920, 23, CORAL)
        card.y += 52
    sources = {"weather": "来源：Open-Meteo · open-meteo.com",
               "route": "来源：高德地图 amap.com / 天气 Open-Meteo open-meteo.com",
               "rail": "来源：12306 · www.12306.cn"}
    card.y += 12
    card.draw.line((72, card.y, 1008, card.y), fill=LINE, width=2)
    card.y += 28
    card.text(sources[kind], 76, card.y, 920, 22, MUTED, lines=2)
    card.y += 62
    if kind in ("weather", "route"):
        card.text("天气许可：CC BY 4.0 · creativecommons.org/licenses/by/4.0/", 76, card.y, 920, 22, MUTED)
        card.y += 40
    query = generated_at if generated_at is not None else data.get("queried_at", data.get("generated_at"))
    query_stamp = _stamp(query) if query is not None else "未提供"
    card.text("查询时间 " + query_stamp + " · 北京时间", 76, card.y, 920, 22, MUTED)
    card.y += 40
    card.text("制图时间 " + datetime.now(CHINA_TZ).strftime("%Y-%m-%d %H:%M") + " · 北京时间", 76, card.y, 920, 22, MUTED)
    card.y += 76
    if card.y > MAX_HEIGHT:
        raise ValueError("卡片内容超过布局上限；请减少输入内容")
    height = max(760, math.ceil(card.y))
    output = Path(output_path).expanduser().resolve()
    metadata = PngImagePlugin.PngInfo()
    metadata.add_text("Description", "\n".join(card.displayed))
    _save_private_png(image.crop((0, 0, WIDTH, height)), output, metadata)
    caption = _clean(title)
    if len(caption) > 120:
        caption = caption[:119] + "…"
    return {"path": str(output), "width": WIDTH, "height": height, "caption": caption}
