#!/usr/bin/env python3
"""Convert GitHub Trending repository metadata into Markdown note files."""

import argparse
import hashlib
import json
import os
import re


WINDOW_LABELS = {
    "daily": "today",
    "weekly": "this week",
    "monthly": "this month",
}


def load_ids(path):
    if not path:
        return None
    with open(path, encoding="utf-8") as source:
        return {line.strip().casefold() for line in source if line.strip() and not line.startswith("#")}


def format_count(value):
    return f"{value:,}" if isinstance(value, int) else "Not reported"


def safe_cell(value):
    return str(value).replace("|", "\\|").replace("\n", " ")


def slug(value):
    readable = re.sub(r"[^a-zA-Z0-9._-]+", "-", value).strip("-").lower()[:80]
    digest = hashlib.sha256(value.casefold().encode()).hexdigest()[:10]
    return f"{readable or 'repository'}-{digest}"


def build_markdown(document, manifest):
    filters = manifest["filters"]
    date_range = filters["date_range"]
    language = (filters.get("language") or {}).get("label", "Any")
    spoken = (filters.get("spoken_language") or {}).get("label", "Any")
    full_name = document["full_name"]
    period_stars = format_count(document.get("period_stars"))
    period = WINDOW_LABELS[date_range]

    lines = [
        f"# {full_name}",
        "",
        document.get("description") or "*No repository description was provided on GitHub Trending.*",
        "",
        f"[View repository]({document['url']}) · [View this Trending feed]({manifest['source']})",
        "",
        "## Trending snapshot",
        "",
        "| Field | Value |",
        "| --- | --- |",
        f"| Rank | {document['rank']} |",
        f"| Date range | {safe_cell(date_range)} ({period}) |",
        f"| Feed programming language | {safe_cell(language)} |",
        f"| Feed spoken language | {safe_cell(spoken)} |",
        f"| Repository programming language | {safe_cell(document.get('language') or 'Not reported')} |",
        f"| Stars gained {period} | {period_stars} |",
        f"| Total stars | {format_count(document.get('total_stars'))} |",
        f"| Forks | {format_count(document.get('forks'))} |",
        f"| Snapshot time | {safe_cell(manifest['fetched_at'])} |",
    ]

    contributors = document.get("contributors") or []
    if contributors:
        lines.extend(["", "## Built by", ""])
        lines.extend(f"- [@{item['login']}]({item['url']})" for item in contributors)

    lines.extend([
        "",
        "---",
        "",
        "Source: GitHub Trending repositories. Counts are a point-in-time snapshot and may change.",
        "",
    ])
    return "\n".join(lines)


def convert(manifest, output_directory, keep_ids=None):
    os.makedirs(output_directory, exist_ok=True)
    documents = manifest.get("documents", [])
    known = {document["id"].casefold() for document in documents}
    if keep_ids is not None:
        missing = keep_ids - known
        if missing:
            raise ValueError(f"keep list contains IDs absent from the manifest: {sorted(missing)[:5]}")
        documents = [document for document in documents if document["id"].casefold() in keep_ids]

    index = []
    for document in documents:
        path = os.path.abspath(os.path.join(output_directory, f"{slug(document['full_name'])}.md"))
        with open(path, "w", encoding="utf-8") as output:
            output.write(build_markdown(document, manifest))
        index.append({
            "id": document["id"],
            "full_name": document["full_name"],
            "note_title": f"{document['full_name']} — GitHub Trending",
            "file": path,
            "source_url": document["url"],
        })

    index_path = os.path.abspath(os.path.join(output_directory, "index.json"))
    with open(index_path, "w", encoding="utf-8") as output:
        json.dump(index, output, indent=2, ensure_ascii=False)
    return index_path, index


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--keep")
    args = parser.parse_args()

    with open(args.manifest, encoding="utf-8") as source:
        manifest = json.load(source)
    keep_ids = load_ids(args.keep)
    try:
        index_path, index = convert(manifest, args.out, keep_ids)
    except (KeyError, OSError, ValueError) as exc:
        parser.error(str(exc))
    print(json.dumps({"converted": len(index), "index": index_path}))


if __name__ == "__main__":
    main()
