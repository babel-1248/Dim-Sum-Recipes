#!/usr/bin/env python3
"""Stage 1: list SEC EDGAR filings for a date window into a manifest.

Metadata only. Every filing on EDGAR is listed with its form, dates, 8-K item
codes and the URL of its primary document, but nothing is downloaded — a 10-K is
1-15 MB of HTML and the relevance pass must be able to reject one for free.
convert.py fetches the documents for survivors.

Two modes, because "follow these companies" and "find filings that mention X"
are different queries against different EDGAR services:

  * --ticker/--cik without --query -> data.sec.gov/submissions/CIK##########.json,
    the complete filing history of one company. This is normal watchlist mode.
  * --query and/or a bare --form window -> efts.sec.gov full-text search, which
    indexes filing contents since 2001. A query combined with tickers/CIKs is
    restricted to those resolved companies.

Three EDGAR facts shape this script:

  * **A User-Agent is mandatory.** Requests without one, or with a generic
    library agent such as `curl/8.x`, are answered 403 by every sec.gov host.
    SEC asks for a declared contact; set SEC_CONTACT_EMAIL.
  * **10 requests/second in total.** SEC applies the guideline regardless of
    how many machines submit requests.
  * **Full-text search cannot page past 10,000 hits** — `from` beyond that
    returns a 500. Narrow the window rather than trying to walk further.

Usage:
  python3 fetch.py --since 2026-08-01 --out DIR --ticker AAPL --form 8-K --form 4
  python3 fetch.py --since 2026-08-01 --out DIR --query '"material weakness"'
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

SUBMISSIONS = "https://data.sec.gov/submissions/CIK{cik}.json"
TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
FTS = "https://efts.sec.gov/LATEST/search-index"
ARCHIVES = "https://www.sec.gov/Archives/edgar/data/{cik}/{acc}"

FTS_PAGE = 100           # the service returns 100 per page regardless of `size`
FTS_MAX_FROM = 9900      # `from` beyond ~10k is a 500 from Elasticsearch
TICKER_TTL = 7 * 86400   # the ticker map changes slowly; re-fetch weekly
HARD_CAP = 20000

# Form 8-K item codes. This map is most of the reason an 8-K feed is worth
# having: the form itself is often a two-line shell pointing at an exhibit, and
# the item code is the only place the filing says what happened.
ITEMS_8K = {
    "1.01": "Entry into a material definitive agreement",
    "1.02": "Termination of a material definitive agreement",
    "1.03": "Bankruptcy or receivership",
    "1.04": "Mine safety — shutdowns and patterns of violations",
    "1.05": "Material cybersecurity incident",
    "2.01": "Completion of an acquisition or disposition of assets",
    "2.02": "Results of operations and financial condition (earnings)",
    "2.03": "Creation of a direct financial obligation",
    "2.04": "Triggering event accelerating a financial obligation",
    "2.05": "Costs associated with exit or disposal activities",
    "2.06": "Material impairment",
    "3.01": "Notice of delisting or failure to satisfy a listing rule",
    "3.02": "Unregistered sale of equity securities",
    "3.03": "Material modification to rights of security holders",
    "4.01": "Change in the registrant's certifying accountant",
    "4.02": "Non-reliance on previously issued financial statements",
    "5.01": "Change in control of the registrant",
    "5.02": "Departure or election of directors or principal officers",
    "5.03": "Amendment to articles or bylaws; change in fiscal year",
    "5.04": "Temporary suspension of trading under employee benefit plans",
    "5.05": "Amendment to, or waiver of, the code of ethics",
    "5.06": "Change in shell company status",
    "5.07": "Submission of matters to a vote of security holders",
    "5.08": "Shareholder director nominations",
    "6.01": "ABS informational and computational material",
    "6.02": "Change of servicer or trustee",
    "6.03": "Change in credit enhancement or other external support",
    "6.04": "Failure to make a required distribution",
    "6.05": "Securities Act updating disclosure",
    "6.06": "Static pool",
    "6.07": "Change in sponsor interest",
    "7.01": "Regulation FD disclosure",
    "8.01": "Other events",
    "9.01": "Financial statements and exhibits",
}

# Items that are, by their nature, the bad news a reader is scanning for. Used
# only to flag entries in the manifest — nothing is filtered on this.
MATERIAL_8K = {"1.03", "1.05", "2.01", "2.06", "3.01", "4.01", "4.02", "5.02"}

KNOWN_FORMS = """\
Form types worth naming in --form (EDGAR has hundreds; these are the ones a
company-follower reads):

  Periodic     10-K annual        10-Q quarterly     20-F / 40-F foreign annual
               11-K employee plan
  Event        8-K current report (see the item codes below)   6-K foreign
  Ownership    3 initial          4 changes          5 annual
               SC 13D activist stake   SC 13G passive stake    13F-HR institutional
  Proxy        DEF 14A definitive   PRE 14A preliminary   DEFA14A additional
  Offering     S-1 / S-3 / S-4 registration   424B* prospectus   S-8 employee plan
  Other        144 proposed sale    SD conflict minerals   ARS annual report
               25 delisting         15-12B deregistration  NT 10-K late filing

--form matches amendments too (8-K also matches 8-K/A) unless --no-amendments.
--exclude-form drops a type after matching, which is how you keep 8-Ks but lose
the 144s that flood a large-cap watchlist.

Date windows apply to the filing date, not the period covered — a 10-K for FY2025
is filed in the autumn of 2026 and belongs to that day's window.

Full-text search (--query) indexes document *contents* from 2001 onward, across
all companies. It takes quoted phrases, AND/OR/NOT and parentheses. It cannot
page beyond 10,000 hits, so narrow the window rather than the other way round.
"""


class Client:
    """sec.gov client honouring the 10-requests/second ceiling.

    The interval is deliberately a touch over 1/10s. SEC's guideline applies to
    a user's total traffic regardless of how many machines submit it, and the
    site may limit excessive clients, so being slightly slow is preferable.
    """

    def __init__(self, user_agent=None):
        email = os.environ.get("SEC_CONTACT_EMAIL")
        configured_agent = user_agent or os.environ.get("SEC_USER_AGENT")
        if not email and not configured_agent:
            raise SystemExit(
                "SEC_CONTACT_EMAIL is required so EDGAR requests identify a "
                "responsible contact"
            )
        self.user_agent = configured_agent or f"edgar-feed/1.0 ({email})"
        self.interval = 0.11
        self._last = 0.0

    def get(self, url, params=None, tries=4):
        parsed = urllib.parse.urlparse(url)
        host = (parsed.hostname or "").lower()
        if parsed.scheme != "https" or not (host == "sec.gov" or host.endswith(".sec.gov")):
            raise ValueError(f"refusing non-SEC URL: {url}")
        wait = self._last + self.interval - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        full = url + ("?" + urllib.parse.urlencode(params) if params else "")
        for attempt in range(tries):
            try:
                req = urllib.request.Request(full, headers={
                    "User-Agent": self.user_agent,
                    "Accept-Encoding": "gzip",
                    "Host": parsed.netloc,
                })
                with urllib.request.urlopen(req, timeout=120) as resp:
                    self._last = time.monotonic()
                    body = resp.read()
                    if resp.headers.get("Content-Encoding") == "gzip":
                        import gzip
                        body = gzip.decompress(body)
                    return body
            except urllib.error.HTTPError as exc:
                if exc.code == 403:
                    raise SystemExit(
                        f"sec.gov returned 403 for {full}\nEDGAR rejects requests "
                        f"with a missing or generic User-Agent. Current agent: "
                        f"{self.user_agent!r}. Set SEC_CONTACT_EMAIL or pass "
                        f"--user-agent 'Name email@example.com'.")
                if exc.code == 404:
                    raise
                if exc.code not in (429, 500, 502, 503, 504) or attempt == tries - 1:
                    raise
                back = float(exc.headers.get("Retry-After") or 0) or 5 * (attempt + 1)
                print(f"    HTTP {exc.code}; retry in {back:.0f}s", file=sys.stderr)
                time.sleep(back)
            except (urllib.error.URLError, TimeoutError) as exc:
                if attempt == tries - 1:
                    raise SystemExit(f"sec.gov unreachable after {tries} attempts: {exc}")
                back = 2 ** attempt
                print(f"    retry in {back}s ({exc})", file=sys.stderr)
                time.sleep(back)
        raise RuntimeError("unreachable")


# ---- ticker resolution ----------------------------------------------------

def cache_path(name):
    root = os.environ.get("RESEARCH_FEED_STATE") or os.path.join(
        os.environ.get("RESEARCH_PROJECT_DIR") or os.getcwd(), ".feed-state")
    os.makedirs(root, exist_ok=True)
    return os.path.join(root, name)


def ticker_map(client):
    """{TICKER: (cik_int, company_name)} from SEC's own ticker file, cached."""
    path = cache_path("company_tickers.json")
    fresh = os.path.exists(path) and (time.time() - os.path.getmtime(path)) < TICKER_TTL
    if not fresh:
        try:
            data = client.get(TICKERS_URL)
            with open(path, "wb") as fh:
                fh.write(data)
        except Exception as exc:                      # noqa: BLE001
            if not os.path.exists(path):
                raise SystemExit(f"cannot fetch the ticker map: {exc}")
            print(f"WARNING: using stale ticker cache ({exc})", file=sys.stderr)
    with open(path, encoding="utf-8") as fh:
        raw = json.load(fh)
    return {v["ticker"].upper(): (int(v["cik_str"]), v["title"])
            for v in raw.values()}


def resolve(client, tickers, ciks):
    """[(cik_int, name, ticker)] for the requested companies."""
    out, tmap = [], None
    for t in tickers or []:
        tmap = tmap if tmap is not None else ticker_map(client)
        key = t.upper().lstrip("$")
        if key not in tmap:
            # A missing ticker is nearly always a typo or a delisting; naming it
            # is more useful than silently returning an empty feed.
            near = [k for k in tmap if k.startswith(key[:3])][:5]
            print(f"WARNING: ticker {t!r} is not in SEC's ticker map"
                  + (f" (did you mean {', '.join(near)}?)" if near else ""),
                  file=sys.stderr)
            continue
        cik, name = tmap[key]
        out.append((cik, name, key))
    for c in ciks or []:
        out.append((int(str(c).lstrip("0") or 0), None, None))
    return out


# ---- submissions mode -----------------------------------------------------

def form_matches(form, wanted, exclude, amendments=True):
    base = form.split("/")[0].strip().upper()
    full = form.strip().upper()
    for e in exclude or []:
        if full == e.upper() or base == e.upper().split("/")[0]:
            return False
    if not wanted:
        return True
    for w in wanted:
        w = w.strip().upper()
        if full == w:
            return True
        if amendments and base == w.split("/")[0] and full.startswith(w.split("/")[0]):
            return True
    return False


def raw_document(primary):
    """The machine-readable original behind an XSL-rendered primary document.

    submissions.json points ownership and 144 filings at their stylesheet output
    (`xslF345X06/form4.xml`); the raw XML sits at the same name with that
    directory removed. Parsing the raw file rather than the rendering is what
    makes the Form 4 table possible at all.
    """
    if "/" in primary and primary.split("/")[0].lower().startswith("xsl"):
        return primary.split("/", 1)[1]
    return primary


def from_submissions(client, cik, name, ticker, args):
    padded = str(cik).zfill(10)
    try:
        data = json.loads(client.get(SUBMISSIONS.format(cik=padded)))
    except urllib.error.HTTPError as exc:
        print(f"WARNING: no submissions for CIK {cik}: HTTP {exc.code}", file=sys.stderr)
        return []
    company = data.get("name") or name or f"CIK {cik}"
    tickers = data.get("tickers") or ([ticker] if ticker else [])
    blocks = [data["filings"]["recent"]]

    # `recent` holds the last 1,000 filings only. For an active large-cap that
    # can be under a year, so a backfill silently stops at the boundary unless
    # the older shards are pulled in too.
    earliest = min(data["filings"]["recent"].get("filingDate") or [""], default="")
    for shard in data["filings"].get("files") or []:
        if args.since and shard.get("filingTo", "") < args.since:
            continue
        if not args.since and earliest and shard.get("filingTo", "") < earliest:
            pass
        print(f"    + older shard {shard['name']} "
              f"({shard.get('filingFrom')}..{shard.get('filingTo')})", file=sys.stderr)
        blocks.append(json.loads(client.get(
            f"https://data.sec.gov/submissions/{shard['name']}")))

    rows = []
    for block in blocks:
        forms = block.get("form") or []
        for i, form in enumerate(forms):
            def f(key, default=""):
                seq = block.get(key) or []
                return seq[i] if i < len(seq) else default

            filed = f("filingDate")
            if args.since and filed < args.since:
                continue
            if args.until and filed > args.until:
                continue
            if not form_matches(form, args.form, args.exclude_form,
                                not args.no_amendments):
                continue
            acc = f("accessionNumber")
            acc_plain = acc.replace("-", "")
            primary = f("primaryDocument")
            items = [x.strip() for x in (f("items") or "").split(",") if x.strip()]
            base = ARCHIVES.format(cik=cik, acc=acc_plain)
            rows.append({
                "id": acc,
                "accession": acc,
                "cik": cik,
                "company": company,
                "tickers": tickers,
                "form": form,
                "filed": filed,
                "report_date": f("reportDate") or None,
                "accepted": f("acceptanceDateTime") or None,
                "items": items,
                "item_labels": [ITEMS_8K.get(x, "unrecognised item " + x) for x in items],
                "material_items": sorted(set(items) & MATERIAL_8K),
                "primary_document": primary or None,
                # Filings before roughly 2001 list no primary document at all;
                # the whole submission is one SGML .txt file. Leaving this None
                # made convert.py skip the body and write an empty note.
                "raw_document": raw_document(primary) if primary else f"{acc}.txt",
                "primary_description": f("primaryDocDescription") or None,
                "size": f("size") or None,
                "is_xbrl": bool(f("isInlineXBRL") or f("isXBRL")),
                "base_url": base,
                "url": f"{base}/{raw_document(primary)}" if primary else f"{base}/{acc}.txt",
                "index_url": f"{base}/{acc}-index.htm",
                "header_url": f"{base}/{acc}-index-headers.html",
                "matched_files": [],
                "source": "submissions",
            })
    return rows


# ---- full-text search mode ------------------------------------------------

# "KINDER MORGAN, INC.  (KMI, EPB)  (CIK 0001506307)" — the ticker group is a
# comma-separated list, not a single symbol, and a one-symbol pattern leaves the
# whole list glued onto the company name.
DISPLAY_RE = re.compile(
    r"^(.*?)\s*(?:\((\.?[A-Z0-9.\-]{1,10}(?:\s*,\s*[A-Z0-9.\-]{1,10})*)\)\s*)?"
    r"\(CIK (\d{10})\)\s*$")


def from_fts(client, args):
    params = {}
    if args.query:
        params["q"] = args.query
    if args.form:
        params["forms"] = ",".join(args.form)
    if args.since:
        params["startdt"] = args.since
    if args.until:
        params["enddt"] = args.until
    if args.cik_filter:
        params["ciks"] = ",".join(str(c).zfill(10) for c in args.cik_filter)

    by_accession, total, start = {}, None, 0
    while True:
        page = dict(params, **({"from": start} if start else {}))
        data = json.loads(client.get(FTS, list(page.items())))
        hits = data.get("hits", {}).get("hits") or []
        if total is None:
            total = data["hits"]["total"]["value"]
            print(f"full-text search: {total} matching document(s)", file=sys.stderr)
        for h in hits:
            s = h["_source"]
            acc = s["adsh"]
            filename = (h["_id"].split(":", 1) + [""])[1]
            entry = by_accession.get(acc)
            if entry is None:
                display = (s.get("display_names") or [""])[0]
                m = DISPLAY_RE.match(" ".join(display.split()))
                company = m.group(1) if m else display
                syms = [t.strip() for t in (m.group(2) or "").split(",") if t.strip()] if m else []
                cik = int((s.get("ciks") or ["0"])[0])
                acc_plain = acc.replace("-", "")
                base = ARCHIVES.format(cik=cik, acc=acc_plain)
                items = s.get("items") or []
                entry = by_accession[acc] = {
                    "id": acc,
                    "accession": acc,
                    "cik": cik,
                    "company": company,
                    "tickers": syms,
                    "form": s.get("form") or (s.get("root_forms") or [""])[0],
                    "filed": s.get("file_date"),
                    "report_date": s.get("period_ending"),
                    "accepted": None,
                    "items": items,
                    "item_labels": [ITEMS_8K.get(x, "unrecognised item " + x)
                                    for x in items],
                    "material_items": sorted(set(items) & MATERIAL_8K),
                    "primary_document": None,
                    "raw_document": None,
                    "primary_description": s.get("file_description"),
                    "size": None,
                    "is_xbrl": None,
                    "base_url": base,
                    "url": f"{base}/{acc}.txt",
                    "index_url": f"{base}/{acc}-index.htm",
                    "header_url": f"{base}/{acc}-index-headers.html",
                    # Full-text search matches a *document*, not a filing, and
                    # the match is often in an exhibit rather than the form
                    # itself. Keeping the filename means convert.py can fetch the
                    # document that actually contains the phrase.
                    "matched_files": [],
                    "sic": (s.get("sics") or [None])[0],
                    "location": (s.get("biz_locations") or [None])[0],
                    "source": "full-text-search",
                }
            if filename and filename not in entry["matched_files"]:
                entry["matched_files"].append(filename)
                entry.setdefault("matched_types", []).append(s.get("file_type"))
            if not entry["primary_document"] and filename:
                entry["primary_document"] = filename
                entry["raw_document"] = filename
                entry["url"] = f"{entry['base_url']}/{filename}"

        start += FTS_PAGE
        if start >= min(total, args.limit or total, HARD_CAP) or not hits:
            break
        if start > FTS_MAX_FROM:
            print(f"WARNING: full-text search cannot page past {FTS_MAX_FROM} hits; "
                  f"{total - start} match(es) not retrieved. Narrow the window.",
                  file=sys.stderr)
            break
    return list(by_accession.values())


def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--since", help="inclusive lower bound on filing date, YYYY-MM-DD")
    ap.add_argument("--until", help="inclusive upper bound, YYYY-MM-DD")
    ap.add_argument("--ticker", action="append", help="watchlist ticker (repeatable)")
    ap.add_argument("--cik", action="append", help="watchlist CIK (repeatable)")
    ap.add_argument("--form", action="append",
                    help="form type, e.g. 8-K (repeatable, ORed)")
    ap.add_argument("--exclude-form", action="append",
                    help="form type to drop, e.g. 144 (repeatable)")
    ap.add_argument("--no-amendments", action="store_true",
                    help="--form 8-K stops matching 8-K/A")
    ap.add_argument("--query", help="EDGAR full-text search expression; switches to "
                                    "search mode across all companies")
    ap.add_argument("--cik-filter", action="append",
                    help="restrict full-text search to these CIKs (repeatable)")
    ap.add_argument("--limit", type=int, help="stop after N filings")
    ap.add_argument("--user-agent", help="User-Agent header; SEC 403s without a "
                                         "descriptive one (default: $SEC_USER_AGENT "
                                         "or edgar-feed/1.0 with $SEC_CONTACT_EMAIL)")
    ap.add_argument("--list-forms", action="store_true",
                    help="print the form-type and 8-K item vocabulary and exit")
    ap.add_argument("--out", help="output directory for manifest.json")
    args = ap.parse_args()

    if args.list_forms:
        print(KNOWN_FORMS)
        print("Form 8-K item codes:")
        for k in sorted(ITEMS_8K):
            flag = "  *" if k in MATERIAL_8K else "   "
            print(f"{flag} {k}  {ITEMS_8K[k]}")
        print("\n  * = item that reports a material adverse or structural event")
        return
    if not args.out:
        ap.error("--out is required")

    client = Client(args.user_agent)
    watchlist = resolve(client, args.ticker, args.cik)
    if args.query and (args.ticker or args.cik) and not watchlist:
        raise SystemExit(
            "none of the requested companies resolved; refusing to broaden the "
            "full-text query to all of EDGAR"
        )

    if args.query:
        # A phrase plus a watchlist means "search these companies", not "ignore
        # the phrase and list their submissions". Resolve tickers first, then
        # pass the CIKs to EDGAR full-text search.
        scoped = [str(cik) for cik, _, _ in watchlist]
        args.cik_filter = list(dict.fromkeys((args.cik_filter or []) + scoped))
        rows = from_fts(client, args)
        mode = "full-text-search"
    elif watchlist:
        rows = []
        for cik, name, ticker in watchlist:
            print(f"  {ticker or cik}…", file=sys.stderr)
            got = from_submissions(client, cik, name, ticker, args)
            print(f"    {len(got)} filing(s)", file=sys.stderr)
            rows += got
        mode = "submissions"
    elif args.form:
        rows = from_fts(client, args)
        mode = "full-text-search"
    else:
        ap.error("give --ticker/--cik for a watchlist, or --query/--form for a "
                 "full-text search; a bare date window would pull all of EDGAR")

    rows.sort(key=lambda r: (r.get("filed") or "", r.get("accession") or ""))
    if args.limit:
        rows = rows[: args.limit]

    os.makedirs(args.out, exist_ok=True)
    manifest = {
        "feed": "edgar",
        "mode": mode,
        "since": args.since,
        "until": args.until,
        "forms": args.form,
        "query": args.query,
        "watchlist": [{"cik": c, "name": n, "ticker": t} for c, n, t in watchlist],
        "fetched_count": len(rows),
        "documents": rows,
    }
    path = os.path.join(args.out, "manifest.json")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, indent=2, ensure_ascii=False)

    print(f"\n{len(rows)} filing(s) -> {path}", file=sys.stderr)
    if rows:
        print(f"filing date range: {rows[0]['filed']} .. {rows[-1]['filed']}",
              file=sys.stderr)
        counts = {}
        for r in rows:
            counts[r["form"]] = counts.get(r["form"], 0) + 1
        for k, v in sorted(counts.items(), key=lambda kv: -kv[1])[:15]:
            print(f"  {v:5d}  {k}", file=sys.stderr)
        flagged = [r for r in rows if r["material_items"]]
        if flagged:
            print(f"\n{len(flagged)} filing(s) carry a material 8-K item:", file=sys.stderr)
            for r in flagged[:20]:
                labels = "; ".join(ITEMS_8K.get(i, i) for i in r["material_items"])
                print(f"  {r['filed']} {r['company'][:34]:34s} {labels}", file=sys.stderr)


if __name__ == "__main__":
    main()
