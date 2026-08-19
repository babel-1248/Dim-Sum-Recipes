#!/usr/bin/env python3
"""Fetch and render hourly weather plus US AQI for one US ZIP code."""

import argparse
import datetime
import hashlib
import json
import os
import sys
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import geocode  # noqa: E402
import wmo  # noqa: E402
from httpclient import CLIENT  # noqa: E402


FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
AQI_URL = "https://air-quality-api.open-meteo.com/v1/air-quality"
HOURLY_FORECAST_VARS = ",".join([
    "temperature_2m", "apparent_temperature", "precipitation",
    "precipitation_probability", "weather_code", "wind_speed_10m",
])
HOURLY_AQI_VARS = "us_aqi,pm2_5"


def fetch_forecast(latitude, longitude, past_days, forecast_days):
    return CLIENT.get_json(FORECAST_URL, {
        "latitude": latitude,
        "longitude": longitude,
        "hourly": HOURLY_FORECAST_VARS,
        "temperature_unit": "fahrenheit",
        "wind_speed_unit": "mph",
        "precipitation_unit": "inch",
        "timezone": "auto",
        "past_days": past_days,
        "forecast_days": forecast_days,
    })


def fetch_aqi(latitude, longitude, past_days, forecast_days):
    return CLIENT.get_json(AQI_URL, {
        "latitude": latitude,
        "longitude": longitude,
        "hourly": HOURLY_AQI_VARS,
        "timezone": "auto",
        "past_days": past_days,
        "forecast_days": forecast_days,
    })


def hourly_aqi_by_time(aqi_json):
    hourly = aqi_json.get("hourly") or {}
    times = hourly.get("time") or []
    values = {}
    for index, timestamp in enumerate(times):
        def at(name):
            series = hourly.get(name) or []
            return series[index] if index < len(series) else None

        values[timestamp] = {"us_aqi": at("us_aqi"), "pm2_5": at("pm2_5")}
    return values


def build_hours(forecast_json, aqi_by_time, now_iso_hour):
    hourly = forecast_json.get("hourly") or {}
    times = hourly.get("time") or []
    hours = []
    for index, timestamp in enumerate(times):
        def at(name):
            series = hourly.get(name) or []
            return series[index] if index < len(series) else None

        aqi = aqi_by_time.get(timestamp, {})
        hours.append({
            "timestamp": timestamp,
            "date": timestamp[:10],
            "hour": timestamp[11:16],
            "when": (
                "past" if timestamp < now_iso_hour
                else "now" if timestamp == now_iso_hour
                else "forecast"),
            "temp_f": at("temperature_2m"),
            "feels_f": at("apparent_temperature"),
            "precip_in": at("precipitation"),
            "precip_prob_pct": at("precipitation_probability"),
            "wind_mph": at("wind_speed_10m"),
            "weather_code": at("weather_code"),
            "conditions": wmo.weather_text(at("weather_code")),
            "us_aqi": aqi.get("us_aqi"),
            "aqi_category": wmo.aqi_category(aqi.get("us_aqi")),
            "pm2_5": aqi.get("pm2_5"),
        })
    if not hours:
        raise SystemExit("Open-Meteo returned no hourly forecast data")
    return hours


def cell(value):
    return str(value).replace("|", "\\|").replace("\r", " ").replace("\n", " ")


def number(value, suffix=""):
    return "--" if value is None else f"{round(value):,}{suffix}"


def render_markdown(place, hours, generated_at):
    location = f"{place['place']}, {place['state_abbr']}"
    lines = [
        f"**ZIP {place['zip']} — {cell(location)}** · synced {generated_at}",
        "",
        "| Date | Time | Temp | Feels | Conditions | Precip | Wind | AQI | PM2.5 |",
        "|---|---|---:|---:|---|---:|---:|---|---:|",
    ]
    previous_date = None
    for hour in hours:
        date = datetime.date.fromisoformat(hour["date"])
        date_label = ""
        if hour["date"] != previous_date:
            date_label = f"**{date.strftime('%b')} {date.day}**"
            previous_date = hour["date"]

        hour_number = int(hour["hour"].split(":", 1)[0])
        time_label = datetime.time(hour_number).strftime("%I %p").lstrip("0")
        if hour["when"] == "now":
            time_label = f"**{time_label} (Now)**"

        precipitation = "--"
        if hour["precip_in"] is not None and hour["precip_in"] > 0:
            precipitation = f"{hour['precip_in']:.2f} in"
        elif hour["precip_prob_pct"] is not None:
            precipitation = f"{round(hour['precip_prob_pct'])}%"

        aqi = "--"
        if hour["us_aqi"] is not None:
            aqi = f"{round(hour['us_aqi'])} {hour['aqi_category']}"
        pm25 = "--" if hour["pm2_5"] is None else f"{hour['pm2_5']:.1f} µg/m³"
        lines.append("| " + " | ".join([
            date_label,
            time_label,
            number(hour["temp_f"], "°F"),
            number(hour["feels_f"], "°F"),
            cell(hour["conditions"]),
            precipitation,
            number(hour["wind_mph"], " mph"),
            cell(aqi),
            pm25,
        ]) + " |")

    lines += [
        "",
        "*Weather and air-quality data: [Open-Meteo](https://open-meteo.com/) "
        "([CC BY 4.0](https://creativecommons.org/licenses/by/4.0/)). "
        "ZIP geocoding: [Zippopotam.us](https://api.zippopotam.us/).*",
    ]
    return "\n".join(lines) + "\n"


def local_now(forecast_json):
    timezone_name = forecast_json.get("timezone")
    try:
        timezone = ZoneInfo(timezone_name) if timezone_name else datetime.timezone.utc
    except ZoneInfoNotFoundError:
        print(f"WARNING: unknown Open-Meteo timezone {timezone_name!r}; using UTC",
              file=sys.stderr)
        timezone = datetime.timezone.utc
    return datetime.datetime.now(timezone)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("zip_code")
    parser.add_argument("--past-days", type=int, default=7)
    parser.add_argument("--forecast-days", type=int, default=7)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()
    if not 0 <= args.past_days <= 92:
        parser.error("--past-days must be between 0 and 92")
    if not 1 <= args.forecast_days <= 16:
        parser.error("--forecast-days must be between 1 and 16")

    try:
        place = geocode.resolve(args.zip_code)
    except ValueError as exc:
        parser.error(str(exc))
    forecast = fetch_forecast(
        place["latitude"], place["longitude"], args.past_days, args.forecast_days)
    air_quality = fetch_aqi(
        place["latitude"], place["longitude"], args.past_days, args.forecast_days)
    now = local_now(forecast)
    now_iso_hour = now.strftime("%Y-%m-%dT%H:00")
    generated_at = now.isoformat(timespec="seconds")
    hours = build_hours(forecast, hourly_aqi_by_time(air_quality), now_iso_hour)
    markdown = render_markdown(place, hours, now.strftime("%Y-%m-%d %H:%M %Z"))
    title = f"US Weather — {place['zip']} ({place['place']}, {place['state_abbr']})"

    os.makedirs(args.out, exist_ok=True)
    note_path = os.path.abspath(os.path.join(args.out, f"{place['zip']}.md"))
    with open(note_path, "w", encoding="utf-8") as note_file:
        note_file.write(markdown)
    content_hash = hashlib.sha256(markdown.encode("utf-8")).hexdigest()
    entry = {
        "id": place["zip"],
        "zip": place["zip"],
        "note_title": title,
        "file": note_path,
        "generated_at": generated_at,
        "content_sha256": content_hash,
        "hour_count": len(hours),
        "unhealthy_hours": sum(
            1 for hour in hours if (hour.get("us_aqi") or 0) > 150),
    }
    manifest = {
        "feed": "us-weather",
        "past_days": args.past_days,
        "forecast_days": args.forecast_days,
        "place": place,
        "generated_at": generated_at,
        "hours": hours,
        "entries": [entry],
    }
    manifest_path = os.path.abspath(os.path.join(args.out, "manifest.json"))
    index_path = os.path.abspath(os.path.join(args.out, "index.json"))
    with open(manifest_path, "w", encoding="utf-8") as manifest_file:
        json.dump(manifest, manifest_file, indent=2, ensure_ascii=False)
    with open(index_path, "w", encoding="utf-8") as index_file:
        json.dump([entry], index_file, indent=2, ensure_ascii=False)
    print(json.dumps({
        "manifest": manifest_path,
        "index": index_path,
        "zip": place["zip"],
        "note_title": title,
        "hour_count": len(hours),
        "content_sha256": content_hash,
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
