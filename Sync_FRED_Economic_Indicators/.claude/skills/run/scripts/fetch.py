#!/usr/bin/env python3
"""Fetch selected series from complete FRED Version 2 release payloads."""

import argparse
import datetime as dt
import json
import os
from pathlib import Path
import re
import sys

import httpclient


ENDPOINT = "https://api.stlouisfed.org/fred/v2/release/observations"
DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}\Z", re.ASCII)
SERIES_METADATA = (
    "series_id",
    "title",
    "frequency",
    "units",
    "seasonal_adjustment",
    "last_updated",
    "copyright_id",
    "notes",
)


def load_configuration(path):
    with open(path, encoding="utf-8") as source:
        configuration = json.load(source)
    if not isinstance(configuration, dict):
        raise ValueError("configuration must be a JSON object")
    if configuration.get("history") not in {"latest", "13", "5y"}:
        raise ValueError("configuration has an invalid history mode")
    indicators = configuration.get("indicators")
    if not isinstance(indicators, list) or not indicators:
        raise ValueError("configuration must contain selected indicators")
    required = {
        "key", "label", "category", "release_id", "release_name", "series_id"
    }
    seen_keys = set()
    seen_series = set()
    for indicator in indicators:
        if not isinstance(indicator, dict) or not required.issubset(indicator):
            raise ValueError("configuration contains an invalid indicator")
        if not isinstance(indicator["release_id"], int) or indicator["release_id"] < 1:
            raise ValueError("configuration contains an invalid release ID")
        if indicator["key"] in seen_keys or indicator["series_id"] in seen_series:
            raise ValueError("configuration contains duplicate indicators or series")
        seen_keys.add(indicator["key"])
        seen_series.add(indicator["series_id"])
    return configuration


def boolean(value, field):
    if isinstance(value, bool):
        return value
    if isinstance(value, str) and value.casefold() in {"true", "false"}:
        return value.casefold() == "true"
    raise RuntimeError(f"FRED returned invalid {field!r}: {value!r}")


def clean_source(source):
    if not isinstance(source, dict) or not isinstance(source.get("name"), str):
        raise RuntimeError("FRED returned invalid release source metadata")
    result = {"name": source["name"]}
    for key in ("url", "notes"):
        value = source.get(key)
        if value is not None:
            if not isinstance(value, str):
                raise RuntimeError("FRED returned invalid release source metadata")
            result[key] = value
    return result


def clean_release(raw, expected_id, expected_name):
    if not isinstance(raw, dict):
        raise RuntimeError("FRED returned no release metadata")
    try:
        release_id = int(raw.get("release_id"))
    except (TypeError, ValueError) as exc:
        raise RuntimeError("FRED returned an invalid release ID") from exc
    name = raw.get("name")
    if release_id != expected_id:
        raise RuntimeError(
            f"FRED returned release {release_id}, expected {expected_id}"
        )
    if not isinstance(name, str) or name.casefold() != expected_name.casefold():
        raise RuntimeError(
            f"FRED release {expected_id} is {name!r}, expected {expected_name!r}"
        )
    url = raw.get("url")
    if url is not None and not isinstance(url, str):
        raise RuntimeError("FRED returned an invalid release URL")
    sources = raw.get("sources") or []
    if not isinstance(sources, list):
        raise RuntimeError("FRED returned invalid release sources")
    return {
        "release_id": release_id,
        "name": name,
        "url": url,
        "sources": [clean_source(source) for source in sources],
    }


def clean_series(raw):
    if not isinstance(raw, dict) or not isinstance(raw.get("series_id"), str):
        raise RuntimeError("FRED returned invalid series metadata")
    result = {}
    for key in SERIES_METADATA:
        value = raw.get(key, "")
        if not isinstance(value, str):
            raise RuntimeError(
                f"FRED returned invalid {key} for series {raw.get('series_id')!r}"
            )
        result[key] = value
    observations = raw.get("observations")
    if not isinstance(observations, list):
        raise RuntimeError(
            f"FRED returned no observations for series {result['series_id']}"
        )
    cleaned = []
    for observation in observations:
        if not isinstance(observation, dict):
            raise RuntimeError(f"FRED returned an invalid observation for {result['series_id']}")
        date = observation.get("date")
        value = observation.get("value")
        if not isinstance(date, str) or not DATE_RE.fullmatch(date):
            raise RuntimeError(f"FRED returned an invalid observation date for {result['series_id']}")
        if not isinstance(value, str):
            raise RuntimeError(f"FRED returned an invalid observation value for {result['series_id']}")
        cleaned.append({"date": date, "value": value})
    result["observations"] = cleaned
    return result


def merge_series(existing, incoming):
    for key in SERIES_METADATA:
        if existing[key] != incoming[key]:
            raise RuntimeError(
                f"FRED changed {key} for {existing['series_id']} during pagination; rerun"
            )
    values = {item["date"]: item["value"] for item in existing["observations"]}
    for observation in incoming["observations"]:
        previous = values.get(observation["date"])
        if previous is not None and previous != observation["value"]:
            raise RuntimeError(
                f"FRED returned conflicting values for {existing['series_id']} "
                f"on {observation['date']}"
            )
        values[observation["date"]] = observation["value"]
    existing["observations"] = [
        {"date": date, "value": values[date]} for date in sorted(values)
    ]


def fetch_release(client, release_id, release_name, series_ids):
    wanted = set(series_ids)
    found = {}
    release = None
    cursor = None
    seen_cursors = set()
    pages = 0

    while True:
        params = {
            "release_id": release_id,
            "format": "json",
            "limit": 500000,
        }
        if cursor is not None:
            params["next_cursor"] = cursor
        payload = client.get_json(ENDPOINT, params=params)
        pages += 1
        if pages > 1000:
            raise RuntimeError(f"FRED release {release_id} exceeded 1000 cursor pages")
        if not isinstance(payload, dict):
            raise RuntimeError(f"FRED release {release_id} response is not a JSON object")

        current_release = clean_release(payload.get("release"), release_id, release_name)
        if release is None:
            release = current_release
        elif release != current_release:
            raise RuntimeError(
                f"FRED changed release {release_id} metadata during pagination; rerun"
            )

        raw_series = payload.get("series")
        if not isinstance(raw_series, list):
            raise RuntimeError(f"FRED release {release_id} has no series collection")
        for raw in raw_series:
            series_id = raw.get("series_id") if isinstance(raw, dict) else None
            if series_id not in wanted:
                continue
            incoming = clean_series(raw)
            if series_id in found:
                merge_series(found[series_id], incoming)
            else:
                found[series_id] = incoming

        has_more = boolean(payload.get("has_more"), "has_more")
        if not has_more:
            break
        next_cursor = payload.get("next_cursor")
        if not isinstance(next_cursor, str) or not next_cursor:
            raise RuntimeError(f"FRED release {release_id} omitted its next cursor")
        if next_cursor in seen_cursors:
            raise RuntimeError(f"FRED release {release_id} repeated cursor {next_cursor!r}")
        seen_cursors.add(next_cursor)
        cursor = next_cursor

    missing = [series_id for series_id in series_ids if series_id not in found]
    if missing:
        raise RuntimeError(
            f"FRED release {release_id} did not contain selected series: {', '.join(missing)}"
        )
    release["series"] = [found[series_id] for series_id in series_ids]
    release["pages"] = pages
    return release


def fetch_selected(configuration, client):
    grouped = {}
    for indicator in configuration["indicators"]:
        release_id = indicator["release_id"]
        group = grouped.setdefault(
            release_id,
            {"name": indicator["release_name"], "series_ids": []},
        )
        if group["name"] != indicator["release_name"]:
            raise ValueError(f"release {release_id} has conflicting configured names")
        group["series_ids"].append(indicator["series_id"])

    releases = []
    for release_id, group in grouped.items():
        releases.append(
            fetch_release(client, release_id, group["name"], group["series_ids"])
        )
    return releases


def write_manifest(output_directory, configuration, releases):
    output = Path(output_directory)
    output.mkdir(parents=True, exist_ok=True)
    path = output / "manifest.json"
    manifest = {
        "schema_version": 1,
        "fetched_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "filter_source": configuration.get("filter_source", "configured"),
        "history": configuration["history"],
        "warnings": configuration.get("warnings") or [],
        "indicators": configuration["indicators"],
        "releases": releases,
    }
    temporary = path.with_name(f"{path.name}.tmp.{os.getpid()}")
    with temporary.open("w", encoding="utf-8") as destination:
        json.dump(manifest, destination, indent=2, ensure_ascii=False)
        destination.write("\n")
        destination.flush()
        os.fsync(destination.fileno())
    os.replace(temporary, path)
    return path.resolve(), manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()
    api_key = os.environ.get("FRED_API_KEY") or ""
    try:
        configuration = load_configuration(args.config)
        client = httpclient.Client(api_key)
        releases = fetch_selected(configuration, client)
        path, manifest = write_manifest(args.out, configuration, releases)
        page_count = sum(release["pages"] for release in releases)
        selected_count = sum(len(release["series"]) for release in releases)
        print(json.dumps({
            "manifest": str(path),
            "filter_source": manifest["filter_source"],
            "history": manifest["history"],
            "release_count": len(releases),
            "page_count": page_count,
            "selected_series": selected_count,
        }, ensure_ascii=False))
    except (OSError, ValueError, RuntimeError, json.JSONDecodeError) as exc:
        message = str(exc).replace(api_key, "[REDACTED]") if api_key else str(exc)
        print(f"FRED fetch error: {message}", file=sys.stderr)
        raise SystemExit(1) from exc


if __name__ == "__main__":
    main()
