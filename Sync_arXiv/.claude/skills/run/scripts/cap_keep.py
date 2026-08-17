#!/usr/bin/env python3
"""Cap an arXiv keep list while leaving overflow eligible for the next run."""

import argparse
import json
import sys


DEFAULT_LIMIT = 20


def load_ids(path):
    with open(path, encoding="utf-8") as fh:
        return {line.strip() for line in fh if line.strip() and not line.startswith("#")}


def manifest_entries(manifest):
    use_updated = manifest.get("date_field") == "lastUpdatedDate"
    return [
        (
            paper["arxiv_id"],
            (paper.get("updated") or "")[:10]
            if use_updated
            else paper.get("published_date"),
        )
        for paper in manifest.get("documents", [])
    ]


def cap_entries(entries, keep_ids, limit):
    matching = [(item_id, date) for item_id, date in entries if item_id in keep_ids]
    matching.sort(key=lambda entry: (entry[1] or "", entry[0]))
    if limit is None:
        return matching, []
    return matching[:limit], matching[limit:]


def write_ids(path, entries):
    with open(path, "w", encoding="utf-8") as fh:
        for item_id, _ in entries:
            fh.write(f"{item_id}\n")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--keep", required=True)
    parser.add_argument("--selected", required=True)
    parser.add_argument("--deferred", required=True)
    cap = parser.add_mutually_exclusive_group()
    cap.add_argument("--limit", type=int)
    cap.add_argument("--no-limit", action="store_true")
    args = parser.parse_args()

    if args.limit is not None and args.limit < 1:
        parser.error("--limit must be at least 1")
    limit = None if args.no_limit else (
        args.limit if args.limit is not None else DEFAULT_LIMIT
    )

    with open(args.manifest, encoding="utf-8") as fh:
        manifest = json.load(fh)
    entries = manifest_entries(manifest)
    keep_ids = load_ids(args.keep)

    known_ids = {item_id for item_id, _ in entries}
    missing = keep_ids - known_ids
    if missing:
        print(
            f"WARNING: {len(missing)} keep-list id(s) not in manifest: "
            f"{sorted(missing)[:5]}",
            file=sys.stderr,
        )

    selected, deferred = cap_entries(entries, keep_ids, limit)
    write_ids(args.selected, selected)
    write_ids(args.deferred, deferred)

    dated_deferred = [date for _, date in deferred if date]
    print(json.dumps({
        "eligible": len(selected) + len(deferred),
        "limit": limit,
        "selected": len(selected),
        "deferred": len(deferred),
        "earliest_deferred_date": min(dated_deferred) if dated_deferred else None,
        "undated_deferred": sum(1 for _, date in deferred if not date),
    }))


if __name__ == "__main__":
    main()
