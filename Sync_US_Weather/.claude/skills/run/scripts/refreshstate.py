#!/usr/bin/env python3
"""Durable create-then-delete replacement journal for Sync US Weather."""

import argparse
import contextlib
import datetime
import fcntl
import json
import os
import re


LOCK_TIMEOUT = 30.0
ZIP_RE = re.compile(r"^[0-9]{5}$")


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
    return {"feed": "us-weather", "active": {}, "staged": {}, "last_run": None}


def read():
    path, _ = paths()
    try:
        with open(path, encoding="utf-8") as state_file:
            value = json.load(state_file)
    except FileNotFoundError:
        return blank()
    except json.JSONDecodeError as exc:
        raise SystemExit(f"weather state is unreadable ({path}): {exc}") from exc
    data = blank()
    data.update(value)
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


def stage(args):
    with locked():
        data = read()
        item_key = key(args.destination, args.zip_code)
        existing = data["staged"].get(item_key)
        if existing and existing.get("new_note_id") != args.new_note_id:
            raise SystemExit(
                "a different replacement is already staged for this ZIP and destination")
        old_ids = list(dict.fromkeys(note_id for note_id in (args.old_note_id or [])
                                     if note_id))
        if args.new_note_id in old_ids:
            raise SystemExit("the new note ID cannot also be an old note ID")
        data["staged"][item_key] = {
            "destination": args.destination,
            "zip": args.zip_code,
            "title": args.title,
            "new_note_id": args.new_note_id,
            "old_note_ids": old_ids,
            "content_sha256": args.content_sha256,
            "generated_at": args.generated_at,
            "staged_at": datetime.datetime.now().astimezone().isoformat(timespec="seconds"),
        }
        write(data)
        return data["staged"][item_key]


def complete(destination, zip_code):
    with locked():
        data = read()
        item_key = key(destination, zip_code)
        staged = data["staged"].get(item_key)
        if not staged:
            raise SystemExit("no staged replacement for this ZIP and destination")
        active = dict(staged)
        active.pop("old_note_ids", None)
        active["completed_at"] = datetime.datetime.now().astimezone().isoformat(
            timespec="seconds")
        data["active"][item_key] = active
        del data["staged"][item_key]
        data["last_run"] = active
        write(data)
        return active


def reset():
    with locked():
        write(blank())


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("show")
    subparsers.add_parser("pending")

    active_parser = subparsers.add_parser("active")
    active_parser.add_argument("--destination", required=True)
    active_parser.add_argument("--zip", required=True, dest="zip_code")

    stage_parser = subparsers.add_parser("stage")
    stage_parser.add_argument("--destination", required=True)
    stage_parser.add_argument("--zip", required=True, dest="zip_code")
    stage_parser.add_argument("--title", required=True)
    stage_parser.add_argument("--new-note-id", required=True)
    stage_parser.add_argument("--old-note-id", action="append", default=[])
    stage_parser.add_argument("--content-sha256", required=True)
    stage_parser.add_argument("--generated-at", required=True)

    complete_parser = subparsers.add_parser("complete")
    complete_parser.add_argument("--destination", required=True)
    complete_parser.add_argument("--zip", required=True, dest="zip_code")
    subparsers.add_parser("reset")
    args = parser.parse_args()

    if args.command == "show":
        print(json.dumps(read(), indent=2, ensure_ascii=False))
    elif args.command == "pending":
        staged = list(read()["staged"].values())
        print(json.dumps({
            "count": len(staged),
            "terminal": not staged,
            "replacements": staged,
        }, ensure_ascii=False))
    elif args.command == "active":
        print(json.dumps(
            read()["active"].get(key(args.destination, args.zip_code)),
            ensure_ascii=False))
    elif args.command == "stage":
        print(json.dumps(stage(args), ensure_ascii=False))
    elif args.command == "complete":
        print(json.dumps(complete(args.destination, args.zip_code), ensure_ascii=False))
    elif args.command == "reset":
        reset()
        print("weather replacement state cleared")


if __name__ == "__main__":
    main()
