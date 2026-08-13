#!/usr/bin/env python3
"""Stage 1: pull Federal Register document metadata since a date into a manifest.

Deliberately does NOT download full text. The manifest is what the relevance pass
reads to decide which documents are worth converting, so only the survivors get
their XML fetched (by convert.py). That is the whole point of the layered filter.

Any API condition can be passed through with --condition, so a new kind of filter
never requires editing this script:

  --condition presidential_document_type[]=executive_order
  --condition cfr[title]=40 --condition cfr[part]=52
  --condition near[location]=94105 --condition near[within]=50

--since/--until/--type/--agency/--term are shorthands that expand to exactly the
same mechanism. Run with --list-conditions to see the verified vocabulary.

Pagination follows next_page_url, which carries a search_after cursor. Do not
replace this with a page=N loop: the API silently wraps back to page 1 past
page 50 instead of erroring, which would re-ingest the newest documents forever.

Usage:
  python3 fetch.py --since 2026-08-01 --out DIR [--type RULE] [--condition K=V]
"""
import argparse
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

API = "https://www.federalregister.gov/api/v1/documents.json"
PER_PAGE = 200
MAX_PAGES = 500          # backstop; 500 * 200 = 100k documents
USER_AGENT = "federal-register-feed (personal note archive)"

# Every field the list endpoint accepts, verified by probing each key of the
# document detail endpoint against it. Requesting them all by default means a new
# filter never fails because some metadata field was not being asked for.
ALL_FIELDS = [
    "abstract", "action", "agencies", "body_html_url", "cfr_references", "citation",
    "comment_url", "comments_close_on", "correction_of", "corrections", "dates",
    "disposition_notes", "docket_ids", "dockets", "document_number", "effective_on",
    "end_page", "executive_order_notes", "executive_order_number", "full_text_xml_url",
    "html_url", "images", "images_metadata", "json_url", "mods_url",
    "not_received_for_publication", "page_length", "page_views", "pdf_url",
    "presidential_document_number", "proclamation_number", "public_inspection_pdf_url",
    "publication_date", "raw_text_url", "regulation_id_number_info",
    "regulation_id_numbers", "regulations_dot_gov_info", "regulations_dot_gov_url",
    "significant", "signing_date", "start_page", "subtype", "title", "toc_doc",
    "toc_subject", "topics", "type", "volume",
]

# Bulky nested blobs. Excluded by default because the manifest is read by the
# relevance pass, and these add megabytes without helping any judgement.
BULKY = {
    "images", "images_metadata", "page_views", "corrections", "dockets",
    "regulations_dot_gov_info", "regulation_id_number_info", "body_html_url",
    "mods_url", "json_url", "toc_doc", "toc_subject",
}

DEFAULT_FIELDS = [f for f in ALL_FIELDS if f not in BULKY]

# Condition keys verified against the live API. Anything here can be passed to
# --condition; the list is documentation, not a whitelist.
KNOWN_CONDITIONS = """\
  term=<words>                          full-text search
  type[]=RULE|PRORULE|NOTICE|PRESDOCU   document type
  agencies[]=<slug>                     agency slug (see /api/v1/agencies)
  agency_ids[]=<id>                     agency numeric id
  presidential_document_type[]=executive_order|proclamation|
      presidential_memorandum|notice|determination
  president[]=<slug>                    signing president
  publication_date[gte|lte|is|year]=<v> publication date
  effective_date[gte|lte|is|year]=<v>   effective date
  significant=1                         significant regulatory actions
  docket_id=<id>                        agency docket
  regulation_id_number=<rin>            RIN
  sections[]=<section>                  federalregister.gov section
  topics[]=<topic-slug>                 subject topic
  cfr[title]=<n> cfr[part]=<n>          CFR citation
  near[location]=<zip> near[within]=<mi> geographic proximity
  correction=1                          corrections only

NOT filterable server-side (must be handled as a layer-2 relevance pass):
  comments_close_on, subtype, action, abstract text"""

TYPE_ALIASES = {
    "rule": "RULE", "rules": "RULE", "final": "RULE", "final rule": "RULE",
    "prorule": "PRORULE", "proposed": "PRORULE", "proposed rule": "PRORULE",
    "notice": "NOTICE", "notices": "NOTICE",
    "presdocu": "PRESDOCU", "presidential": "PRESDOCU",
    "presidential document": "PRESDOCU",
}

# name, name[], name[sub] — the three shapes the API's condition keys take.
KEY_RE = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)((?:\[[A-Za-z0-9_]*\])*)$")


def condition_param(spec):
    """'cfr[title]=40' -> ('conditions[cfr][title]', '40')."""
    key, sep, value = spec.partition("=")
    if not sep:
        raise SystemExit(f"--condition needs KEY=VALUE, got: {spec!r}")
    m = KEY_RE.match(key.strip())
    if not m:
        raise SystemExit(f"--condition key not understood: {key!r}")
    base, subscripts = m.group(1), m.group(2)
    return f"conditions[{base}]{subscripts}", value


def get(url, retries=4):
    """GET with retry on transient errors and 429s."""
    for attempt in range(retries):
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            if exc.code == 429 or exc.code >= 500:
                wait = 2 ** attempt
                print(f"  {exc.code}, retrying in {wait}s", file=sys.stderr)
                time.sleep(wait)
                continue
            body = exc.read().decode("utf-8", "replace")[:400]
            raise SystemExit(
                f"HTTP {exc.code} from API: {body}\n"
                "If this names a condition, check it against --list-conditions."
            )
        except (urllib.error.URLError, TimeoutError) as exc:
            if attempt == retries - 1:
                raise SystemExit(f"network error: {exc}")
            time.sleep(2 ** attempt)
    raise SystemExit("exhausted retries")


def build_params(args):
    """Shorthand flags and --condition all funnel into one list of conditions."""
    conds = []
    if args.since:
        conds.append(f"publication_date[gte]={args.since}")
    if args.until:
        conds.append(f"publication_date[lte]={args.until}")
    for t in args.type or []:
        conds.append(f"type[]={TYPE_ALIASES.get(t.lower(), t.upper())}")
    for a in args.agency or []:
        conds.append(f"agencies[]={a}")
    if args.term:
        conds.append(f"term={args.term}")
    conds += list(args.condition or [])

    fields = args.field or (ALL_FIELDS if args.all_fields else DEFAULT_FIELDS)

    params = [("per_page", str(PER_PAGE)), ("order", "oldest")]
    params += [("fields[]", f) for f in fields]
    params += [condition_param(c) for c in conds]
    return params, conds


def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--since", help="inclusive publication_date lower bound, YYYY-MM-DD")
    ap.add_argument("--until", help="inclusive publication_date upper bound, YYYY-MM-DD")
    ap.add_argument("--type", action="append", help="RULE/PRORULE/NOTICE/PRESDOCU (repeatable)")
    ap.add_argument("--agency", action="append", help="agency slug (repeatable)")
    ap.add_argument("--term", help="full-text search term")
    ap.add_argument("--condition", action="append", metavar="KEY=VALUE",
                    help="any API condition, e.g. 'presidential_document_type[]="
                         "executive_order' (repeatable)")
    ap.add_argument("--field", action="append",
                    help="restrict manifest to these fields (repeatable)")
    ap.add_argument("--all-fields", action="store_true",
                    help="include bulky nested fields (images, page_views, …)")
    ap.add_argument("--limit", type=int, help="stop after N documents (safety cap)")
    ap.add_argument("--list-conditions", action="store_true",
                    help="print the verified condition vocabulary and exit")
    ap.add_argument("--out", help="output directory for manifest.json")
    args = ap.parse_args()

    if args.list_conditions:
        print(KNOWN_CONDITIONS)
        return
    if not args.out:
        ap.error("--out is required")
    if not (args.since or args.condition or args.type or args.agency or args.term):
        ap.error("give at least --since or a --condition; refusing to pull everything")

    os.makedirs(args.out, exist_ok=True)
    params, conds = build_params(args)
    print("conditions: " + (", ".join(conds) or "(none)"), file=sys.stderr)
    url = API + "?" + urllib.parse.urlencode(params)

    docs, seen, pages = [], set(), 0
    reported_count = None
    while url and pages < MAX_PAGES:
        data = get(url)
        pages += 1
        if reported_count is None:
            reported_count = data.get("count")
        results = data.get("results") or []
        if not results:
            break
        new = 0
        for d in results:
            num = d.get("document_number")
            if not num or num in seen:
                continue
            seen.add(num)
            docs.append(d)
            new += 1
        print(f"  page {pages}: +{new} (total {len(docs)})", file=sys.stderr)
        if new == 0:
            break  # cursor stopped advancing
        if args.limit and len(docs) >= args.limit:
            docs = docs[: args.limit]
            break
        url = data.get("next_page_url")

    if pages >= MAX_PAGES:
        print(f"WARNING: hit MAX_PAGES={MAX_PAGES}; results may be truncated.",
              file=sys.stderr)
    if reported_count is not None and reported_count >= 10000:
        print("NOTE: API count was capped at 10000; treat totals as a lower bound.",
              file=sys.stderr)

    docs.sort(key=lambda d: (d.get("publication_date") or "", d.get("document_number") or ""))
    manifest = {
        "since": args.since,
        "until": args.until,
        "conditions": conds,
        "fetched_count": len(docs),
        "api_reported_count": reported_count,
        "documents": docs,
    }
    path = os.path.join(args.out, "manifest.json")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, indent=2, ensure_ascii=False)

    print(f"\n{len(docs)} documents -> {path}", file=sys.stderr)
    if docs:
        print(f"date range: {docs[0]['publication_date']} .. {docs[-1]['publication_date']}",
              file=sys.stderr)
        counts = {}
        for d in docs:
            k = d.get("subtype") or d.get("type")
            counts[k] = counts.get(k, 0) + 1
        for k, v in sorted(counts.items(), key=lambda kv: -kv[1]):
            print(f"  {v:5d}  {k}", file=sys.stderr)


if __name__ == "__main__":
    main()
