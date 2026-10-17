"""Normalize optional current/hourly forecast data without inventing missing values."""
from datetime import datetime, timedelta
import math

from planning import CHINA_TZ, value_at


def finite(value):
    return value if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) else None


def add_details(data, forecast, now, weather_codes):
    current = forecast.get("current")
    current = current if isinstance(current, dict) else {}
    code = finite(current.get("weather_code"))
    data["current"] = {
        "time": current.get("time") if isinstance(current.get("time"), str) else None,
        "temperature_c": finite(current.get("temperature_2m")),
        "apparent_temperature_c": finite(current.get("apparent_temperature")),
        "weather_code": code, "weather": weather_codes.get(code, ("未知", ""))[0],
        "is_day": finite(current.get("is_day")),
        "relative_humidity_pct": finite(current.get("relative_humidity_2m")),
        "wind_speed_kmh": finite(current.get("wind_speed_10m")),
    }
    hours = forecast.get("hourly")
    hours = hours if isinstance(hours, dict) else {}
    start = now.astimezone(CHINA_TZ).replace(minute=0, second=0, microsecond=0)
    result = []
    times = hours.get("time", [])
    for index, raw in enumerate(times if isinstance(times, list) else []):
        try:
            when = datetime.fromisoformat(raw)
            when = when.replace(tzinfo=CHINA_TZ) if when.tzinfo is None else when.astimezone(CHINA_TZ)
        except (TypeError, ValueError):
            continue
        if not start <= when < start + timedelta(hours=6):
            continue
        code = value_at(hours, "weather_code", index)
        result.append({"time": raw, "temperature_c": value_at(hours, "temperature_2m", index),
                       "weather_code": code, "weather": weather_codes.get(code, ("未知", ""))[0],
                       "precipitation_probability_pct": value_at(hours, "precipitation_probability", index),
                       "precipitation_mm": value_at(hours, "precipitation", index)})
    data["hourly"] = sorted(result, key=lambda row: row["time"])
    data["generated_at"] = now.isoformat(timespec="seconds")
