#!/usr/bin/env python3
"""Store the last successful Sync US Weather note creation."""

import argparse
import contextlib
import datetime
import fcntl
import json
import os
import re


LOCK_TIMEOUT = 30.0
ZIP_RE = re.compile(r"^[0-9]{5}$")
MAX_LOOKBACK_DAYS = 7
MAX_DAYS_AHEAD = 6


def state_dir():
    root = os.environ.get("RESEARCH_FEED_STATE")
    if not root:
        root = os.path.join(
            os.environ.get("RESEARCH_PROJECT_DIR") or os.getcwd(), ".feed-state")
    os.makedirs(root, exist_ok=True)
    return root


def paths():
    root = state_dir()
    return os.path.join(root, "weather.json"), os.path.join(root, "weather.lock")


def blank():
    return {"feed": "us-weather", "last_successful": {}}


def read():
    path, _ = paths()
    try:
        with open(path, encoding="utf-8") as state_file:
            value = json.load(state_file)
    except FileNotFoundError:
        return blank()
    except json.JSONDecodeError as exc:
        raise SystemExit(f"weather state is unreadable ({path}): {exc}") from exc
    if not isinstance(value, dict):
        raise SystemExit(f"weather state must be a JSON object ({path})")
    data = blank()
    if isinstance(value.get("last_successful"), dict):
        data["last_successful"] = value["last_successful"]
    return data


@contextlib.contextmanager
def locked():
    _, lock_path = paths()
    fd = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
    deadline = datetime.datetime.now().timestamp() + LOCK_TIMEOUT
    try:
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if datetime.datetime.now().timestamp() > deadline:
                    raise SystemExit("timed out waiting for the weather state lock")
                import time
                time.sleep(0.1)
        yield
    finally:
        with contextlib.suppress(OSError):
            fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


def write(data):
    path, _ = paths()
    temporary = f"{path}.tmp.{os.getpid()}"
    fd = os.open(temporary, os.O_CREAT | os.O_WRONLY | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as state_file:
        json.dump(data, state_file, indent=2, ensure_ascii=False)
        state_file.flush()
        os.fsync(state_file.fileno())
    os.replace(temporary, path)
    os.chmod(path, 0o600)


def key(destination, zip_code):
    if not destination or "\n" in destination or "\r" in destination:
        raise SystemExit("destination token must be non-empty and contain no newlines")
    if not ZIP_RE.fullmatch(zip_code or ""):
        raise SystemExit("ZIP must contain exactly five digits")
    return f"{destination}\n{zip_code}"


def parse_date(value, label):
    try:
        return datetime.date.fromisoformat(value)
    except (TypeError, ValueError) as exc:
        raise SystemExit(f"{label} must be an ISO date in YYYY-MM-DD form") from exc


def parse_days_ahead(value):
    text = str(value)
    if not re.fullmatch(r"[0-9]+", text):
        raise SystemExit("days ahead must be an integer from 0 through 6")
    days = int(text)
    if days > MAX_DAYS_AHEAD:
        raise SystemExit("days ahead must be an integer from 0 through 6")
    return days


def resolve_window(destination, zip_code, today=None, days_ahead=1):
    today_date = parse_date(today, "today") if today else datetime.date.today()
    future_days = parse_days_ahead(days_ahead)
    successful = read()["last_successful"].get(key(destination, zip_code))
    previous_value = successful.get("sync_date") if successful else None
    floor = today_date - datetime.timedelta(days=MAX_LOOKBACK_DAYS)
    if previous_value:
        previous_date = parse_date(previous_value, "last successful sync date")
        start_date = min(max(previous_date, floor), today_date)
    else:
        previous_date = None
        start_date = floor
    return {
        "today": today_date.isoformat(),
        "start": start_date.isoformat(),
        "end": (today_date + datetime.timedelta(days=future_days)).isoformat(),
        "previous_sync_date": (
            previous_date.isoformat() if previous_date is not None else None),
        "first_run": previous_date is None,
        "max_lookback_days": MAX_LOOKBACK_DAYS,
        "days_ahead": future_days,
    }


def record_success(args):
    if not args.note_id or "\n" in args.note_id or "\r" in args.note_id:
        raise SystemExit("note ID must be non-empty and contain no newlines")
    sync_date = parse_date(args.sync_date, "sync date")

    with locked():
        data = read()
        item_key = key(args.destination, args.zip_code)
        values = {
            "destination": args.destination,
            "zip": args.zip_code,
            "sync_date": sync_date.isoformat(),
            "note_id": args.note_id,
        }
        existing = data["last_successful"].get(item_key)
        if existing and all(existing.get(name) == value
                            for name, value in values.items()):
            return existing
        successful = {
            **values,
            "recorded_at": datetime.datetime.now().astimezone().isoformat(
                timespec="seconds"),
        }
        data["last_successful"][item_key] = successful
        write(data)
        return successful


def reset():
    with locked():
        write(blank())


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("show")

    window_parser = subparsers.add_parser("window")
    window_parser.add_argument("--destination", required=True)
    window_parser.add_argument("--zip", required=True, dest="zip_code")
    window_parser.add_argument("--today")
    window_parser.add_argument("--days-ahead", default="1")

    success_parser = subparsers.add_parser("record-success")
    success_parser.add_argument("--destination", required=True)
    success_parser.add_argument("--zip", required=True, dest="zip_code")
    success_parser.add_argument("--sync-date", required=True)
    success_parser.add_argument("--note-id", required=True)
    subparsers.add_parser("reset")
    args = parser.parse_args()

    if args.command == "show":
        print(json.dumps(read(), indent=2, ensure_ascii=False))
    elif args.command == "window":
        print(json.dumps(
            resolve_window(
                args.destination, args.zip_code, args.today, args.days_ahead),
            ensure_ascii=False))
    elif args.command == "record-success":
        print(json.dumps(record_success(args), ensure_ascii=False))
    elif args.command == "reset":
        reset()
        print("weather success state cleared")


if __name__ == "__main__":
    main()
