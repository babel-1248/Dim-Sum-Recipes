#!/usr/bin/env python3
"""Cap a government-feed keep list without losing deferred items.

Select the oldest matching manifest items up to --limit, or all matching items
with --no-limit, and write selected and deferred ID files. Print a JSON summary
containing the earliest deferred date so the caller can keep the next sync
boundary behind any unprocessed items. The default limit is 50.
"""

import argparse
import json
import sys


def load_ids(path):
    with open(path, encoding="utf-8") as fh:
        return {line.strip() for line in fh if line.strip() and not line.startswith("#")}


def manifest_entries(manifest):
    if "documents" in manifest:
        return [
            (item["document_number"], item.get("publication_date"))
            for item in manifest["documents"]
        ]
    if "items" in manifest:
        return [(item["id"], item.get("date")) for item in manifest["items"]]
    raise ValueError("manifest must contain 'documents' or 'items'")


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
    limit = None if args.no_limit else (args.limit if args.limit is not None else 50)

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
