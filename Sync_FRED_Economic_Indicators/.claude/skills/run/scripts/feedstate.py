#!/usr/bin/env python3
"""Locked, atomic, destination-scoped state for FRED indicator snapshots."""

import argparse
import contextlib
import datetime as dt
import errno
import fcntl
import json
import os
import re
import sys
import time


LOCK_TIMEOUT = 30.0
STATE_FILENAME = "fred-economic-indicators.json"
SNAPSHOT_RE = re.compile(r"fred-[0-9a-f]{64}\Z", re.ASCII)


def state_directory():
    root = os.environ.get("FRED_INDICATORS_STATE")
    if not root:
        project = os.environ.get("FRED_INDICATORS_PROJECT_DIR") or os.getcwd()
        root = os.path.join(project, ".feed-state")
    os.makedirs(root, mode=0o700, exist_ok=True)
    return root


def state_paths():
    directory = state_directory()
    return (
        os.path.join(directory, STATE_FILENAME),
        os.path.join(directory, "fred-economic-indicators.lock"),
    )


def blank():
    return {"version": 1, "destinations": {}}


def validate_destination(destination):
    if not destination or "\n" in destination or "\r" in destination or len(destination) > 512:
        raise ValueError("destination must be a non-empty single-line value")


def validate_snapshot_id(snapshot_id):
    if not SNAPSHOT_RE.fullmatch(snapshot_id or ""):
        raise ValueError("snapshot ID has an invalid format")


def read_state():
    path, _ = state_paths()
    try:
        with open(path, encoding="utf-8") as source:
            data = json.load(source)
    except FileNotFoundError:
        return blank()
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"state file is not valid JSON: {path}") from exc
    if not isinstance(data, dict) or data.get("version") != 1:
        raise RuntimeError(f"state schema mismatch in {path}")
    destinations = data.get("destinations")
    if not isinstance(destinations, dict):
        raise RuntimeError(f"state destinations are invalid in {path}")
    for destination, entry in destinations.items():
        try:
            validate_destination(destination)
        except ValueError as exc:
            raise RuntimeError(f"state contains an invalid destination in {path}") from exc
        if not isinstance(entry, dict) or not isinstance(entry.get("snapshots"), dict):
            raise RuntimeError(f"state destination {destination!r} is invalid in {path}")
        for snapshot_id, record in entry["snapshots"].items():
            try:
                validate_snapshot_id(snapshot_id)
            except ValueError as exc:
                raise RuntimeError(f"state contains an invalid snapshot ID in {path}") from exc
            if not isinstance(record, dict) or not isinstance(record.get("note_id"), str):
                raise RuntimeError(f"state snapshot {snapshot_id!r} is invalid in {path}")
    return data


@contextlib.contextmanager
def locked():
    _, lock_path = state_paths()
    descriptor = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
    deadline = time.monotonic() + LOCK_TIMEOUT
    try:
        while True:
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError as exc:
                if exc.errno not in (errno.EAGAIN, errno.EACCES):
                    raise
                if time.monotonic() >= deadline:
                    raise RuntimeError(f"timed out waiting for state lock {lock_path}")
                time.sleep(0.1)
        yield
    finally:
        with contextlib.suppress(OSError):
            fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)


def write_atomic(data):
    path, _ = state_paths()
    temporary = f"{path}.tmp.{os.getpid()}"
    descriptor = os.open(temporary, os.O_CREAT | os.O_TRUNC | os.O_WRONLY, 0o600)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as output:
            json.dump(data, output, indent=2, ensure_ascii=False)
            output.write("\n")
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
        os.chmod(path, 0o600)
    except Exception:
        with contextlib.suppress(OSError):
            os.unlink(temporary)
        raise


def load_index(path):
    with open(path, encoding="utf-8") as source:
        entries = json.load(source)
    if not isinstance(entries, list) or len(entries) != 1 or not isinstance(entries[0], dict):
        raise ValueError("index must contain exactly one snapshot object")
    entry = entries[0]
    validate_snapshot_id(entry.get("id"))
    if not isinstance(entry.get("note_title"), str) or not entry["note_title"]:
        raise ValueError("index entry needs a note title")
    file_path = entry.get("file")
    if not isinstance(file_path, str) or not os.path.isabs(file_path) or not os.path.isfile(file_path):
        raise ValueError("index entry needs an existing absolute note file")
    return entries


def pending(destination, index_path):
    validate_destination(destination)
    entries = load_index(index_path)
    snapshots = (
        read_state().get("destinations", {}).get(destination, {}).get("snapshots", {})
    )
    checkpointed = [entry for entry in entries if entry["id"] in snapshots]
    remaining = [entry for entry in entries if entry["id"] not in snapshots]
    return {
        "total": 1,
        "checkpointed": len(checkpointed),
        "remaining": len(remaining),
        "terminal": not remaining,
        "batch": remaining,
    }


def mark_seen(destination, snapshot_id, note_id):
    validate_destination(destination)
    validate_snapshot_id(snapshot_id)
    if not note_id or "\n" in note_id or "\r" in note_id:
        raise ValueError("note ID must be a non-empty single-line value")
    with locked():
        data = read_state()
        entry = data["destinations"].setdefault(destination, {"snapshots": {}})
        snapshots = entry["snapshots"]
        existing = snapshots.get(snapshot_id)
        if existing is not None and existing["note_id"] != note_id:
            raise RuntimeError(
                f"snapshot {snapshot_id} is already checkpointed to another note"
            )
        if existing is None:
            snapshots[snapshot_id] = {
                "note_id": note_id,
                "created_at": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
            }
            write_atomic(data)
    return snapshots[snapshot_id]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)

    show = commands.add_parser("show")
    show.add_argument("--destination")

    status = commands.add_parser("pending")
    status.add_argument("--destination", required=True)
    status.add_argument("--index", required=True)

    seen = commands.add_parser("mark-seen")
    seen.add_argument("--destination", required=True)
    seen.add_argument("--id", required=True)
    seen.add_argument("--note-id", required=True)

    args = parser.parse_args()
    try:
        if args.command == "show":
            data = read_state()
            if args.destination:
                validate_destination(args.destination)
                result = data["destinations"].get(args.destination, {"snapshots": {}})
            else:
                result = data
            print(json.dumps(result, indent=2, ensure_ascii=False))
        elif args.command == "pending":
            print(json.dumps(pending(args.destination, args.index), ensure_ascii=False))
        elif args.command == "mark-seen":
            record = mark_seen(args.destination, args.id, args.note_id)
            print(json.dumps({
                "destination": args.destination,
                "snapshot_id": args.id,
                "note_id": record["note_id"],
                "created_at": record["created_at"],
            }, ensure_ascii=False))
    except (OSError, RuntimeError, ValueError, json.JSONDecodeError) as exc:
        print(f"State error: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc


if __name__ == "__main__":
    main()
