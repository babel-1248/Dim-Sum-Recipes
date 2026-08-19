#!/usr/bin/env python3
"""Locked, atomic, per-filter state for the GitHub Trending recipe."""

import argparse
import contextlib
import datetime as dt
import errno
import fcntl
import hashlib
import json
import os
import sys
import time


MAX_SEEN = 50_000
LOCK_TIMEOUT = 30.0


def state_directory():
    root = os.environ.get("GITHUB_TRENDING_STATE")
    if not root:
        project = os.environ.get("GITHUB_TRENDING_PROJECT_DIR") or os.getcwd()
        root = os.path.join(project, ".feed-state")
    os.makedirs(root, exist_ok=True)
    return root


def state_paths(scope):
    digest = hashlib.sha256(scope.encode("utf-8")).hexdigest()[:20]
    stem = os.path.join(state_directory(), f"github-trending-{digest}")
    return f"{stem}.json", f"{stem}.lock"


def blank(scope):
    return {"version": 1, "scope": scope, "seen_ids": [], "last_run": None}


def read_state(scope):
    path, _ = state_paths(scope)
    try:
        with open(path, encoding="utf-8") as source:
            data = json.load(source)
    except FileNotFoundError:
        return blank(scope)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"state file is not valid JSON: {path}") from exc
    if data.get("scope") != scope:
        raise RuntimeError(f"state scope mismatch in {path}")
    merged = blank(scope)
    merged.update(data)
    return merged


@contextlib.contextmanager
def locked(scope):
    _, lock_path = state_paths(scope)
    descriptor = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o644)
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


def write_atomic(scope, data):
    path, _ = state_paths(scope)
    temporary = f"{path}.tmp.{os.getpid()}"
    with open(temporary, "w", encoding="utf-8") as output:
        json.dump(data, output, indent=2, ensure_ascii=False)
        output.flush()
        os.fsync(output.fileno())
    os.replace(temporary, path)


def load_ids(path):
    if not path:
        return []
    with open(path, encoding="utf-8") as source:
        return [line.strip().casefold() for line in source if line.strip() and not line.startswith("#")]


def merge_seen(data, ids):
    seen = list(data.get("seen_ids") or [])
    known = set(seen)
    for item_id in ids:
        normalized = item_id.casefold()
        if normalized not in known:
            seen.append(normalized)
            known.add(normalized)
    data["seen_ids"] = seen[-MAX_SEEN:]


def mark_seen(scope, ids):
    with locked(scope):
        data = read_state(scope)
        merge_seen(data, ids)
        write_atomic(scope, data)
    return data


def filter_seen(scope, ids):
    seen = set(read_state(scope).get("seen_ids") or [])
    return [item_id for item_id in ids if item_id not in seen]


def load_index(path):
    with open(path, encoding="utf-8") as source:
        entries = json.load(source)
    if not isinstance(entries, list) or any(not isinstance(entry, dict) for entry in entries):
        raise ValueError(f"{path}: expected a JSON list of objects")
    ids = [entry.get("id", "").casefold() for entry in entries]
    if any(not item_id for item_id in ids):
        raise ValueError(f"{path}: every entry needs an id")
    if len(ids) != len(set(ids)):
        raise ValueError(f"{path}: duplicate ids make completion ambiguous")
    if any(not entry.get("note_title") or not entry.get("file") for entry in entries):
        raise ValueError(f"{path}: every entry needs note_title and file")
    return entries


def pending(scope, index_path, batch_size, failed_ids=()):
    entries = load_index(index_path)
    seen = set(read_state(scope).get("seen_ids") or [])
    failed = {item.casefold() for item in failed_ids}
    index_ids = {entry["id"].casefold() for entry in entries}
    ignored_failures = sorted(failed - index_ids)
    checkpointed = [entry for entry in entries if entry["id"].casefold() in seen]
    failed_entries = [
        entry for entry in entries
        if entry["id"].casefold() in failed and entry["id"].casefold() not in seen
    ]
    remaining = [
        entry for entry in entries
        if entry["id"].casefold() not in seen and entry["id"].casefold() not in failed
    ]
    return {
        "total": len(entries),
        "checkpointed": len(checkpointed),
        "failed": len(failed_entries),
        "remaining": len(remaining),
        "terminal": not remaining,
        "ignored_failures": ignored_failures,
        "batch": remaining[:batch_size],
    }


def record(scope, ids, fetched, written):
    with locked(scope):
        data = read_state(scope)
        merge_seen(data, ids)
        data["last_run"] = {
            "at": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
            "fetched": fetched,
            "written": written,
        }
        write_atomic(scope, data)
    return data


def mark_failed(path, item_id):
    existing = load_ids(path) if os.path.exists(path) else []
    normalized = item_id.casefold()
    if normalized not in existing:
        existing.append(normalized)
    temporary = f"{path}.tmp.{os.getpid()}"
    with open(temporary, "w", encoding="utf-8") as output:
        output.write("".join(f"{value}\n" for value in existing))
    os.replace(temporary, path)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)

    show = commands.add_parser("show")
    show.add_argument("--scope", required=True)

    fresh = commands.add_parser("filter-seen")
    fresh.add_argument("--scope", required=True)
    fresh.add_argument("--ids-file", required=True)
    fresh.add_argument("--out", required=True)

    seen = commands.add_parser("mark-seen")
    seen.add_argument("--scope", required=True)
    seen.add_argument("--id", action="append", required=True, dest="ids")

    failed = commands.add_parser("mark-failed")
    failed.add_argument("--file", required=True)
    failed.add_argument("--id", required=True)

    status = commands.add_parser("pending")
    status.add_argument("--scope", required=True)
    status.add_argument("--index", required=True)
    status.add_argument("--batch-size", type=int, default=5)
    status.add_argument("--exclude-file")

    completed = commands.add_parser("record")
    completed.add_argument("--scope", required=True)
    completed.add_argument("--ids-file")
    completed.add_argument("--fetched", type=int, required=True)
    completed.add_argument("--written", type=int, required=True)

    args = parser.parse_args()
    try:
        if args.command == "show":
            print(json.dumps(read_state(args.scope), indent=2, ensure_ascii=False))
        elif args.command == "filter-seen":
            ids = load_ids(args.ids_file)
            result = filter_seen(args.scope, ids)
            with open(args.out, "w", encoding="utf-8") as output:
                output.write("".join(f"{item_id}\n" for item_id in result))
            print(json.dumps({"fetched": len(ids), "fresh": len(result), "seen": len(ids) - len(result)}))
        elif args.command == "mark-seen":
            data = mark_seen(args.scope, args.ids)
            print(json.dumps({"checkpointed": len(args.ids), "seen": len(data["seen_ids"])}))
        elif args.command == "mark-failed":
            mark_failed(args.file, args.id)
            print(json.dumps({"failed_id": args.id.casefold(), "file": args.file}))
        elif args.command == "pending":
            if args.batch_size < 1:
                parser.error("--batch-size must be at least 1")
            failures = load_ids(args.exclude_file)
            print(json.dumps(pending(args.scope, args.index, args.batch_size, failures), ensure_ascii=False))
        elif args.command == "record":
            ids = load_ids(args.ids_file)
            data = record(args.scope, ids, args.fetched, args.written)
            print(json.dumps({"seen": len(data["seen_ids"]), "last_run": data["last_run"]}))
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"State error: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc


if __name__ == "__main__":
    main()
