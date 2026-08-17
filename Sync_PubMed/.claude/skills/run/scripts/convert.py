#!/usr/bin/env python3
"""Stage 2: turn kept PubMed manifest entries into one markdown note per paper.

Everything expensive lives here on purpose. Full text is fetched only for
papers that survived the relevance pass, so a rejected paper costs nothing
beyond the metadata already in the manifest.

Full text comes from PubMed Central via efetch db=pmc, and is available only
for the subset of papers that are open access. A PMC id is necessary but not
sufficient: a paywalled deposit answers successfully with a record that has
metadata and no <body>. That case is detected by content and degrades to an
abstract-only note with the reason in the entry's `warning`.

Requests are issued sequentially through one rate-limited client. This is not
an oversight — NCBI blocks IPs that exceed 3 requests/second (10 with an API
key), so parallel downloads would trade a slower run for a blocked machine.

Usage:
  python3 convert.py --manifest DIR/manifest.json --out DIR/notes \
      [--keep DIR/keep.txt] [--abstract-only]
"""
import argparse
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from jats2md import jats_to_markdown          # noqa: E402
from fetch import EFETCH, Client            # noqa: E402

MAX_AUTHORS = 12

# Ceiling on notes per run. The oldest survivors are taken and the remainder
# deferred, which is only safe because the caller then records the *last
# converted record's date* as the window end rather than the date it asked for —
# see the resume line printed at the end of a capped run. Recording the
# requested end after a capped run would strand every deferred record.
MAX_NOTES = 20


def slug(text, limit=60):
    s = re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")
    return s[:limit].rstrip("-") or "untitled"


def author_line(authors):
    names = [a.get("name", "") for a in authors or [] if a.get("name")]
    if not names:
        return None
    if len(names) > MAX_AUTHORS:
        return ", ".join(names[:MAX_AUTHORS]) + f", and {len(names) - MAX_AUTHORS} others"
    return ", ".join(names)


def citation(r):
    """Journal, volume(issue):pages — the parts that are present."""
    bits = [r.get("journal_abbrev") or r.get("journal") or ""]
    vol = r.get("volume") or ""
    if vol:
        vol += f"({r['issue']})" if r.get("issue") else ""
        bits.append(vol)
    if r.get("pages"):
        bits.append(f":{r['pages']}" if vol else r["pages"])
    return " ".join(b for b in bits if b).replace(" :", ":").strip()


def header(r):
    lines = []

    ident = [f"PMID {r['pmid']}"]
    types = [t for t in (r.get("pub_types") or []) if t != "Journal Article"]
    if types:
        ident.append(", ".join(types))
    lines.append("**" + "** · **".join(ident) + "**")

    when = []
    if r.get("pub_date"):
        when.append(f"Published **{r['pub_date']}**")
    if r.get("entrez_date"):
        when.append(f"Indexed **{r['entrez_date']}**")
    if when:
        lines.append(" · ".join(when))

    au = author_line(r.get("authors"))
    if au:
        lines.append(f"Authors: {au}")

    cite = citation(r)
    meta = [cite] if cite else []
    if r.get("doi"):
        meta.append(f"doi:{r['doi']}")
    if meta:
        lines.append(" · ".join(meta))

    links = [f"[PubMed]({r['url']})"]
    if r.get("doi_url"):
        links.append(f"[DOI]({r['doi_url']})")
    if r.get("pmc_url"):
        links.append(f"[PMC]({r['pmc_url']})")
    lines.append(" · ".join(links))

    block = "  \n".join(lines)

    if r.get("abstract"):
        block += "\n\n> " + r["abstract"].strip().replace("\n", "\n> ")

    # Major MeSH headings carry a * — that distinction is the whole point of
    # PubMed's indexing and it costs one character to keep.
    mesh = [("*" + m["term"] + "*") if m.get("major") else m["term"]
            for m in (r.get("mesh") or [])]
    if mesh:
        block += "\n\nMeSH: " + ", ".join(mesh)
    if r.get("keywords"):
        block += "\n\nKeywords: " + ", ".join(r["keywords"])
    return block


def full_text(client, r):
    """(markdown, warning) for one record."""
    if not r.get("pmc"):
        return "", "no PMC id (not open access)"
    pmcid = r["pmc"].replace("PMC", "")
    try:
        body = client.get(EFETCH, [("db", "pmc"), ("id", pmcid), ("retmode", "xml")])
    except Exception as exc:  # noqa: BLE001 - one bad paper must not kill the run
        return "", f"full text unavailable: {exc}"
    try:
        md, warns = jats_to_markdown(body)
    except Exception as exc:  # noqa: BLE001
        return "", f"JATS conversion failed: {exc}"
    return md, ("; ".join(warns) + " — abstract only") if warns else None


def build(r, outdir, client, abstract_only=False):
    if abstract_only:
        body, warning = "", "abstract only (--abstract-only)"
    else:
        body, warning = full_text(client, r)

    md = f"# {r['title']}\n\n{header(r)}\n"
    if body.strip():
        md += f"\n---\n\n{body}\n"
    if warning:
        md += f"\n\n*[{warning}]*\n"

    date = r.get("entrez_date") or r.get("pub_date") or "undated"
    fname = f"{date}-{r['pmid']}-{slug(r['title'])}.md"
    path = os.path.join(outdir, fname)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(md)

    return {
        "id": r["pmid"],
        "pmid": r["pmid"],
        "note_title": r["title"],
        "title": r["title"],
        "entrez_date": r.get("entrez_date"),
        "pub_date": r.get("pub_date"),
        "modified_date": r.get("modified_date"),
        "window_date": r.get("window_date"),
        "journal": r.get("journal"),
        "pub_types": r.get("pub_types"),
        "doi": r.get("doi"),
        "pmc": r.get("pmc"),
        "url": r["url"],
        "file": os.path.abspath(path),
        "bytes": len(md),
        "has_full_text": bool(body.strip()),
        "warning": warning,
    }


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--manifest", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--keep", help="file of PMIDs to keep, one per line "
                                   "(omit to convert everything in the manifest)")
    ap.add_argument("--abstract-only", action="store_true",
                    help="skip full-text download entirely")
    ap.add_argument("--api-key", default=os.environ.get("NCBI_API_KEY"))
    ap.add_argument("--email", default=os.environ.get("NCBI_EMAIL"))
    ap.add_argument("--max", type=int, default=MAX_NOTES, dest="max_notes",
                    help=f"most records to convert in one run (default {MAX_NOTES}); "
                         f"the oldest are taken and the rest deferred to the next run")
    args = ap.parse_args()

    with open(args.manifest, encoding="utf-8") as fh:
        records = json.load(fh)["documents"]

    if args.keep:
        with open(args.keep, encoding="utf-8") as fh:
            keep = {ln.strip() for ln in fh if ln.strip() and not ln.startswith("#")}
        missing = keep - {r["pmid"] for r in records}
        if missing:
            print(f"WARNING: {len(missing)} keep-list ids not in manifest: "
                  f"{sorted(missing)[:5]}", file=sys.stderr)
        records = [r for r in records if r["pmid"] in keep]

    # Manifests arrive in window-date order, so the cap keeps the oldest and
    # defers the newest — a feed must not skip a day to show today's records.
    deferred = []
    if args.max_notes and len(records) > args.max_notes:
        deferred = records[args.max_notes:]
        records = records[: args.max_notes]

    os.makedirs(args.out, exist_ok=True)
    if not records:
        print("nothing to convert", file=sys.stderr)
        with open(os.path.join(args.out, "index.json"), "w") as fh:
            json.dump([], fh)
        return

    client = Client(args.api_key, args.email)
    oa = sum(1 for r in records if r.get("pmc"))
    print(f"converting {len(records)} records ({oa} with a PMC id)…", file=sys.stderr)

    index = []
    for i, r in enumerate(records, 1):
        entry = build(r, args.out, client, args.abstract_only)
        index.append(entry)
        flag = f"  [{entry['warning']}]" if entry.get("warning") else ""
        print(f"  {i}/{len(records)} {entry['pmid']} ({entry['bytes']:,}b){flag}",
              file=sys.stderr)

    index.sort(key=lambda e: (e.get("window_date") or "", e["pmid"]))
    path = os.path.join(args.out, "index.json")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(index, fh, indent=2, ensure_ascii=False)

    full = sum(1 for e in index if e["has_full_text"])
    print(f"\n{len(index)} notes -> {args.out}  ({full} with full text)", file=sys.stderr)
    print(f"index: {path}", file=sys.stderr)

    if deferred:
        # The window end must be the last record actually written, not the one
        # the caller asked for. seen_ids then suppresses the re-read duplicates
        # and the deferred records arrive on the next run instead of vanishing.
        resume = index[-1]["window_date"]
        print(f"\nCAPPED at {args.max_notes}: {len(deferred)} record(s) deferred "
              f"({deferred[0]['window_date']} .. {deferred[-1]['window_date']}).",
              file=sys.stderr)
        print(f"RECORD --until {resume} — NOT the requested window end, or the "
              f"deferred records are stranded.", file=sys.stderr)


if __name__ == "__main__":
    main()
