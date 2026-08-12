#!/usr/bin/env python3
"""Per-feed sync state, safe for concurrent agents.

One JSON file per feed under <project>/.feed-state/. Two independent agents may
run this skill at the same time, so every mutation takes an exclusive lock and
every write is atomic:

  * fcntl.flock on a sibling .lock file serialises read-modify-write, so a second
    agent blocks rather than clobbering the first agent's watermark.
  * The payload is written to a temp file in the same directory and os.replace'd
    into position, which is atomic on POSIX. A reader therefore sees either the
    old file or the new one, never a half-written one.
  * Readers take no lock, so reporting never blocks a sync.

State recorded per feed:
  watermark    latest item date successfully written as a note
  next_since   what a no-argument run should use as --since
  seen_ids     ids already written, newest last (bounded), for dedupe
  last_run     window, counts and timestamp of the most recent run

next_since deliberately re-scans the boundary day rather than starting the day
after. Items are published throughout a day, so starting at watermark+1 would
silently drop anything that landed after the previous run. Re-scanning is safe
because seen_ids removes what was already written.

CLI:
  python3 feedstate.py show [FEED]
  python3 feedstate.py since FEED [--default YYYY-MM-DD | --default-one-month]
  python3 feedstate.py filter-seen FEED --ids-file IDS      # drop already-written
  python3 feedstate.py record FEED --watermark D --until D --ids-file IDS \
                                   [--fetched N] [--kept N]
  python3 feedstate.py reset FEED
"""
import argparse
import calendar
import contextlib
import datetime
import errno
import fcntl
import json
import os
import sys

FEEDS = ["executive", "house", "senate"]
MAX_SEEN = 2000          # bounded so the file cannot grow without limit
LOCK_TIMEOUT = 30.0      # seconds to wait for another agent to finish


def one_calendar_month_ago(today=None):
    """Return the same day last month, clamped to that month's final day."""
    today = today or datetime.date.today()
    year = today.year
    month = today.month - 1
    if month == 0:
        year -= 1
        month = 12
    day = min(today.day, calendar.monthrange(year, month)[1])
    return datetime.date(year, month, day).isoformat()


def state_dir():
    """<project>/.feed-state, overridable for tests."""
    root = os.environ.get("US_GOV_FEED_STATE")
    if not root:
        root = os.path.join(os.environ.get("US_GOV_PROJECT_DIR")
                            or os.getcwd(), ".feed-state")
    os.makedirs(root, exist_ok=True)
    return root


def _paths(feed):
    d = state_dir()
    return os.path.join(d, f"{feed}.json"), os.path.join(d, f"{feed}.lock")


@contextlib.contextmanager
def _locked(feed):
    """Exclusive cross-process lock for read-modify-write."""
    _, lock_path = _paths(feed)
    fd = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o644)
    deadline = datetime.datetime.now().timestamp() + LOCK_TIMEOUT
    try:
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError as exc:
                if exc.errno not in (errno.EAGAIN, errno.EACCES):
                    raise
                if datetime.datetime.now().timestamp() > deadline:
                    raise SystemExit(
                        f"timed out waiting for the {feed} feed lock "
                        f"({lock_path}); another agent may be mid-sync")
                # Brief spin; syncs are minutes long but writes are milliseconds.
                import time
                time.sleep(0.1)
        yield
    finally:
        with contextlib.suppress(OSError):
            fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


def blank(feed):
    return {"feed": feed, "watermark": None, "next_since": None,
            "seen_ids": [], "last_run": None}


def read(feed):
    """Lock-free read. Returns a blank record if nothing is stored yet."""
    path, _ = _paths(feed)
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except FileNotFoundError:
        return blank(feed)
    except json.JSONDecodeError:
        # A truncated file should never happen given atomic writes, but never
        # let a corrupt watermark stop a sync — fall back to "no state".
        print(f"WARNING: {path} is unreadable; treating {feed} as unsynced",
              file=sys.stderr)
        return blank(feed)
    merged = blank(feed)
    merged.update(data)
    return merged


def _write_atomic(feed, data):
    path, _ = _paths(feed)
    tmp = f"{path}.tmp.{os.getpid()}"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2, ensure_ascii=False)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)          # atomic on POSIX


def record(feed, watermark=None, until=None, ids=(), fetched=None, kept=None,
           written=None):
    """Merge a completed run into the feed's state under lock."""
    with _locked(feed):
        data = read(feed)
        if watermark and (not data["watermark"] or watermark > data["watermark"]):
            data["watermark"] = watermark
        # Re-scan the boundary day next time; seen_ids prevents duplicates.
        if until:
            data["next_since"] = until
        elif data["watermark"]:
            data["next_since"] = data["watermark"]

        seen = list(data.get("seen_ids") or [])
        known = set(seen)
        for i in ids:
            if i not in known:
                seen.append(i)
                known.add(i)
        data["seen_ids"] = seen[-MAX_SEEN:]

        data["last_run"] = {
            "at": datetime.datetime.now().astimezone().isoformat(timespec="seconds"),
            "until": until, "watermark": watermark,
            "fetched": fetched, "kept": kept, "written": written,
        }
        _write_atomic(feed, data)
        return data


def reset(feed):
    with _locked(feed):
        _write_atomic(feed, blank(feed))


def _load_ids(path):
    if not path:
        return []
    with open(path, encoding="utf-8") as fh:
        return [ln.strip() for ln in fh if ln.strip() and not ln.startswith("#")]


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("show"); p.add_argument("feed", nargs="?")
    p = sub.add_parser("since"); p.add_argument("feed")
    defaults = p.add_mutually_exclusive_group()
    defaults.add_argument("--default")
    defaults.add_argument("--default-one-month", action="store_true")
    p = sub.add_parser("filter-seen")
    p.add_argument("feed"); p.add_argument("--ids-file", required=True)
    p = sub.add_parser("record")
    p.add_argument("feed"); p.add_argument("--watermark"); p.add_argument("--until")
    p.add_argument("--ids-file"); p.add_argument("--fetched", type=int)
    p.add_argument("--kept", type=int); p.add_argument("--written", type=int)
    p = sub.add_parser("reset"); p.add_argument("feed")
    args = ap.parse_args()

    if args.cmd == "show":
        feeds = [args.feed] if args.feed else FEEDS
        out = {f: read(f) for f in feeds}
        for f, d in out.items():
            lr = d.get("last_run") or {}
            print(f"{f:10s} watermark={d.get('watermark') or '-':12s} "
                  f"next_since={d.get('next_since') or '-':12s} "
                  f"seen={len(d.get('seen_ids') or []):5d}  "
                  f"last_run={lr.get('at') or '-'}")
        return

    if args.cmd == "since":
        d = read(args.feed)
        fallback = one_calendar_month_ago() if args.default_one_month else args.default
        value = d.get("next_since") or fallback
        if not value:
            raise SystemExit(f"no stored state for {args.feed} and no --default given")
        print(value)
        return

    if args.cmd == "filter-seen":
        seen = set(read(args.feed).get("seen_ids") or [])
        ids = _load_ids(args.ids_file)
        fresh = [i for i in ids if i not in seen]
        skipped = len(ids) - len(fresh)
        if skipped:
            print(f"{skipped} already-synced id(s) skipped", file=sys.stderr)
        print("\n".join(fresh))
        return

    if args.cmd == "record":
        d = record(args.feed, watermark=args.watermark, until=args.until,
                   ids=_load_ids(args.ids_file), fetched=args.fetched,
                   kept=args.kept, written=args.written)
        print(f"{args.feed}: watermark={d['watermark']} next_since={d['next_since']} "
              f"seen={len(d['seen_ids'])}")
        return

    if args.cmd == "reset":
        reset(args.feed)
        print(f"{args.feed}: state cleared")


if __name__ == "__main__":
    main()
