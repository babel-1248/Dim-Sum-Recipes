#!/usr/bin/env python3
"""Render selected FRED series as one deterministic Markdown snapshot."""

import argparse
import calendar
import datetime as dt
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
import hashlib
import json
import os
from pathlib import Path
import re
import sys


FRED_HOME = "https://fred.stlouisfed.org/"
FRED_API_DOCS = "https://fred.stlouisfed.org/docs/api/fred/v2/"
FRED_TERMS = "https://fred.stlouisfed.org/docs/api/terms_of_use.html"
DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}\Z", re.ASCII)
PRICE_INDEX_SERIES = {"CPIAUCSL", "CPILFESL", "PCEPI", "PCEPILFE"}
DISPLAY_LABELS = {
    "CPIAUCSL": "Consumer Price Index (headline)",
    "CPILFESL": "Consumer Price Index (core)",
    "PCEPI": "PCE price index",
    "PCEPILFE": "Core PCE price index",
}


def safe_cell(value):
    return str(value).replace("|", "\\|").replace("\n", " ").strip()


def parse_value(value, series_id, date):
    if value == ".":
        return None
    try:
        number = Decimal(value)
    except InvalidOperation as exc:
        raise ValueError(f"{series_id} has a non-numeric value on {date}: {value!r}") from exc
    if not number.is_finite():
        raise ValueError(f"{series_id} has a non-finite value on {date}")
    return number


def parsed_observations(series):
    result = []
    for observation in series.get("observations") or []:
        date_text = observation.get("date") if isinstance(observation, dict) else None
        value_text = observation.get("value") if isinstance(observation, dict) else None
        if not isinstance(date_text, str) or not DATE_RE.fullmatch(date_text):
            raise ValueError(f"{series.get('series_id')} has an invalid observation date")
        if not isinstance(value_text, str):
            raise ValueError(f"{series.get('series_id')} has an invalid observation value")
        number = parse_value(value_text, series.get("series_id"), date_text)
        if number is not None:
            result.append({
                "date": dt.date.fromisoformat(date_text),
                "date_text": date_text,
                "value": number,
                "value_text": value_text,
            })
    result.sort(key=lambda item: item["date"])
    if not result:
        raise ValueError(f"{series.get('series_id')} has no non-missing observations")
    dates = [item["date"] for item in result]
    if len(dates) != len(set(dates)):
        raise ValueError(f"{series.get('series_id')} has duplicate observation dates")
    return result


def shift_year(date, years):
    year = date.year + years
    day = min(date.day, calendar.monthrange(year, date.month)[1])
    return date.replace(year=year, day=day)


def annual_reference(observations, frequency):
    latest = observations[-1]
    target = shift_year(latest["date"], -1)
    candidates = [item for item in observations[:-1] if item["date"] <= target]
    if not candidates:
        return None
    candidate = candidates[-1]
    normalized = (frequency or "").casefold()
    if "daily" in normalized:
        tolerance = 10
    elif "weekly" in normalized:
        tolerance = 21
    elif "monthly" in normalized:
        tolerance = 62
    elif "quarter" in normalized:
        tolerance = 150
    elif "annual" in normalized or "year" in normalized:
        tolerance = 500
    else:
        tolerance = 62
    return candidate if (target - candidate["date"]).days <= tolerance else None


def format_decimal(value):
    text = format(value, ",f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return "0" if text in {"-0", ""} else text


def format_value(value, units):
    text = format_decimal(value)
    return f"{text}%" if "percent" in (units or "").casefold() else text


def signed(value):
    prefix = "+" if value > 0 else ""
    return f"{prefix}{format_decimal(value)}"


def format_percent(value):
    rounded = value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    prefix = "+" if rounded > 0 else ""
    return f"{prefix}{format_decimal(rounded)}%"


def absolute_change_text(difference, units, series_id):
    if series_id == "PAYEMS":
        return f"{signed(difference * 1000)} jobs"
    if (units or "").casefold() == "thousands of units":
        return f"{signed(difference * 1000)} units"
    return signed(difference)


def change_text(current, previous, units, series_id=None, annual=False):
    if previous is None:
        return "—"
    difference = current - previous
    if "percent" in (units or "").casefold():
        basis_points = (difference * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
        return f"{signed(difference)} pp ({signed(basis_points)} bp)"
    percent = None if previous == 0 else (current / previous - 1) * 100
    absolute = absolute_change_text(difference, units, series_id)
    if annual and series_id in PRICE_INDEX_SERIES and percent is not None:
        return f"{format_percent(percent)} ({signed(difference)} index points)"
    return absolute if percent is None else f"{absolute} ({format_percent(percent)})"


def display_label(indicator, series):
    return DISPLAY_LABELS.get(series.get("series_id"), indicator["label"])


def select_history(observations, mode):
    if mode == "latest":
        return observations[-2:]
    if mode == "13":
        return observations[-13:]
    if mode == "5y":
        cutoff = shift_year(observations[-1]["date"], -5)
        return [item for item in observations if item["date"] >= cutoff]
    raise ValueError(f"unknown history mode {mode!r}")


def release_and_series(manifest):
    releases = manifest.get("releases")
    indicators = manifest.get("indicators")
    if not isinstance(releases, list) or not isinstance(indicators, list) or not indicators:
        raise ValueError("manifest has no selected releases or indicators")
    release_map = {}
    for release in releases:
        if not isinstance(release, dict) or not isinstance(release.get("release_id"), int):
            raise ValueError("manifest contains invalid release metadata")
        series_map = {}
        for series in release.get("series") or []:
            series_id = series.get("series_id") if isinstance(series, dict) else None
            if not isinstance(series_id, str) or series_id in series_map:
                raise ValueError("manifest contains invalid or duplicate series metadata")
            series_map[series_id] = series
        release_map[release["release_id"]] = (release, series_map)

    documents = []
    for indicator in indicators:
        try:
            release, series_map = release_map[indicator["release_id"]]
            series = series_map[indicator["series_id"]]
        except (KeyError, TypeError) as exc:
            raise ValueError(
                f"manifest is missing selected series {indicator.get('series_id')!r}"
            ) from exc
        observations = parsed_observations(series)
        documents.append({
            "indicator": indicator,
            "release": release,
            "series": series,
            "observations": observations,
            "previous": observations[-2] if len(observations) > 1 else None,
            "annual": annual_reference(observations, series.get("frequency")),
        })
    return documents


def release_url(release):
    return f"https://fred.stlouisfed.org/release?rid={release['release_id']}"


def build_markdown(manifest):
    mode = manifest.get("history")
    if mode not in {"latest", "13", "5y"}:
        raise ValueError("manifest has an invalid history mode")
    documents = release_and_series(manifest)
    latest_date = max(item["observations"][-1]["date_text"] for item in documents)
    updated_values = [
        item["series"].get("last_updated", "") for item in documents
        if item["series"].get("last_updated")
    ]
    latest_update = max(updated_values) if updated_values else latest_date
    update_date = latest_update[:10] if DATE_RE.fullmatch(latest_update[:10]) else latest_date
    title = f"FRED Economic Indicators — {update_date} update"

    history_labels = {
        "latest": "Latest and previous observation",
        "13": "Last 13 observations",
        "5y": "Last 5 years",
    }
    lines = [
        "# FRED Economic Indicators",
        "",
        "A point-in-time snapshot of the selected economic indicators from the Federal Reserve Bank of St. Louis.",
        "",
        f"**Most recent observation in this snapshot:** {latest_date}  ",
        f"**Most recent FRED update among selected series:** {safe_cell(latest_update)}  ",
        f"**History selection:** {history_labels[mode]}",
        "",
        "## Summary",
        "",
        "Each row has its own **As of** date. **Change vs prior observation** compares with the preceding weekly, monthly, or quarterly observation for that series. Rate changes are shown in percentage points (pp) and basis points (bp). For CPI and PCE price indexes, the percentage shown first under **Change vs year ago** is the year-over-year inflation rate. Payroll changes are expanded from thousands of persons to jobs.",
        "",
        "| Category | Indicator | As of | Latest level | Change vs prior observation | Change vs year ago | Units |",
        "| --- | --- | --- | ---: | ---: | ---: | --- |",
    ]

    for document in documents:
        indicator = document["indicator"]
        series = document["series"]
        latest = document["observations"][-1]
        previous = document["previous"]
        annual = document["annual"]
        units = series.get("units", "")
        series_link = f"https://fred.stlouisfed.org/series/{series['series_id']}"
        label = f"[{safe_cell(display_label(indicator, series))}]({series_link})"
        lines.append(
            f"| {safe_cell(indicator['category'])} | {label} | {latest['date_text']} | "
            f"{format_value(latest['value'], units)} | "
            f"{change_text(latest['value'], previous['value'] if previous else None, units, series['series_id'])} | "
            f"{change_text(latest['value'], annual['value'] if annual else None, units, series['series_id'], annual=True)} | "
            f"{safe_cell(units or 'Not reported')} |"
        )

    current_category = None
    for document in documents:
        indicator = document["indicator"]
        release = document["release"]
        series = document["series"]
        observations = document["observations"]
        label = display_label(indicator, series)
        if indicator["category"] != current_category:
            current_category = indicator["category"]
            lines.extend(["", f"## {current_category}", ""])
        series_link = f"https://fred.stlouisfed.org/series/{series['series_id']}"
        lines.extend([
            f"### [{label}]({series_link})",
            "",
            safe_cell(series.get("title") or label),
            "",
            f"- **Series:** `{series['series_id']}`",
            f"- **Release:** [{release['name']}]({release_url(release)})",
            f"- **Frequency:** {safe_cell(series.get('frequency') or 'Not reported')}",
            f"- **Units:** {safe_cell(series.get('units') or 'Not reported')}",
            f"- **Seasonal adjustment:** {safe_cell(series.get('seasonal_adjustment') or 'Not reported')}",
            f"- **FRED last updated:** {safe_cell(series.get('last_updated') or 'Not reported')}",
            f"- **Copyright:** {safe_cell(series.get('copyright_id') or 'Not reported')}",
        ])
        if mode != "latest":
            lines.extend(["", "| Observation date | Reported level |", "| --- | ---: |"])
            for observation in reversed(select_history(observations, mode)):
                lines.append(
                    f"| {observation['date_text']} | "
                    f"{format_value(observation['value'], series.get('units', ''))} |"
                )

    lines.extend([
        "",
        "## Sources and methodology",
        "",
    ])
    seen_releases = set()
    for document in documents:
        release = document["release"]
        release_id = release["release_id"]
        if release_id in seen_releases:
            continue
        seen_releases.add(release_id)
        publishers = []
        for source in release.get("sources") or []:
            name = safe_cell(source.get("name") or "Publisher")
            url = source.get("url")
            publishers.append(f"[{name}]({url})" if url else name)
        publisher_text = f" — {', '.join(publishers)}" if publishers else ""
        lines.append(f"- [{release['name']}]({release_url(release)}){publisher_text}")
    lines.extend([
        f"- [FRED API Version 2 documentation]({FRED_API_DOCS})",
        f"- [FRED API Terms of Use]({FRED_TERMS})",
        "",
        "Changes vs prior observation compare each series with its immediately preceding non-missing FRED observation, so the interval follows that series' reporting frequency. Changes vs year ago use the closest available observation on or just before the same date one year earlier. For series reported as percentages, changes are shown in percentage points and basis points. For CPI and PCE price indexes, the year-over-year percentage change is the inflation rate. Absolute payroll changes are expanded from thousands of persons to jobs. Missing observations reported by FRED as `.` are omitted. Values can be revised by their publishers.",
        "",
    ])
    return title, "\n".join(lines)


def load_manifest(path):
    with open(path, encoding="utf-8") as source:
        manifest = json.load(source)
    if not isinstance(manifest, dict) or manifest.get("schema_version") != 1:
        raise ValueError("manifest schema mismatch")
    return manifest


def write_output(manifest, output_directory):
    title, body = build_markdown(manifest)
    digest = hashlib.sha256(f"{title}\0{body}".encode("utf-8")).hexdigest()
    snapshot_id = f"fred-{digest}"
    output = Path(output_directory)
    output.mkdir(parents=True, exist_ok=True)
    note_path = (output / f"fred-economic-indicators-{digest[:16]}.md").resolve()
    note_path.write_text(body, encoding="utf-8")
    latest_date = max(
        observation["date"]
        for release in manifest["releases"]
        for series in release["series"]
        for observation in series["observations"]
        if observation["value"] != "."
    )
    entry = {
        "id": snapshot_id,
        "note_title": title,
        "file": str(note_path),
        "source_url": FRED_HOME,
        "indicator_count": len(manifest["indicators"]),
        "release_count": len(manifest["releases"]),
        "through_date": latest_date,
    }
    index_path = (output / "index.json").resolve()
    index_path.write_text(json.dumps([entry], indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return index_path, entry


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()
    try:
        manifest = load_manifest(args.manifest)
        index_path, entry = write_output(manifest, args.out)
        print(json.dumps({
            "index": str(index_path),
            "snapshot_id": entry["id"],
            "note_title": entry["note_title"],
            "indicator_count": entry["indicator_count"],
            "release_count": entry["release_count"],
            "through_date": entry["through_date"],
        }, ensure_ascii=False))
    except (OSError, ValueError, KeyError, json.JSONDecodeError) as exc:
        print(f"FRED conversion error: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc


if __name__ == "__main__":
    main()
