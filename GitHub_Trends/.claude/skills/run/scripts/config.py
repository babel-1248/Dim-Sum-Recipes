#!/usr/bin/env python3
"""Read and validate GitHub Trending recipe parameters from the environment."""

import json
import os
import sys


DATE_RANGES = {"daily", "weekly", "monthly"}
ANY_VALUES = {"", "*", "all", "any"}


def normalize_date_range(value):
    normalized = (value or "daily").strip().lower()
    if normalized not in DATE_RANGES:
        expected = ", ".join(sorted(DATE_RANGES))
        raise ValueError(f"DATE_RANGE must be one of: {expected}")
    return normalized


def normalize_optional(value):
    normalized = (value or "").strip()
    return None if normalized.casefold() in ANY_VALUES else normalized


def load_environment():
    return {
        "date_range": normalize_date_range(os.environ.get("DATE_RANGE")),
        "language": normalize_optional(os.environ.get("LANGUAGE")),
        "spoken_language": normalize_optional(os.environ.get("SPOKEN_LANGUAGE")),
    }


def main() -> None:
    try:
        print(json.dumps(load_environment(), ensure_ascii=False))
    except ValueError as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        raise SystemExit(2) from exc


if __name__ == "__main__":
    main()
