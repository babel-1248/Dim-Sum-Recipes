#!/usr/bin/env python3
"""Stage 3: fetch full text for the kept documents and build Pachinko note bodies.

Reads manifest.json from fetch.py plus an optional keep-list (the survivors of the
relevance pass) and writes one markdown file per document, each opening with a
header carrying the metadata and the deadlines, followed by the converted body.

Emits index.json listing {document_number, title, section, file} in publication
order so the caller can create notes without re-deriving anything.

Usage:
  python3 convert.py --manifest DIR/manifest.json --out DIR/notes
                     [--keep DIR/keep.txt] [--jobs 6]
"""
import argparse
import concurrent.futures
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from frxml2md import xml_to_markdown  # noqa: E402

USER_AGENT = "federal-register-feed (personal note archive)"


def get_bytes(url, retries=4):
    for attempt in range(retries):
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        try:
            with urllib.request.urlopen(req, timeout=90) as resp:
                return resp.read()
        except urllib.error.HTTPError as exc:
            if exc.code == 429 or exc.code >= 500:
                time.sleep(2 ** attempt)
                continue
            raise
        except (urllib.error.URLError, TimeoutError):
            if attempt == retries - 1:
                raise
            time.sleep(2 ** attempt)
    raise RuntimeError("exhausted retries")


def slug(text, maxlen=60):
    s = re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")
    return s[:maxlen].rstrip("-") or "document"


def agency_names(doc):
    out = []
    for a in doc.get("agencies") or []:
        name = a.get("name") or a.get("raw_name")
        if name and name not in out:
            out.append(name)
    return out


def header(doc, body=""):
    """Metadata block that opens every note.

    The abstract and DATES paragraph are dropped when the converted body already
    carries them as its own SUMMARY / DATES sections, which is the case for most
    rules and notices but not for presidential documents.
    """
    lines = []
    # Presidential documents share type=PRESDOCU, so lead with the subtype and its
    # number ("Executive Order 14419") rather than the generic type.
    label = doc.get("subtype") or doc.get("type") or "Document"
    number = doc.get("executive_order_number") or doc.get("proclamation_number")
    if doc.get("subtype") and number:
        label = f"{label} {number}"
    bits = [label]
    if doc.get("action"):
        bits.append(doc["action"].rstrip("."))
    lines.append("**" + "** · **".join(b for b in bits if b) + "**")

    when = []
    if doc.get("signing_date"):
        when.append(f"Signed **{doc['signing_date']}**")
    when.append(f"Published **{doc.get('publication_date', '?')}**")
    if doc.get("effective_on"):
        when.append(f"Effective **{doc['effective_on']}**")
    if doc.get("comments_close_on"):
        when.append(f"Comments close **{doc['comments_close_on']}**")
    lines.append(" · ".join(when))

    ags = agency_names(doc)
    if ags:
        lines.append("Agencies: " + ", ".join(ags))

    refs = []
    for r in doc.get("cfr_references") or []:
        t, p = r.get("title"), r.get("part")
        if t and p:
            refs.append(f"{t} CFR {p}")
    meta = []
    if doc.get("citation"):
        meta.append(doc["citation"])
    if refs:
        meta.append("; ".join(dict.fromkeys(refs)))
    if doc.get("docket_ids"):
        meta.append(", ".join(doc["docket_ids"]))
    if meta:
        lines.append(" · ".join(meta))

    links = []
    if doc.get("html_url"):
        links.append(f"[Federal Register]({doc['html_url']})")
    if doc.get("pdf_url"):
        links.append(f"[PDF]({doc['pdf_url']})")
    url = doc.get("regulations_dot_gov_url") or doc.get("comment_url")
    if url:
        links.append(f"[Comment]({url})")
    if links:
        lines.append(" · ".join(links))

    block = "  \n".join(lines)

    if doc.get("abstract") and not re.search(r"^## SUMMARY\s*$", body, re.M):
        block += "\n\n> " + doc["abstract"].strip().replace("\n", "\n> ")

    # The free-text DATES paragraph carries conditions the structured fields miss
    # (comment windows, phase-ins, compliance dates).
    dates = (doc.get("dates") or "").strip()
    if dates and not re.search(r"^## DATES\s*$", body, re.M):
        block += f"\n\n**Dates:** {dates}"

    return block


def build(doc, outdir):
    num = doc.get("document_number")
    body, note = "", None
    xml_url = doc.get("full_text_xml_url")
    try:
        if xml_url:
            body, _ = xml_to_markdown(get_bytes(xml_url))
        if not body.strip() and doc.get("raw_text_url"):
            raw = get_bytes(doc["raw_text_url"]).decode("utf-8", "replace")
            body = raw.strip()
            note = "converted from raw text (no XML body)"
    except Exception as exc:  # noqa: BLE001 - one bad doc must not kill the run
        try:
            if doc.get("raw_text_url"):
                body = get_bytes(doc["raw_text_url"]).decode("utf-8", "replace").strip()
                note = f"XML failed ({exc}); used raw text"
        except Exception as exc2:  # noqa: BLE001
            body = ""
            note = f"full text unavailable: {exc2}"

    md = f"# {doc.get('title', num)}\n\n{header(doc, body)}\n\n---\n\n{body}\n"
    if note:
        md += f"\n\n*[{note}]*\n"

    fname = f"{doc.get('publication_date')}-{num}-{slug(doc.get('title'))}.md"
    path = os.path.join(outdir, fname)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(md)
    return {
        "document_number": num,
        "title": doc.get("title"),
        "type": doc.get("type"),
        "section": doc.get("publication_date"),
        "publication_date": doc.get("publication_date"),
        "effective_on": doc.get("effective_on"),
        "comments_close_on": doc.get("comments_close_on"),
        "agencies": agency_names(doc),
        "file": os.path.abspath(path),
        "bytes": len(md),
        "warning": note,
    }


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--manifest", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--keep", help="file of document_numbers to keep, one per line "
                                   "(omit to convert everything in the manifest)")
    ap.add_argument("--jobs", type=int, default=6)
    args = ap.parse_args()

    with open(args.manifest, encoding="utf-8") as fh:
        manifest = json.load(fh)
    docs = manifest["documents"]

    if args.keep:
        with open(args.keep, encoding="utf-8") as fh:
            keep = {ln.strip() for ln in fh if ln.strip() and not ln.startswith("#")}
        missing = keep - {d["document_number"] for d in docs}
        if missing:
            print(f"WARNING: {len(missing)} keep-list ids not in manifest: "
                  f"{sorted(missing)[:5]}", file=sys.stderr)
        docs = [d for d in docs if d["document_number"] in keep]

    if not docs:
        print("nothing to convert", file=sys.stderr)
        with open(os.path.join(args.out or ".", "index.json"), "w") as fh:
            json.dump([], fh)
        return

    os.makedirs(args.out, exist_ok=True)
    print(f"converting {len(docs)} documents with {args.jobs} workers…", file=sys.stderr)

    index = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.jobs) as pool:
        futures = {pool.submit(build, d, args.out): d for d in docs}
        for i, fut in enumerate(concurrent.futures.as_completed(futures), 1):
            entry = fut.result()
            index.append(entry)
            flag = f"  [{entry['warning']}]" if entry.get("warning") else ""
            print(f"  {i}/{len(docs)} {entry['document_number']} "
                  f"({entry['bytes']:,}b){flag}", file=sys.stderr)

    index.sort(key=lambda e: (e["publication_date"] or "", e["document_number"]))
    path = os.path.join(args.out, "index.json")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(index, fh, indent=2, ensure_ascii=False)

    warned = [e for e in index if e.get("warning")]
    print(f"\n{len(index)} notes -> {args.out}", file=sys.stderr)
    print(f"index: {path}", file=sys.stderr)
    if warned:
        print(f"{len(warned)} with warnings:", file=sys.stderr)
        for e in warned:
            print(f"  {e['document_number']}: {e['warning']}", file=sys.stderr)


if __name__ == "__main__":
    main()
