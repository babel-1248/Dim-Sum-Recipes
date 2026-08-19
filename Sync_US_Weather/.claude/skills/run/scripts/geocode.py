#!/usr/bin/env python3
"""Resolve one five-digit US ZIP through Zippopotam.us and cache it."""

import contextlib
import fcntl
import json
import os
import re
import sys

from httpclient import CLIENT, HttpStatusError


CACHE_ENV = "WEATHER_FEED_CACHE"
ZIP_RE = re.compile(r"^[0-9]{5}$")


def normalize(zip_code):
    value = str(zip_code or "").strip()
    if not ZIP_RE.fullmatch(value):
        raise ValueError("ZIP_CODE must contain exactly five digits")
    return value


def cache_dir():
    root = os.environ.get(CACHE_ENV) or os.path.join(os.getcwd(), ".weather-cache")
    os.makedirs(root, exist_ok=True)
    return root


def cache_path():
    return os.path.join(cache_dir(), "zip_geo.json")


@contextlib.contextmanager
def cache_lock():
    path = os.path.join(cache_dir(), "zip_geo.lock")
    fd = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        with contextlib.suppress(OSError):
            fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


def _load_cache():
    try:
        with open(cache_path(), encoding="utf-8") as cache_file:
            value = json.load(cache_file)
        return value if isinstance(value, dict) else {}
    except FileNotFoundError:
        return {}
    except json.JSONDecodeError:
        print("WARNING: weather ZIP cache is unreadable; rebuilding it", file=sys.stderr)
        return {}


def _save_cache(data):
    path = cache_path()
    temporary = f"{path}.tmp.{os.getpid()}"
    fd = os.open(temporary, os.O_CREAT | os.O_WRONLY | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as cache_file:
        json.dump(data, cache_file, indent=2, ensure_ascii=False)
        cache_file.flush()
        os.fsync(cache_file.fileno())
    os.replace(temporary, path)
    os.chmod(path, 0o600)


def resolve(zip_code):
    zip_code = normalize(zip_code)
    with cache_lock():
        cached = _load_cache().get(zip_code)
    if cached:
        return cached

    url = f"https://api.zippopotam.us/us/{zip_code}"
    try:
        data = CLIENT.get_json(url)
    except HttpStatusError as exc:
        if exc.code == 404:
            raise SystemExit(f"ZIP {zip_code} was not found; check ZIP_CODE") from exc
        raise SystemExit(str(exc)) from exc
    places = data.get("places") if isinstance(data, dict) else None
    if not places:
        raise SystemExit(f"Zippopotam returned no location for ZIP {zip_code}")
    place = places[0]
    try:
        result = {
            "zip": zip_code,
            "place": str(place["place name"]),
            "state": str(place["state"]),
            "state_abbr": str(place["state abbreviation"]),
            "latitude": float(place["latitude"]),
            "longitude": float(place["longitude"]),
        }
    except (KeyError, TypeError, ValueError) as exc:
        raise SystemExit(f"Zippopotam returned an invalid location for ZIP {zip_code}") from exc

    with cache_lock():
        cache = _load_cache()
        result = cache.setdefault(zip_code, result)
        _save_cache(cache)
    return result


def main():
    if len(sys.argv) != 2:
        raise SystemExit("usage: geocode.py ZIP")
    print(json.dumps(resolve(sys.argv[1]), ensure_ascii=False))


if __name__ == "__main__":
    main()
