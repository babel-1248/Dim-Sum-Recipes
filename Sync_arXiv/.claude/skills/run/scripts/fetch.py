#!/usr/bin/env python3
"""Stage 1: pull arXiv paper metadata for a date window into a manifest.

Deliberately does NOT download full text. The manifest carries title, abstract,
authors and categories — everything the relevance pass needs — so only the
survivors get their HTML fetched (by convert.py). That is the whole point
of the layered filter: a rejected paper costs zero extra requests.

The query is assembled from repeatable shorthands plus a raw --query escape
hatch, so a new kind of filter never requires editing this script:

  --category cs.CL --category cs.AI      ORed together
  --term "chain of thought" --term RLHF  ORed together, searched in --term-field
  --author Hinton                        ORed with other --author values
  --query 'abs:%22world model%22 ANDNOT cat:math.*'   raw, ANDed with the rest

Groups are ANDed: (cats) AND (terms) AND (authors) AND (raw) AND date-window.

Two traps this script exists to avoid:

  * The API must be reached over **https**. http://export.arxiv.org/api/query
    answers 301 with an empty body, which looks exactly like "no results".
  * arXiv asks for one request per 3 seconds. SLEEP is not tunable below that
    on purpose; hammering it earns a block for the whole machine.

The search index lags roughly a day behind real time — the newest submittedDate
available today is usually yesterday's. An empty tail day is normal, not a bug.

Usage:
  python3 fetch.py --since 2026-08-01 --out DIR --category cs.CL
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
import xml.etree.ElementTree as ET

API = "https://export.arxiv.org/api/query"
PER_PAGE = 200           # arXiv allows 2000 but large pages time out routinely
# API Terms of Use: "make no more than one request every three seconds, and
# limit requests to a single connection at a time" — and that ceiling applies
# across all machines under your control collectively, so spreading a job over
# several hosts is prohibited rather than clever. Paging below is sequential.
# The website (arxiv.org) is a different host under a stricter 15s Crawl-delay;
# see convert.py.
SLEEP = 3.0
MAX_PAGES = 200
MAX_START = 30000        # the API refuses to page beyond this
USER_AGENT = "research-feeds (personal note archive; contact via arxiv.org)"

NS = {
    "a": "http://www.w3.org/2005/Atom",
    "ar": "http://arxiv.org/schemas/atom",
    "o": "http://a9.com/-/spec/opensearch/1.1/",
}

# Verified field prefixes for --query. Documentation, not a whitelist.
KNOWN_SYNTAX = """\
arXiv search_query field prefixes:
  ti:      title                     abs:  abstract
  au:      author                    co:   comment
  jr:      journal reference         cat:  category (cs.CL, q-bio.*, …)
  rn:      report number             id:   arXiv id
  all:     any of the above

  submittedDate:[YYYYMMDDHHMM TO YYYYMMDDHHMM]    original v1 submission
  lastUpdatedDate:[YYYYMMDDHHMM TO YYYYMMDDHHMM]  most recent revision

Boolean operators are AND, OR, ANDNOT (uppercase). Group with parentheses,
quote phrases with double quotes: abs:"in-context learning" AND cat:cs.CL

Not expressible server-side, so these belong to the relevance pass:
  anything about the abstract's meaning, author affiliation, citation counts,
  venue quality, whether the paper is a survey, code availability.
"""


def get(url, tries=5):
    """Fetch with backoff. 503 is arXiv's "slow down", not a hard failure.

    The API answers 503 when it wants callers to back off, so that case gets a
    long, honest wait (and honours Retry-After when sent) rather than the brisk
    retry a network blip deserves. Exhausting the retries exits with a readable
    message instead of a traceback, because "arXiv is asking us to back off" is
    something to act on, not a bug to debug.
    """
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    for attempt in range(tries):
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                return resp.read()
        except urllib.error.HTTPError as exc:
            if exc.code not in (429, 500, 502, 503, 504) or attempt == tries - 1:
                if exc.code in (429, 503):
                    raise SystemExit(
                        f"arXiv returned {exc.code} after {attempt + 1} attempts. "
                        f"It is rate-limiting or briefly unavailable — wait a few "
                        f"minutes and re-run; the state file means nothing is lost.")
                raise
            wait = float(exc.headers.get("Retry-After") or 0) or 15 * (attempt + 1)
            print(f"    HTTP {exc.code}; retry in {wait:.0f}s", file=sys.stderr)
            time.sleep(wait)
        except (urllib.error.URLError, TimeoutError) as exc:
            if attempt == tries - 1:
                raise SystemExit(f"arXiv unreachable after {tries} attempts: {exc}")
            wait = SLEEP * (attempt + 2)
            print(f"    retry in {wait:.0f}s ({exc})", file=sys.stderr)
            time.sleep(wait)
    raise RuntimeError("unreachable")


def _ws(s):
    return re.sub(r"\s+", " ", (s or "")).strip()


def stamp(date, end=False):
    """YYYY-MM-DD -> arXiv's YYYYMMDDHHMM, inclusive at both ends."""
    d = date.replace("-", "")
    if not re.fullmatch(r"\d{8}", d):
        raise SystemExit(f"bad date {date!r}; expected YYYY-MM-DD")
    return d + ("2359" if end else "0000")


def quote_value(v):
    return f'"{v}"' if " " in v else v


def build_query(args):
    groups = []
    if args.category:
        groups.append("(" + " OR ".join(f"cat:{c}" for c in args.category) + ")")
    if args.term:
        field = args.term_field
        groups.append("(" + " OR ".join(
            f"{field}:{quote_value(t)}" for t in args.term) + ")")
    if args.author:
        groups.append("(" + " OR ".join(
            f"au:{quote_value(a)}" for a in args.author) + ")")
    if args.query:
        groups.append(f"({args.query})")
    if args.since:
        until = args.until or args.since
        groups.append(
            f"{args.date_field}:[{stamp(args.since)} TO {stamp(until, end=True)}]")
    return " AND ".join(groups)


def parse_entry(e):
    """Atom entry -> flat manifest record."""
    raw_id = (e.findtext("a:id", "", NS) or "").strip()
    arxiv_id = raw_id.rsplit("/abs/", 1)[-1]          # 2608.04289v1
    base_id = re.sub(r"v\d+$", "", arxiv_id)
    version = arxiv_id[len(base_id):] or "v1"

    authors = []
    for au in e.findall("a:author", NS):
        name = _ws(au.findtext("a:name", "", NS))
        aff = _ws(au.findtext("ar:affiliation", "", NS))
        authors.append({"name": name, "affiliation": aff} if aff else {"name": name})

    links = {}
    for ln in e.findall("a:link", NS):
        if ln.get("title") == "pdf":
            links["pdf"] = ln.get("href")
        elif ln.get("title") == "doi":
            links["doi_url"] = ln.get("href")
        elif ln.get("rel") == "alternate":
            links["abs"] = ln.get("href")

    published = (e.findtext("a:published", "", NS) or "").strip()
    return {
        "arxiv_id": arxiv_id,
        "base_id": base_id,
        "version": version,
        "title": _ws(e.findtext("a:title", "", NS)),
        "abstract": _ws(e.findtext("a:summary", "", NS)),
        "authors": authors,
        "published": published,
        "published_date": published[:10],
        "updated": (e.findtext("a:updated", "", NS) or "").strip(),
        "primary_category": (e.find("ar:primary_category", NS).get("term")
                             if e.find("ar:primary_category", NS) is not None else None),
        "categories": [c.get("term") for c in e.findall("a:category", NS)],
        "doi": _ws(e.findtext("ar:doi", "", NS)) or None,
        "journal_ref": _ws(e.findtext("ar:journal_ref", "", NS)) or None,
        "comment": _ws(e.findtext("ar:comment", "", NS)) or None,
        "abs_url": links.get("abs") or f"https://arxiv.org/abs/{arxiv_id}",
        "pdf_url": links.get("pdf") or f"https://arxiv.org/pdf/{arxiv_id}",
        "html_url": f"https://arxiv.org/html/{arxiv_id}",
        "doi_url": links.get("doi_url"),
    }


def fetch_page(query, start, sort_by, sort_order):
    params = [
        ("search_query", query),
        ("start", str(start)),
        ("max_results", str(PER_PAGE)),
        ("sortBy", sort_by),
        ("sortOrder", sort_order),
    ]
    body = get(API + "?" + urllib.parse.urlencode(params))
    root = ET.fromstring(body)

    entries = root.findall("a:entry", NS)
    # A malformed query comes back as a single entry pointing at the error doc
    # rather than as an HTTP error, so it must be detected explicitly.
    if len(entries) == 1:
        eid = entries[0].findtext("a:id", "", NS) or ""
        if "api/errors" in eid:
            raise SystemExit("arXiv rejected the query: "
                             + _ws(entries[0].findtext("a:summary", "", NS)))
    total = root.findtext("o:totalResults", None, NS)
    return entries, (int(total) if total and total.isdigit() else None)


def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--since", help="inclusive lower bound on the date field, YYYY-MM-DD")
    ap.add_argument("--until", help="inclusive upper bound, YYYY-MM-DD (default: --since)")
    ap.add_argument("--category", action="append", help="arXiv category (repeatable, ORed)")
    ap.add_argument("--term", action="append", help="search term (repeatable, ORed)")
    ap.add_argument("--term-field", default="all", choices=["all", "ti", "abs", "co", "jr"],
                    help="which field --term searches (default: all)")
    ap.add_argument("--author", action="append", help="author name (repeatable, ORed)")
    ap.add_argument("--query", help="raw search_query fragment, ANDed with the rest")
    ap.add_argument("--date-field", default="submittedDate",
                    choices=["submittedDate", "lastUpdatedDate"],
                    help="submittedDate = new papers; lastUpdatedDate also catches revisions")
    ap.add_argument("--sort-order", default="ascending", choices=["ascending", "descending"])
    ap.add_argument("--limit", type=int, help="stop after N papers (safety cap)")
    ap.add_argument("--list-syntax", action="store_true",
                    help="print the verified query vocabulary and exit")
    ap.add_argument("--out", help="output directory for manifest.json")
    args = ap.parse_args()

    if args.list_syntax:
        print(KNOWN_SYNTAX)
        return
    if not args.out:
        ap.error("--out is required")

    query = build_query(args)
    if not query:
        ap.error("give at least --since, --category, --term, --author or --query; "
                 "refusing to pull all of arXiv")

    os.makedirs(args.out, exist_ok=True)
    print(f"search_query: {query}", file=sys.stderr)

    sort_by = ("submittedDate" if args.date_field == "submittedDate"
               else "lastUpdatedDate")
    papers, seen, start, pages, total = [], set(), 0, 0, None
    while pages < MAX_PAGES and start < MAX_START:
        entries, reported = fetch_page(query, start, sort_by, args.sort_order)
        pages += 1
        if total is None:
            total = reported
            print(f"totalResults: {total}", file=sys.stderr)
        if not entries:
            break
        new = 0
        for e in entries:
            rec = parse_entry(e)
            if rec["arxiv_id"] in seen:
                continue
            seen.add(rec["arxiv_id"])
            papers.append(rec)
            new += 1
        print(f"  start={start}: +{new} (total {len(papers)})", file=sys.stderr)
        if args.limit and len(papers) >= args.limit:
            papers = papers[: args.limit]
            break
        if len(entries) < PER_PAGE:
            break
        start += PER_PAGE
        if total is not None and start >= total:
            break
        time.sleep(SLEEP)

    if start >= MAX_START:
        print(f"WARNING: hit the {MAX_START}-result paging ceiling; narrow the "
              f"query or shorten the window.", file=sys.stderr)

    papers.sort(key=lambda p: (p.get("published") or "", p.get("arxiv_id") or ""))
    manifest = {
        "feed": "arxiv",
        "since": args.since,
        "until": args.until or args.since,
        "query": query,
        "date_field": args.date_field,
        "fetched_count": len(papers),
        "api_reported_count": total,
        "documents": papers,
    }
    path = os.path.join(args.out, "manifest.json")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, indent=2, ensure_ascii=False)

    print(f"\n{len(papers)} papers -> {path}", file=sys.stderr)
    if papers:
        print(f"date range: {papers[0]['published_date']} .. "
              f"{papers[-1]['published_date']}", file=sys.stderr)
        counts = {}
        for p in papers:
            k = p.get("primary_category") or "?"
            counts[k] = counts.get(k, 0) + 1
        for k, v in sorted(counts.items(), key=lambda kv: -kv[1])[:15]:
            print(f"  {v:5d}  {k}", file=sys.stderr)


if __name__ == "__main__":
    main()
