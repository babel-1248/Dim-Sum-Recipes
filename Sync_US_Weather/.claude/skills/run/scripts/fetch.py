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
MAX_WINDOW_SPAN_DAYS = 13


def fetch_forecast(latitude, longitude, start_date, end_date):
    return CLIENT.get_json(FORECAST_URL, {
        "latitude": latitude,
        "longitude": longitude,
        "hourly": HOURLY_FORECAST_VARS,
        "temperature_unit": "fahrenheit",
        "wind_speed_unit": "mph",
        "precipitation_unit": "inch",
        "timezone": "auto",
        "start_date": start_date,
        "end_date": end_date,
    })


def fetch_aqi(latitude, longitude, start_date, end_date):
    return CLIENT.get_json(AQI_URL, {
        "latitude": latitude,
        "longitude": longitude,
        "hourly": HOURLY_AQI_VARS,
        "timezone": "auto",
        "start_date": start_date,
        "end_date": end_date,
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


def render_markdown(place, hours, generated_at, start_date, end_date):
    location = f"{place['place']}, {place['state_abbr']}"
    lines = [
        f"**ZIP {place['zip']} — {cell(location)}** · "
        f"**{start_date} through {end_date}** · synced {generated_at}",
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


def iso_date(value, option):
    try:
        return datetime.date.fromisoformat(value)
    except (TypeError, ValueError) as exc:
        raise argparse.ArgumentTypeError(
            f"{option} must be an ISO date in YYYY-MM-DD form") from exc


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("zip_code")
    parser.add_argument("--start", required=True)
    parser.add_argument("--end", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()
    try:
        start = iso_date(args.start, "--start")
        end = iso_date(args.end, "--end")
    except argparse.ArgumentTypeError as exc:
        parser.error(str(exc))
    if start > end:
        parser.error("--start must not be after --end")
    if (end - start).days > MAX_WINDOW_SPAN_DAYS:
        parser.error("the inclusive weather window cannot exceed 14 calendar days")

    try:
        place = geocode.resolve(args.zip_code)
    except ValueError as exc:
        parser.error(str(exc))
    forecast = fetch_forecast(
        place["latitude"], place["longitude"], args.start, args.end)
    air_quality = fetch_aqi(
        place["latitude"], place["longitude"], args.start, args.end)
    now = local_now(forecast)
    now_iso_hour = now.strftime("%Y-%m-%dT%H:00")
    generated_at = now.isoformat(timespec="seconds")
    hours = build_hours(forecast, hourly_aqi_by_time(air_quality), now_iso_hour)
    markdown = render_markdown(
        place, hours, now.strftime("%Y-%m-%d %H:%M %Z"), args.start, args.end)
    title = (f"US Weather — {place['zip']} "
             f"({place['place']}, {place['state_abbr']}) — {args.start} to {args.end}")

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
        "window_start": args.start,
        "window_end": args.end,
        "hour_count": len(hours),
        "unhealthy_hours": sum(
            1 for hour in hours if (hour.get("us_aqi") or 0) > 150),
    }
    manifest = {
        "feed": "us-weather",
        "window_start": args.start,
        "window_end": args.end,
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
        "window_start": args.start,
        "window_end": args.end,
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
