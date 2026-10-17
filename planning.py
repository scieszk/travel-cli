"""Pure route sampling, schedule calculation and argument validation."""
from datetime import date, datetime, timedelta, timezone
import math
import re

CHINA_TZ = timezone(timedelta(hours=8))


class ToolError(SystemExit):
    """A sanitized, machine-readable failure, compatible with older callers."""
    def __init__(self, code, message, status=1):
        super().__init__(message)
        self.error_code = code
        self.status = status


def parse_clock(value):
    if not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", value):
        raise ToolError("INVALID_ARGUMENT", "时间必须为有效的 HH:MM，例如 07:00", 2)
    return datetime.strptime(value, "%H:%M").time()


def parse_date(value, today):
    try:
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
            raise ValueError()
        selected = date.fromisoformat(value)
    except ValueError:
        raise ToolError("INVALID_ARGUMENT", "日期必须为真实的 YYYY-MM-DD 日期", 2) from None
    if not today <= selected <= today + timedelta(days=15):
        raise ToolError("INVALID_ARGUMENT", "出发日期须在北京时间今天至未来第 15 天内，实际可用预报以接口为准", 2)
    return selected


def haversine(lng1, lat1, lng2, lat2):
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lng2 - lng1)
    value = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 6371 * 2 * math.asin(math.sqrt(min(1, max(0, value))))


def sample_route(points, step_km=40):
    """Interpolate along each polyline segment, retaining both endpoints."""
    if not math.isfinite(step_km) or step_km <= 0:
        raise ValueError("采样间隔必须大于 0")
    if not points:
        return []
    out = [(points[0][0], points[0][1], 0.0)]
    total, next_sample = 0.0, step_km
    for first, second in zip(points, points[1:]):
        length = haversine(*first, *second)
        while length > 0 and next_sample < total + length:
            fraction = (next_sample - total) / length
            out.append((first[0] + (second[0] - first[0]) * fraction,
                        first[1] + (second[1] - first[1]) * fraction, next_sample))
            next_sample += step_km
            if len(out) > 200:
                raise ToolError("INVALID_ARGUMENT", "路线采样超过 200 点，请增大 --step", 2)
        total += length
    if out[-1][:2] != tuple(points[-1]):
        out.append((*points[-1], total))
    return out


def build_schedule(legs, stops, speed=0, step=40):
    """Use each leg's distance/duration and add stops after arrival at via points."""
    samples = []
    total_km, elapsed = 0.0, 0.0
    for leg_index, leg in enumerate(legs):
        distance, hours = leg["distance_km"], leg["duration_hours"]
        if not all(math.isfinite(v) and v > 0 for v in (distance, hours)):
            raise ToolError("UPSTREAM_RESPONSE", "地图接口未返回有效的路线距离和时长")
        duration = distance / speed if speed else hours
        sampled = sample_route(leg["points"], step)
        if len(sampled) < 2 or sampled[-1][2] <= 0:
            raise ToolError("UPSTREAM_RESPONSE", "地图接口未返回有效路线折线")
        for index, (lng, lat, geometry_km) in enumerate(sampled):
            if leg_index and index == 0:
                continue
            fraction = geometry_km / sampled[-1][2]
            endpoint = leg["origin"] if index == 0 else leg["destination"] if index == len(sampled) - 1 else None
            samples.append({"label": endpoint[0] if endpoint else f"{lat:.3f},{lng:.3f}",
                            "longitude": lng, "latitude": lat,
                            "elevation_m": endpoint[3] if endpoint else None,
                            "distance_km": total_km + fraction * distance,
                            "elapsed_hours": elapsed + fraction * duration,
                            "stop_minutes": stops.get(leg_index, 0) if index == len(sampled) - 1 else 0})
        elapsed += duration + stops.get(leg_index, 0) / 60
        total_km += distance
    if len(samples) > 200:
        raise ToolError("INVALID_ARGUMENT", "路线采样超过 200 点，请增大 --step", 2)
    return {"distance_km": total_km, "duration_hours": elapsed,
            "stop_minutes": sum(stops.values()), "samples": samples}


def hourly_index(times, arrival):
    """Reject missing, malformed or mismatched forecast hours instead of guessing."""
    try:
        parsed = [datetime.fromisoformat(value) for value in times]
        parsed = [value.replace(tzinfo=CHINA_TZ) if value.tzinfo is None else value.astimezone(CHINA_TZ) for value in parsed]
        if not parsed or any(a >= b for a, b in zip(parsed, parsed[1:])):
            raise ValueError()
    except (ValueError, TypeError):
        raise ToolError("UPSTREAM_RESPONSE", "天气接口未返回有效的小时预报时间") from None
    index = min(range(len(parsed)), key=lambda i: abs(parsed[i] - arrival))
    if arrival < parsed[0] or abs(parsed[index] - arrival) > timedelta(minutes=30):
        raise ToolError("FORECAST_RANGE", "预计到达时间超出实际小时预报范围，不能用其他时段代替")
    return index


def value_at(data, key, index):
    values = data.get(key)
    if not isinstance(values, list) or index >= len(values):
        return None
    value = values[index]
    return value if isinstance(value, (float, int)) and not isinstance(value, bool) and math.isfinite(value) else None
