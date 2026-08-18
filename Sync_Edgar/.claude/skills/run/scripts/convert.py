#!/usr/bin/env python3
"""Stage 2: turn kept EDGAR manifest entries into one markdown note per filing.

Everything expensive lives here on purpose. A 10-K is 1-15 MB of HTML; a filing
rejected by the relevance pass must cost zero requests, so nothing is downloaded
until it has survived stage 1.

Three EDGAR-specific behaviours:

  * **Forms 3/4/5 bypass the HTML path entirely.** They are already structured
    XML, so form4.py builds a real transaction table with the transaction codes
    decoded. See that module for why the code column is the whole point.

  * **An 8-K's content is usually not in the 8-K.** The form itself is often a
    two-line shell saying "see Exhibit 99.1 attached hereto", and the press
    release with the actual numbers is a separate document in the same filing.
    Exhibits are therefore fetched too, chosen from the filing's SGML header.
    Without this an earnings 8-K converts to a note that says nothing.

  * **10-K/10-Q sections are addressable.** `--sections 1A,7` slices out Risk
    Factors and MD&A. The hard part is that the table of contents lists every
    item before the body does; see item_positions() for how the two are told
    apart.

Usage:
  python3 convert.py --manifest DIR/manifest.json --out DIR/notes \
      [--keep DIR/keep.txt] [--sections 1A,7] [--max 20]
"""
import argparse
import html
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from edgarhtml2md import html_to_markdown, text_from_txt      # noqa: E402
from form4 import ownership_to_markdown, short_action          # noqa: E402
from fetch import Client, ITEMS_8K                             # noqa: E402

MAX_NOTES = 20
# A full 10-K converts to roughly 200-400 KB of markdown. The ceiling exists so
# one 2,000-page S-1 cannot produce a note nothing can open; it is per document,
# not per note, so a filing's exhibits are each measured separately.
MAX_DOC_BYTES = 400_000
OWNERSHIP_FORMS = {"3", "4", "5"}
# Exhibit types fetched by default. EX-99 is where press releases and financial
# statements live; the EX-101/EX-104 family is XBRL plumbing with no prose in it.
DEFAULT_EXHIBITS = ("EX-99",)
SKIP_EXHIBITS = ("EX-101", "EX-104", "GRAPHIC", "XML", "EX-24", "EX-23")

# The body of a 10-K repeats "Item 7" a dozen times in cross-references, and the
# table of contents lists every item before the document starts. A heading is
# recognised by shape here and disambiguated by spacing in item_positions().
ITEM_RE = re.compile(r"(?im)^[*\s]*item\s+(\d{1,2}[A-C]?)\s*[.:—-]")
# Below this many characters before the next Item mention, a match is a table-of-
# contents line or a cross-reference, not the start of a section.
MIN_SECTION_GAP = 400


def slug(text, limit=60):
    s = re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")
    return s[:limit].rstrip("-") or "filing"


def is_ownership(form):
    return form.split("/")[0].strip().upper() in OWNERSHIP_FORMS


# ---- exhibit discovery ----------------------------------------------------

DOC_RE = re.compile(
    r"<DOCUMENT>\s*<TYPE>([^\n<]*)\s*(?:<SEQUENCE>([^\n<]*)\s*)?"
    r"<FILENAME>([^\n<]*)\s*(?:<DESCRIPTION>([^\n<]*))?", re.I)


def filing_documents(client, entry):
    """[(type, filename, description)] from the filing's SGML header.

    The header is the only place EDGAR states an exhibit's *type*. The directory
    listing (index.json) gives filenames and sizes but not whether a file is the
    press release or a stylesheet, and guessing from the name fails immediately —
    filers name the same exhibit `ex991.htm`, `a8-kex991q3.htm` or
    `tm2622009d1_ex99-1.htm`.
    """
    try:
        raw = client.get(entry["header_url"]).decode("utf-8", "replace")
    except Exception as exc:                              # noqa: BLE001
        print(f"    header unavailable ({exc}); exhibits skipped", file=sys.stderr)
        return []
    raw = html.unescape(raw)
    return [(t.strip(), f.strip(), (d or "").strip())
            for t, _, f, d in DOC_RE.findall(raw) if f.strip()]


def wanted_exhibits(docs, entry, patterns):
    out = []
    for etype, fname, desc in docs:
        up = etype.upper()
        if fname == entry.get("primary_document") or fname == entry.get("raw_document"):
            continue
        if any(up.startswith(s) for s in SKIP_EXHIBITS):
            continue
        if not fname.lower().endswith((".htm", ".html", ".txt")):
            continue
        if any(up.startswith(p.upper()) for p in patterns) or fname in (
                entry.get("matched_files") or []):
            out.append((etype, fname, desc))
    return out


# ---- 10-K / 10-Q section slicing -----------------------------------------

def item_positions(md):
    """[(offset, item_key)] for headings that actually start a section.

    A 10-K names every item twice: once in the table of contents and once where
    the section begins. The two are indistinguishable by text, but not by
    spacing — TOC lines sit tens of characters apart while real sections are
    thousands. Anything followed too closely by the next Item mention is dropped,
    which also removes the "see Part II, Item 7" cross-references sprinkled
    through the prose.
    """
    hits = [(m.start(), m.group(1).upper()) for m in ITEM_RE.finditer(md)]
    kept = []
    for i, (pos, key) in enumerate(hits):
        nxt = hits[i + 1][0] if i + 1 < len(hits) else len(md)
        if nxt - pos >= MIN_SECTION_GAP:
            kept.append((pos, key))
    return kept


def extract_sections(md, wanted):
    """(markdown, missing_keys) keeping only the requested Item sections."""
    wanted = [w.strip().upper().removeprefix("ITEM").strip() for w in wanted if w.strip()]
    kept = item_positions(md)
    if not kept:
        return md, wanted                      # no headings found: keep it whole
    chunks, found = [], set()
    for i, (pos, key) in enumerate(kept):
        if key not in wanted:
            continue
        end = kept[i + 1][0] if i + 1 < len(kept) else len(md)
        chunks.append(md[pos:end].strip())
        found.add(key)
    if not chunks:
        return md, wanted
    return "\n\n---\n\n".join(chunks), [w for w in wanted if w not in found]


# ---- one document ---------------------------------------------------------

def convert_document(client, url, filename):
    """(markdown, warning) for a single document inside a filing."""
    try:
        raw = client.get(url)
    except Exception as exc:                              # noqa: BLE001
        return "", f"could not fetch {filename}: {exc}"
    try:
        if filename.lower().endswith(".txt"):
            md, warns = text_from_txt(raw)
        else:
            md, warns = html_to_markdown(raw)
    except Exception as exc:                              # noqa: BLE001
        return "", f"conversion of {filename} failed: {exc}"
    return md, ("; ".join(warns) if warns else None)


def truncate(md, limit):
    if len(md) <= limit:
        return md, None
    cut = md.rfind("\n\n", 0, limit)
    return (md[: cut if cut > limit // 2 else limit]
            + f"\n\n*[truncated at {limit:,} characters]*\n",
            f"truncated from {len(md):,} characters")


# ---- note assembly --------------------------------------------------------

def header(e):
    lines = [f"**{e['form']}** · **{e['company']}**"
             + (f" ({', '.join(e['tickers'])})" if e.get("tickers") else "")]
    when = [f"Filed **{e['filed']}**"]
    if e.get("report_date"):
        when.append(f"Period **{e['report_date']}**")
    if e.get("accepted"):
        when.append(f"Accepted {e['accepted'][:16].replace('T', ' ')}")
    lines.append(" · ".join(when))
    lines.append(f"CIK {e['cik']} · Accession {e['accession']}")

    links = [f"[Filing index]({e['index_url']})"]
    if e.get("url"):
        links.append(f"[Primary document]({e['url']})")
    links.append(f"[All filings](https://www.sec.gov/cgi-bin/browse-edgar"
                 f"?action=getcompany&CIK={e['cik']}&type=&dateb=&owner=include&count=40)")
    lines.append(" · ".join(links))

    block = "  \n".join(lines)

    if e.get("items"):
        marks = ["- " + (f"**{i} — {ITEMS_8K.get(i, 'unrecognised item')}**"
                         if i in (e.get("material_items") or [])
                         else f"{i} — {ITEMS_8K.get(i, 'unrecognised item')}")
                 for i in e["items"]]
        block += "\n\n**Items reported:**\n" + "\n".join(marks)
    if e.get("matched_files"):
        block += ("\n\nFull-text search matched: "
                  + ", ".join(f"`{f}`" for f in e["matched_files"]))
    return block


def build(e, outdir, client, args):
    warnings, parts = [], []
    body_bytes = 0

    if is_ownership(e["form"]) and (e.get("raw_document") or "").endswith(".xml"):
        try:
            raw = client.get(e["url"])
            md, warns, facts = ownership_to_markdown(raw)
        except Exception as exc:                          # noqa: BLE001
            md, warns, facts = "", [f"could not fetch ownership XML: {exc}"], {}
        warnings += warns
        if md:
            parts.append(md)
        e = dict(e, ownership=facts)
    else:
        facts = {}
        primary = e.get("raw_document")
        legacy = bool(primary and primary.endswith(".txt")
                      and primary.startswith(e["accession"]))
        # The SGML header is the only list of a filing's documents *by type*, so
        # it is fetched once and reused for both jobs below. Legacy .txt
        # submissions have no header (404) and need none: they are a single file
        # with the exhibits already inside it.
        docs = ([] if legacy or not (args.exhibits or e.get("source") == "full-text-search")
                else filing_documents(client, e))

        # A full-text-search hit names the document that matched, which is
        # usually an exhibit rather than the form. Converting only that leaves a
        # note with no 8-K in it, so the header's first entry — always the form
        # itself — becomes the primary and the matched files become exhibits.
        if e.get("source") == "full-text-search" and docs:
            primary = docs[0][1]
            # The description carried by an FTS hit describes the *matched*
            # document, so leaving it in place labels the 8-K body "EX-99.1".
            e = dict(e, primary_description=docs[0][2] or docs[0][0])

        if primary:
            md, warn = convert_document(client, f"{e['base_url']}/{primary}", primary)
            if warn:
                warnings.append(warn)
            if md and args.sections and e["form"].split("/")[0] in ("10-K", "10-Q", "20-F"):
                md, missing = extract_sections(md, args.sections.split(","))
                if missing:
                    warnings.append("sections not found: " + ", ".join(missing))
            if md:
                md, warn = truncate(md, args.max_doc_bytes)
                if warn:
                    warnings.append(f"{primary}: {warn}")
                parts.append(f"## {e.get('primary_description') or e['form']}\n\n{md}")
                body_bytes += len(md)

        if docs:
            e = dict(e, primary_document=primary, raw_document=primary)
            for etype, fname, desc in wanted_exhibits(docs, e, args.exhibits):
                md, warn = convert_document(
                    client, f"{e['base_url']}/{fname}", fname)
                if warn:
                    warnings.append(warn)
                    continue
                md, warn = truncate(md, args.max_doc_bytes)
                if warn:
                    warnings.append(f"{fname}: {warn}")
                label = f"{etype}" + (f" — {desc}" if desc and desc != etype else "")
                parts.append(f"## Exhibit {label}\n\n"
                             f"*[{e['base_url']}/{fname}]({e['base_url']}/{fname})*\n\n{md}")
                body_bytes += len(md)

    title = note_title(e, facts)
    doc = f"# {title}\n\n{header(e)}\n"
    if parts:
        doc += "\n---\n\n" + "\n\n---\n\n".join(parts) + "\n"
    else:
        warnings.append("no document text was retrieved")
    if warnings:
        doc += "\n\n*[" + "; ".join(warnings) + "]*\n"

    filed = slug(e.get("filed") or "undated", 10)
    accession_suffix = slug(e["accession"].replace("-", ""), 12)[-12:]
    fname = f"{filed}-{slug(e['form'])}-{slug(e['company'], 30)}-" \
            f"{accession_suffix}.md"
    path = os.path.join(outdir, fname)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(doc)

    return {
        "id": e["accession"],
        "accession": e["accession"],
        "note_title": title,
        "company": e["company"],
        "tickers": e.get("tickers"),
        "cik": e["cik"],
        "form": e["form"],
        "filed": e["filed"],
        "report_date": e.get("report_date"),
        "items": e.get("items"),
        "material_items": e.get("material_items"),
        "section": section_title(e),
        "url": e["index_url"],
        "file": os.path.abspath(path),
        "bytes": len(doc),
        "has_body": bool(parts),
        "ownership": facts or None,
        "warning": "; ".join(warnings) or None,
    }


def note_title(e, facts):
    """A title that says what happened, not just which form arrived."""
    who = ", ".join(e.get("tickers") or []) or e["company"]
    if facts and facts.get("owners"):
        names = ", ".join(o["name"] for o in facts["owners"])
        return (f"{who} — Form {facts.get('document_type') or e['form']}: "
                f"{names} {short_action(facts)} {e['filed']}")
    if e.get("items"):
        labels = [ITEMS_8K.get(i, i) for i in (e.get("material_items") or e["items"])[:2]]
        return f"{who} — {e['form']}: {'; '.join(labels)} {e['filed']}"
    return f"{who} — {e['form']} {e['filed']}"


def section_title(e):
    """Pachinko section: one per company, which is how a watchlist is read."""
    tick = (e.get("tickers") or [None])[0]
    return f"{e['company']} ({tick})" if tick else e["company"]


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--manifest", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--keep", help="file of accession numbers to keep, one per line")
    ap.add_argument("--sections", help="10-K/10-Q Item sections to keep, e.g. 1A,7 "
                                       "(default: the whole document)")
    ap.add_argument("--exhibits", default=",".join(DEFAULT_EXHIBITS),
                    help=f"exhibit type prefixes to fetch (default: "
                         f"{','.join(DEFAULT_EXHIBITS)}); empty string disables")
    ap.add_argument("--max-doc-bytes", type=int, default=MAX_DOC_BYTES,
                    help=f"truncate any single document past this (default {MAX_DOC_BYTES})")
    ap.add_argument("--max", type=int, default=MAX_NOTES, dest="max_notes",
                    help=f"most filings to convert in one run (default {MAX_NOTES}); "
                         f"the oldest are taken and the rest deferred")
    ap.add_argument("--user-agent")
    args = ap.parse_args()
    args.exhibits = [p for p in (args.exhibits or "").split(",") if p.strip()]

    with open(args.manifest, encoding="utf-8") as fh:
        records = json.load(fh)["documents"]

    if args.keep:
        with open(args.keep, encoding="utf-8") as fh:
            keep = {ln.strip() for ln in fh if ln.strip() and not ln.startswith("#")}
        missing = keep - {r["accession"] for r in records}
        if missing:
            print(f"WARNING: {len(missing)} keep-list accession(s) not in manifest: "
                  f"{sorted(missing)[:5]}", file=sys.stderr)
        records = [r for r in records if r["accession"] in keep]

    # The manifest is in filing-date order, so the cap keeps the oldest and
    # defers the newest. A feed must not skip a day to show today's filings.
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

    client = Client(args.user_agent)
    print(f"converting {len(records)} filing(s)…", file=sys.stderr)

    index = []
    for i, r in enumerate(records, 1):
        entry = build(r, args.out, client, args)
        index.append(entry)
        flag = f"  [{entry['warning']}]" if entry.get("warning") else ""
        print(f"  {i}/{len(records)} {entry['form']:8s} {entry['company'][:28]:28s} "
              f"({entry['bytes']:,}b){flag}", file=sys.stderr)

    index.sort(key=lambda e: (e["filed"] or "", e["accession"]))
    path = os.path.join(args.out, "index.json")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(index, fh, indent=2, ensure_ascii=False)

    empty = sum(1 for e in index if not e["has_body"])
    print(f"\n{len(index)} note(s) -> {args.out}" +
          (f"  ({empty} with no document text)" if empty else ""), file=sys.stderr)
    print(f"index: {path}", file=sys.stderr)

    if deferred:
        # The window end must be the last filing actually written, not the one
        # the caller asked for, or the deferred filings are neither written nor
        # in seen_ids and the next window starts after them.
        print(f"\nCAPPED at {args.max_notes}: {len(deferred)} filing(s) deferred "
              f"({deferred[0]['filed']} .. {deferred[-1]['filed']}).", file=sys.stderr)
        print(f"RECORD --until {index[-1]['filed']} — NOT the requested window end, "
              f"or the deferred filings are stranded.", file=sys.stderr)


if __name__ == "__main__":
    main()
