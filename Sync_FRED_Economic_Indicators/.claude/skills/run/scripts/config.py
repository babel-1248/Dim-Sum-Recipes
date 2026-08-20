#!/usr/bin/env python3
"""Validate the FRED key shape and resolve an optional Markdown checklist."""

import argparse
import json
import os
from pathlib import Path
import re
import sys


API_KEY_RE = re.compile(r"[a-z0-9]{32}\Z", re.ASCII)
CHECKBOX_RE = re.compile(r"^\s*[-*]\s+\[([ xX])\]\s+(.+?)\s*$")

INDICATORS = (
    {
        "key": "headline-cpi",
        "label": "Headline CPI",
        "category": "Inflation",
        "release_id": 10,
        "release_name": "Consumer Price Index",
        "series_id": "CPIAUCSL",
    },
    {
        "key": "core-cpi",
        "label": "Core CPI",
        "category": "Inflation",
        "release_id": 10,
        "release_name": "Consumer Price Index",
        "series_id": "CPILFESL",
    },
    {
        "key": "pce-inflation",
        "label": "PCE inflation",
        "category": "Inflation",
        "release_id": 54,
        "release_name": "Personal Income and Outlays",
        "series_id": "PCEPI",
    },
    {
        "key": "core-pce-inflation",
        "label": "Core PCE inflation",
        "category": "Inflation",
        "release_id": 54,
        "release_name": "Personal Income and Outlays",
        "series_id": "PCEPILFE",
    },
    {
        "key": "unemployment-rate",
        "label": "Unemployment rate",
        "category": "Labor market",
        "release_id": 50,
        "release_name": "Employment Situation",
        "series_id": "UNRATE",
    },
    {
        "key": "payroll-employment",
        "label": "Payroll employment",
        "category": "Labor market",
        "release_id": 50,
        "release_name": "Employment Situation",
        "series_id": "PAYEMS",
    },
    {
        "key": "initial-unemployment-claims",
        "label": "Initial unemployment claims",
        "category": "Labor market",
        "release_id": 180,
        "release_name": "Unemployment Insurance Weekly Claims Report",
        "series_id": "ICSA",
    },
    {
        "key": "real-gdp",
        "label": "Real GDP",
        "category": "Growth and activity",
        "release_id": 53,
        "release_name": "Gross Domestic Product",
        "series_id": "GDPC1",
    },
    {
        "key": "industrial-production",
        "label": "Industrial production",
        "category": "Growth and activity",
        "release_id": 13,
        "release_name": "G.17 Industrial Production and Capacity Utilization",
        "series_id": "INDPRO",
    },
    {
        "key": "retail-sales",
        "label": "Retail sales",
        "category": "Growth and activity",
        "release_id": 9,
        "release_name": "Advance Monthly Sales for Retail and Food Services",
        "series_id": "RSAFS",
    },
    {
        "key": "housing-starts",
        "label": "Housing starts",
        "category": "Housing",
        "release_id": 27,
        "release_name": "New Residential Construction",
        "series_id": "HOUST",
    },
    {
        "key": "building-permits",
        "label": "Building permits",
        "category": "Housing",
        "release_id": 27,
        "release_name": "New Residential Construction",
        "series_id": "PERMIT",
    },
    {
        "key": "federal-funds-rate",
        "label": "Federal funds rate",
        "category": "Interest rates",
        "release_id": 18,
        "release_name": "H.15 Selected Interest Rates",
        "series_id": "FEDFUNDS",
    },
    {
        "key": "ten-year-treasury-yield",
        "label": "10-year Treasury yield",
        "category": "Interest rates",
        "release_id": 18,
        "release_name": "H.15 Selected Interest Rates",
        "series_id": "GS10",
    },
)

DEFAULT_KEYS = (
    "headline-cpi",
    "core-cpi",
    "unemployment-rate",
    "payroll-employment",
    "real-gdp",
    "retail-sales",
)

HISTORY_OPTIONS = {
    "latest and previous observation": "latest",
    "last 13 observations": "13",
    "last 5 years": "5y",
}


def normalize_label(value):
    return re.sub(r"\s+", " ", value).strip().casefold()


INDICATORS_BY_LABEL = {
    normalize_label(indicator["label"]): indicator for indicator in INDICATORS
}
INDICATORS_BY_KEY = {indicator["key"]: indicator for indicator in INDICATORS}


def validate_api_key(value):
    if not value:
        raise ValueError("FRED_API_KEY is required")
    if not API_KEY_RE.fullmatch(value):
        raise ValueError(
            "FRED_API_KEY must be exactly 32 lowercase ASCII letters or digits"
        )


def default_configuration():
    return {
        "filter_source": "defaults",
        "history": "latest",
        "indicators": [dict(INDICATORS_BY_KEY[key]) for key in DEFAULT_KEYS],
        "warnings": [],
    }


def parse_checklist(contents):
    checkbox_count = 0
    selected = []
    selected_keys = set()
    history = []
    unknown = []

    for line_number, line in enumerate(contents.splitlines(), start=1):
        match = CHECKBOX_RE.match(line)
        if not match:
            continue
        checkbox_count += 1
        checked = match.group(1).casefold() == "x"
        if not checked:
            continue
        raw_label = match.group(2).strip()
        label = normalize_label(raw_label)
        indicator = INDICATORS_BY_LABEL.get(label)
        if indicator:
            if indicator["key"] not in selected_keys:
                selected.append(dict(indicator))
                selected_keys.add(indicator["key"])
            continue
        history_mode = HISTORY_OPTIONS.get(label)
        if history_mode:
            history.append((line_number, raw_label, history_mode))
            continue
        unknown.append((line_number, raw_label))

    if checkbox_count == 0:
        raise ValueError("FILTER_FILE contains no Markdown checklist items")
    if unknown:
        examples = ", ".join(
            f"line {line_number}: {label!r}" for line_number, label in unknown[:5]
        )
        raise ValueError(f"FILTER_FILE has unknown checked options: {examples}")
    if not selected:
        raise ValueError("FILTER_FILE must check at least one economic indicator")

    warnings = []
    history_mode = history[0][2] if history else "latest"
    if len(history) > 1:
        warnings.append(
            "Several history choices were checked; using the topmost checked choice "
            f"{history[0][1]!r}."
        )
    return {
        "filter_source": "configured",
        "history": history_mode,
        "indicators": selected,
        "warnings": warnings,
    }


def load_configuration(environment=None):
    environment = os.environ if environment is None else environment
    validate_api_key(environment.get("FRED_API_KEY"))
    path = (environment.get("FILTER_FILE") or "").strip()
    if not path:
        return default_configuration()
    try:
        contents = Path(path).read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise ValueError(f"cannot read FILTER_FILE: {exc}") from exc
    if not contents.strip():
        return default_configuration()
    return parse_checklist(contents)


def write_configuration(path, configuration):
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(f"{destination.name}.tmp.{os.getpid()}")
    with temporary.open("w", encoding="utf-8") as output:
        json.dump(configuration, output, indent=2, ensure_ascii=False)
        output.write("\n")
        output.flush()
        os.fsync(output.fileno())
    os.replace(temporary, destination)


def summary(configuration, path):
    return {
        "config": str(Path(path).resolve()),
        "filter_source": configuration["filter_source"],
        "history": configuration["history"],
        "indicator_count": len(configuration["indicators"]),
        "release_count": len({item["release_id"] for item in configuration["indicators"]}),
        "indicators": [item["label"] for item in configuration["indicators"]],
        "warnings": configuration["warnings"],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()
    try:
        configuration = load_configuration()
        write_configuration(args.out, configuration)
        print(json.dumps(summary(configuration, args.out), ensure_ascii=False))
    except ValueError as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        raise SystemExit(2) from exc
    except OSError as exc:
        print(f"Configuration write error: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc


if __name__ == "__main__":
    main()
