"""Private preferences, printable itinerary cards, and manual change checks.

This module performs no network requests and sends no notifications. Coordinates
in route plans and map links are GCJ02, matching the Amap route API.
"""
from datetime import date, datetime
import hashlib
import json
import math
import os
from pathlib import Path
import re
import tempfile
from urllib.parse import quote, urlencode


DEFAULT_PREFERENCES = {"max_detour_minutes": 30, "visit_minutes": 60,
                       "buffer_minutes": 30, "interests": "any", "avoid_night_driving": False}
_BOUNDS = {"max_detour_minutes": (0, 240), "visit_minutes": (0, 1440), "buffer_minutes": (0, 240)}
_LAMPS = {"🟢", "🟡", "🔴", "⚪"}
_OPTION_KEYS = {"origin", "dest", "via", "stop", "date", "depart", "speed", "step", "waypoint_overrides"}
_BASE_KEYS = {"complete", "arrival_at", "samples", "night_samples", "daylight_complete"}
_SAMPLE_KEYS = {"label", "latitude", "longitude", "lamp", "weather_status", "daylight"}


def _number(value, low, high):
    return (isinstance(value, (int, float)) and not isinstance(value, bool)
            and math.isfinite(value) and low <= value <= high)


def _preferences(values):
    if not isinstance(values, dict) or set(values) - DEFAULT_PREFERENCES.keys():
        raise ValueError("偏好设置包含无效字段")
    for key, value in values.items():
        if key in _BOUNDS:
            valid = _number(value, *_BOUNDS[key]) and int(value) == value
        elif key == "interests":
            valid = isinstance(value, str) and value in ("any", "nature", "culture")
        else:
            valid = type(value) is bool
        if not valid:
            raise ValueError("偏好设置的值或范围无效")
    return {key: int(value) if key in _BOUNDS else value for key, value in values.items()}


def _preferences_path(path):
    return Path(path if path is not None else os.environ.get(
        "TRAVEL_PREFERENCES", str(Path.home() / ".config" / "travel-cli" / "preferences.json"))).expanduser()


def _read_json(path):
    try:
        with path.open(encoding="utf-8") as handle:
            return json.load(handle, parse_constant=lambda value: (_ for _ in ()).throw(ValueError()))
    except (OSError, ValueError, UnicodeError):
        raise ValueError("无法读取本地设置，文件缺失、损坏或不可访问") from None


def _private_parent(path):
    missing = []
    parent = path.parent
    while not parent.exists():
        missing.append(parent)
        parent = parent.parent
    for directory in reversed(missing):
        try:
            directory.mkdir(mode=0o700)
        except FileExistsError:
            continue
        directory.chmod(0o700)


def _atomic_write(path, data):
    """Replace a complete 0600 file, changing modes only on new directories."""
    temporary = None
    try:
        contents = json.dumps(data, ensure_ascii=False, allow_nan=False, indent=2) + "\n"
        _private_parent(path)
        descriptor, temporary = tempfile.mkstemp(prefix=".travel-", suffix=".tmp", dir=path.parent)
        try:
            os.chmod(temporary, 0o600)
            with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
                descriptor = None
                handle.write(contents)
                handle.flush()
                os.fsync(handle.fileno())
        finally:
            if descriptor is not None:
                os.close(descriptor)
        os.replace(temporary, path)
        temporary = None
    except (OSError, TypeError, ValueError):
        raise ValueError("无法安全保存本地设置") from None
    finally:
        if temporary is not None:
            try:
                os.unlink(temporary)
            except OSError:
                pass


def load_preferences(path=None):
    """Return validated preferences over defaults; a missing file is normal."""
    target = _preferences_path(path)
    values = {}
    if target.exists():
        try:
            values = _preferences(_read_json(target))
        except ValueError:
            raise ValueError("偏好设置文件无效，请重置后重试") from None
    return {**DEFAULT_PREFERENCES, **values}


def save_preferences(updates, path=None):
    """Merge explicit whitelisted updates with existing values and save privately."""
    updates = _preferences(updates)
    values = {**load_preferences(path), **updates}
    _atomic_write(_preferences_path(path), values)
    return values


def reset_preferences(path=None):
    """Replace even malformed preferences with the validated defaults."""
    values = dict(DEFAULT_PREFERENCES)
    _atomic_write(_preferences_path(path), values)
    return values


def parse_updates(items):
    """Parse CLI key=value pairs without accepting Python truthy strings."""
    values = {}
    try:
        for item in items:
            key, value = item.split("=", 1)
            if key in values or key not in DEFAULT_PREFERENCES:
                raise ValueError()
            if key in _BOUNDS:
                value = float(value)
                if value.is_integer():
                    value = int(value)
            elif key == "avoid_night_driving":
                if value not in ("true", "false"):
                    raise ValueError()
                value = value == "true"
            values[key] = value
        return _preferences(values)
    except (TypeError, ValueError, AttributeError, OverflowError):
        raise ValueError("偏好设置须为有效的 key=value；布尔值只能使用 true 或 false") from None


def _text(value):
    if value is None:
        return "未知"
    value = " ".join(str(value).split())
    return re.sub(r"([\\`*_{}\[\]()<>|#!])", r"\\\1", value)


def _display_number(value, digits=0):
    return f"{value:.{digits}f}" if _number(value, -1e12, 1e12) else "未知"


def _map_link(row):
    lng, lat = row.get("longitude"), row.get("latitude")
    if not _number(lng, -180, 180) or not _number(lat, -90, 90):
        return "地图位置未知"
    query = urlencode({"position": f"{lng:.12g},{lat:.12g}", "name": str(row.get("label", "位置")),
                       "coordinate": "gaode", "callnative": "0"}, quote_via=quote)
    return f"[高德地图](https://uri.amap.com/marker?{query})"


def render_card(plan):
    """Render an itinerary as Markdown; never write or open a map automatically."""
    lines = [f"# {_text(plan.get('origin'))} → {_text(plan.get('destination'))} 行程卡", "",
             "以下均为预计时间（北京时间，UTC+08:00）；路况与停留时间变化会影响到达时刻。", "",
             f"- 预计出发：{_text(plan.get('departure_at'))}",
             f"- 预计到达：{_text(plan.get('arrival_at'))}",
             f"- 路程约 {_display_number(plan.get('distance_km'), 1)} km；总计约 {_display_number(plan.get('duration_minutes'))} 分钟；停留 {_display_number(plan.get('stop_minutes'))} 分钟", "",
             "| 位置 | 预计到达 | 预计离开 | 停留分钟 | 天气提示 | 地图（GCJ02） |",
             "| --- | --- | --- | ---: | --- | --- |"]
    for row in plan.get("samples", []):
        lines.append(f"| {_text(row.get('label'))} | {_text(row.get('arrival_at'))} | {_text(row.get('departure_at'))} | "
                     f"{_display_number(row.get('stop_minutes'))} | {_text(row.get('lamp'))} {_text(row.get('weather'))} | {_map_link(row)} |")
    if not plan.get("complete"):
        lines.extend(["", "部分天气数据缺失或查询失败，未知不代表天气良好。"])
    if plan.get("night_samples"):
        lines.extend(["", f"日照提示：有 {_display_number(plan.get('night_samples'))} 个采样时刻处于夜间。"])
    if plan.get("daylight_complete") is False:
        lines.extend(["", "部分日出日落数据缺失，无法确认全程是否处于白天。"])
    lines.extend(["", "天气数据：[Open-Meteo](https://open-meteo.com/) · "
                  "[CC BY 4.0](https://creativecommons.org/licenses/by/4.0/)；天气提示为本工具的估算。",
                  "地图位置链接使用高德 GCJ02 坐标。", ""])
    return "\n".join(lines)


def _watch_path(path):
    path = Path(path).expanduser()
    return path if path.name.endswith(".watch.json") else path.with_name(path.name + ".watch.json")


def _string(value):
    return isinstance(value, str) and 0 < len(value) <= 2000 and not any(ord(ch) < 32 for ch in value)


def _timestamp(value):
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value)
        return parsed if parsed.tzinfo is not None else None
    except ValueError:
        return None


def _options(options):
    if not isinstance(options, dict) or set(options) - _OPTION_KEYS:
        raise ValueError()
    if not all(_string(options.get(key)) for key in ("origin", "dest")):
        raise ValueError()
    result = dict(options)
    for key in ("via", "stop"):
        if key in result:
            value = result[key]
            if value is None:
                result[key] = []
            elif not isinstance(value, list) or len(value) > 200 or not all(_string(item) for item in value):
                raise ValueError()
    if "date" in result:
        if not isinstance(result["date"], str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", result["date"]):
            raise ValueError()
        date.fromisoformat(result["date"])
    if "depart" in result and (not isinstance(result["depart"], str) or not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", result["depart"])):
        raise ValueError()
    if "speed" in result and not _number(result["speed"], 0, 300):
        raise ValueError()
    if "step" in result and not (_number(result["step"], 0, 1000) and result["step"] > 0):
        raise ValueError()
    if "waypoint_overrides" in result:
        overrides = result["waypoint_overrides"]
        if not isinstance(overrides, dict) or len(overrides) > 202:
            raise ValueError()
        for name, point in overrides.items():
            if not _string(name) or not isinstance(point, dict) or set(point) != {"lng", "lat", "elev", "coord_system"}:
                raise ValueError()
            if (not _number(point["lng"], -180, 180) or not _number(point["lat"], -90, 90)
                    or point["coord_system"] != "gcj02" or (point["elev"] is not None and not _number(point["elev"], -1000, 10000))):
                raise ValueError()
    return result


def _snapshot(plan, strict=False):
    if not isinstance(plan, dict) or (strict and set(plan) - _BASE_KEYS):
        raise ValueError()
    if type(plan.get("complete")) is not bool or not isinstance(plan.get("samples"), list) or len(plan["samples"]) > 500:
        raise ValueError()
    arrival = plan.get("arrival_at")
    if arrival is not None and _timestamp(arrival) is None:
        raise ValueError()
    result = {"complete": plan["complete"], "arrival_at": arrival, "samples": []}
    for row in plan["samples"]:
        if not isinstance(row, dict) or (strict and set(row) - _SAMPLE_KEYS) or not _string(row.get("label")):
            raise ValueError()
        if row.get("lamp") not in _LAMPS:
            raise ValueError()
        sample = {"label": row["label"], "lamp": row["lamp"],
                  "weather_status": row.get("weather_status", "unavailable" if row["lamp"] == "⚪" else "available")}
        if sample["weather_status"] not in ("available", "incomplete", "unavailable"):
            raise ValueError()
        for key, low, high in (("longitude", -180, 180), ("latitude", -90, 90)):
            if key in row:
                if not _number(row[key], low, high):
                    raise ValueError()
                sample[key] = row[key]
        if "daylight" in row:
            if row["daylight"] is not None and type(row["daylight"]) is not bool:
                raise ValueError()
            sample["daylight"] = row["daylight"]
        result["samples"].append(sample)
    if "night_samples" in plan:
        if type(plan["night_samples"]) is not int or not 0 <= plan["night_samples"] <= 500:
            raise ValueError()
        result["night_samples"] = plan["night_samples"]
    if "daylight_complete" in plan:
        if type(plan["daylight_complete"]) is not bool:
            raise ValueError()
        result["daylight_complete"] = plan["daylight_complete"]
    return result


def save_watch(path, plan, options):
    """Save a minimal comparison baseline and rerun options; return actual path."""
    try:
        document = {"schema_version": 1, "baseline": _snapshot(plan), "options": _options(options)}
        target = _watch_path(path)
        _atomic_write(target, document)
        return target
    except (TypeError, ValueError):
        raise ValueError("无法保存检查基线：行程或选项无效") from None


def load_watch(path):
    """Load and validate a watch document without exposing its contents in errors."""
    try:
        data = _read_json(_watch_path(path))
        if (not isinstance(data, dict) or set(data) - {"schema_version", "baseline", "options", "last_event_signature", "last_state"}
                or type(data.get("schema_version")) is not int or data["schema_version"] != 1):
            raise ValueError()
        data["baseline"] = _snapshot(data["baseline"], strict=True)
        data["options"] = _options(data["options"])
        signature = data.get("last_event_signature")
        if signature is not None and (not isinstance(signature, str) or not re.fullmatch(r"[a-f0-9]{64}", signature)):
            raise ValueError()
        if data.get("last_state") not in (None, "changed", "unchanged", "unavailable"):
            raise ValueError()
        return data
    except (KeyError, TypeError, ValueError):
        raise ValueError("检查基线文件无效或不可读取") from None


def _available(plan):
    return (plan["complete"] and plan["arrival_at"] is not None and bool(plan["samples"])
            and plan.get("daylight_complete") is not False
            and all(row["lamp"] != "⚪" and row["weather_status"] == "available"
                    and ("daylight" not in row or row["daylight"] is not None) for row in plan["samples"]))


def compare_watch(baseline, current):
    """Compare known forecasts, night counts, and arrival shifts of >=30 minutes."""
    unavailable = {"state": "unavailable", "changes": ["数据不完整或无法取得，暂不能判断行程是否发生变化。"], "notify": True}
    try:
        before, after = _snapshot(baseline), _snapshot(current)
    except (TypeError, ValueError):
        return unavailable
    if not _available(before) or not _available(after):
        return unavailable
    identity = lambda row: (row["label"], row.get("longitude"), row.get("latitude"))
    if ([identity(row) for row in before["samples"]] != [identity(row) for row in after["samples"]]
            or ("night_samples" in before) != ("night_samples" in after)
            or ("daylight_complete" in before) != ("daylight_complete" in after)):
        return unavailable
    changes = []
    for previous, latest in zip(before["samples"], after["samples"]):
        if ("daylight" in previous) != ("daylight" in latest):
            return unavailable
        if previous["lamp"] != latest["lamp"]:
            changes.append(f"{latest['label']} 天气提示由 {previous['lamp']} 变为 {latest['lamp']}。")
        if previous.get("daylight") != latest.get("daylight"):
            earlier = "白天" if previous["daylight"] else "夜间"
            later = "白天" if latest["daylight"] else "夜间"
            changes.append(f"{latest['label']} 预计经过时段由{earlier}变为{later}。")
    if before.get("night_samples") != after.get("night_samples"):
        changes.append(f"夜间采样数由 {before['night_samples']} 变为 {after['night_samples']}。")
    delta = (_timestamp(after["arrival_at"]) - _timestamp(before["arrival_at"])).total_seconds() / 60
    if abs(delta) >= 30:
        changes.append(f"预计到达时间由 {before['arrival_at']} 变为 {after['arrival_at']}（变化 {delta:+.0f} 分钟）。")
    return {"state": "changed" if changes else "unchanged", "changes": changes, "notify": bool(changes)}


def record_watch_check(path, result):
    """Deduplicate consecutive equal events without changing baseline or options."""
    if (not isinstance(result, dict) or result.get("state") not in ("changed", "unchanged", "unavailable")
            or not isinstance(result.get("changes"), list) or not all(isinstance(item, str) for item in result["changes"])):
        raise ValueError("检查结果无效")
    document = load_watch(path)
    event = {"state": result["state"], "changes": result["changes"]}
    signature = hashlib.sha256(json.dumps(event, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()
    recovered = document.get("last_state") == "unavailable" and event["state"] != "unavailable"
    result = {**event, "notify": recovered or (result["state"] != "unchanged" and document.get("last_event_signature") != signature)}
    if recovered:
        result["changes"] = ["行程检查已恢复可用。", *event["changes"]]
    document["last_event_signature"] = signature
    document["last_state"] = event["state"]
    _atomic_write(_watch_path(path), document)
    return result
