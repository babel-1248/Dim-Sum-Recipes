#!/usr/bin/env python3
"""Stage 1: pull PubMed citation metadata for a date window into a manifest.

"Metadata" here means the full PubMed record — title, structured abstract, MeSH
headings, publication types, DOI and PMC id. That is everything the relevance
pass needs, and it is deliberately the boundary: full text lives in PubMed
Central and is fetched only for survivors, by convert.py.

Two API facts shape this script:

  * esearch caps its id list at 9,999 per request and says so only in a
    `warninglist` buried in the response ("start and count adjusted to 0,
    9999"). Reading `count` and assuming you received that many ids silently
    loses everything past 9,999. This script uses the history server instead,
    so efetch pages through the result set server-side and the cap never bites.
  * NCBI allows 3 requests/second without an API key and 10 with one. Exceeding
    it gets the IP blocked, not throttled. Set NCBI_API_KEY (free, from an NCBI
    account) to go faster.

Entrez does the heavy filtering server-side — MeSH terms, publication types,
journals, affiliations — so push as much as possible into --term/--mesh/--query
and leave only real judgement to the relevance pass.

Usage:
  python3 fetch.py --since 2026-08-01 --out DIR --term "base editing"
"""
import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

ESEARCH = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi"
EFETCH = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi"
# Sent as the `tool` parameter on every request. NCBI asks for "a string with no
# internal spaces that uniquely identifies the software producing the request".
# Note their stricter line: "merely providing values for tool and email in
# requests is not sufficient to comply with this policy; these values must be
# registered with NCBI." Registering is a manual step nobody has done here, so
# treat this as identification, not compliance.
TOOL = "pubmed-feed"
BATCH = 200              # records per efetch; NCBI's own guidance for XML
HARD_CAP = 50000         # backstop against a runaway query

KNOWN_SYNTAX = """\
Entrez search field tags (used in --query; --term/--mesh/--journal wrap these):
  [Title/Abstract] [tiab]   title or abstract      [Title] [ti]
  [MeSH Terms] [mh]         indexed MeSH heading   [MeSH Major Topic] [majr]
  [Publication Type] [pt]   Review, Clinical Trial, Meta-Analysis, …
  [Journal] [ta]            journal title          [Author] [au]
  [Affiliation] [ad]        author affiliation     [Text Word] [tw]
  [Substance] [nm]          chemical               [Grant Number] [gr]
  [Language] [la]           eng, fre, …            [Filter] [sb]  e.g. free full text[sb]

Operators are AND, OR, NOT (uppercase). Group with parentheses, quote phrases.
Truncate with * (cardio*). MeSH auto-explodes to narrower terms unless you
write "X"[MeSH Terms:noexp].

Date filtering is done with --since/--until + --datetype, not in the query, so
the window stays visible to the state file.

Not expressible server-side, so these belong to the relevance pass:
  study quality, whether the finding is positive, sample size, how the result
  bears on a specific question, anything about the abstract's meaning.
"""


def _ws(text):
    return " ".join((text or "").split())


def itertext(el):
    """Element text including inline markup (<i>, <sup>) that titles carry."""
    return _ws("".join(el.itertext())) if el is not None else ""


class Client:
    """E-utilities client that respects NCBI's request-rate ceiling."""

    def __init__(self, api_key=None, email=None):
        self.api_key = api_key
        self.email = email
        self.interval = 0.11 if api_key else 0.34
        self._last = 0.0

    def _common(self):
        params = [("tool", TOOL)]
        if self.email:
            params.append(("email", self.email))
        if self.api_key:
            params.append(("api_key", self.api_key))
        return params

    def get(self, url, params, tries=4):
        wait = self._last + self.interval - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        full = url + "?" + urllib.parse.urlencode(params + self._common())
        for attempt in range(tries):
            try:
                req = urllib.request.Request(full, headers={"User-Agent": TOOL})
                with urllib.request.urlopen(req, timeout=120) as resp:
                    self._last = time.monotonic()
                    return resp.read()
            except urllib.error.HTTPError as exc:
                # 429 means the 3/s (or 10/s) ceiling was crossed. Retrying
                # hard would deepen the hole, so back off generously and say
                # plainly what happened rather than raising a traceback.
                if exc.code not in (429, 500, 502, 503, 504) or attempt == tries - 1:
                    if exc.code == 429:
                        raise SystemExit(
                            f"NCBI returned 429 after {attempt + 1} attempts — the "
                            f"request-rate ceiling was crossed. Wait a minute and "
                            f"re-run; set NCBI_API_KEY to raise 3/s to 10/s. The "
                            f"state file means nothing is lost.")
                    raise
                back = float(exc.headers.get("Retry-After") or 0) or 5 * (attempt + 1)
                print(f"    HTTP {exc.code}; retry in {back:.0f}s", file=sys.stderr)
                time.sleep(back)
            except (urllib.error.URLError, TimeoutError) as exc:
                if attempt == tries - 1:
                    raise SystemExit(f"NCBI unreachable after {tries} attempts: {exc}")
                back = 2 ** attempt
                print(f"    retry in {back}s ({exc})", file=sys.stderr)
                time.sleep(back)
        raise RuntimeError("unreachable")


def quoted(v):
    return f'"{v}"' if " " in v and not v.startswith('"') else v


def build_term(args):
    groups = []
    if args.term:
        tag = args.term_field
        groups.append("(" + " OR ".join(f"{quoted(t)}[{tag}]" for t in args.term) + ")")
    if args.mesh:
        groups.append("(" + " OR ".join(
            f"{quoted(m)}[MeSH Terms]" for m in args.mesh) + ")")
    if args.journal:
        groups.append("(" + " OR ".join(
            f"{quoted(j)}[Journal]" for j in args.journal) + ")")
    if args.pub_type:
        groups.append("(" + " OR ".join(
            f"{quoted(p)}[Publication Type]" for p in args.pub_type) + ")")
    if args.query:
        groups.append(f"({args.query})")
    return " AND ".join(groups)


def esearch(client, term, args):
    params = [("db", "pubmed"), ("term", term), ("retmode", "json"),
              ("usehistory", "y"), ("retmax", "0")]
    if args.since:
        params += [("datetype", args.datetype),
                   ("mindate", args.since.replace("-", "/")),
                   ("maxdate", (args.until or args.since).replace("-", "/"))]
    data = json.loads(client.get(ESEARCH, params))["esearchresult"]
    for msg in (data.get("warninglist") or {}).get("outputmessages") or []:
        print(f"NOTE: esearch says {msg}", file=sys.stderr)
    for msg in (data.get("errorlist") or {}).get("phrasesnotfound") or []:
        print(f"WARNING: phrase not found in PubMed: {msg}", file=sys.stderr)
    return int(data["count"]), data.get("webenv"), data.get("querykey")


def date_from(el):
    """<PubDate>/<PubMedPubDate> -> YYYY-MM-DD, or the MedlineDate string."""
    if el is None:
        return None
    medline = el.findtext("MedlineDate")
    if medline:
        return _ws(medline)
    year = el.findtext("Year")
    if not year:
        return None
    month, day = el.findtext("Month") or "", el.findtext("Day") or ""
    months = {"Jan": "01", "Feb": "02", "Mar": "03", "Apr": "04", "May": "05",
              "Jun": "06", "Jul": "07", "Aug": "08", "Sep": "09", "Oct": "10",
              "Nov": "11", "Dec": "12"}
    month = months.get(month[:3], month.zfill(2) if month.isdigit() else "")
    parts = [year] + ([month] + ([day.zfill(2)] if day else []) if month else [])
    return "-".join(parts)


def exact_date(value, fallback):
    """Use an exact YYYY-MM-DD date, otherwise resume conservatively."""
    if (isinstance(value, str) and len(value) == 10
            and value[4] == "-" and value[7] == "-"
            and value.replace("-", "").isdigit()):
        return value
    return fallback


def window_date(record, datetype, fallback):
    """Return the date matching the Entrez window basis for state/capping."""
    if datetype == "mdat":
        # PubMed XML's DateRevised is article metadata, not a guaranteed copy
        # of the Entrez index modification timestamp used by mdat. Re-scan the
        # window start instead of advancing state from a different date field.
        return fallback
    value = {
        "edat": record.get("entrez_date"),
        "pdat": record.get("pub_date"),
    }[datetype]
    return exact_date(value, fallback)


def parse_article(art):
    cite = art.find("MedlineCitation")
    if cite is None:
        return None
    article = cite.find("Article")
    pmid = cite.findtext("PMID")

    abstract = []
    for node in article.findall(".//Abstract/AbstractText") if article is not None else []:
        label = node.get("Label")
        text = itertext(node)
        if not text:
            continue
        abstract.append(f"**{label.title()}:** {text}" if label else text)

    authors = []
    for au in article.findall(".//AuthorList/Author") if article is not None else []:
        collective = au.findtext("CollectiveName")
        if collective:
            authors.append({"name": _ws(collective), "collective": True})
            continue
        last, fore = au.findtext("LastName"), au.findtext("ForeName")
        if last:
            authors.append({"name": _ws(f"{fore} {last}" if fore else last)})

    ids = {i.get("IdType"): _ws(i.text)
           for i in art.findall(".//ArticleIdList/ArticleId") if i.text}

    mesh = []
    for mh in cite.findall(".//MeshHeadingList/MeshHeading"):
        desc = mh.find("DescriptorName")
        if desc is None:
            continue
        quals = [itertext(q) for q in mh.findall("QualifierName")]
        entry = {"term": itertext(desc),
                 "major": desc.get("MajorTopicYN") == "Y"}
        if quals:
            entry["qualifiers"] = quals
        mesh.append(entry)

    history = {p.get("PubStatus"): date_from(p)
               for p in art.findall(".//PubmedData/History/PubMedPubDate")}
    journal = article.find("Journal") if article is not None else None

    return {
        "pmid": pmid,
        "title": itertext(article.find("ArticleTitle")) if article is not None else "",
        "abstract": "\n\n".join(abstract),
        "authors": authors,
        "journal": itertext(journal.find("Title")) if journal is not None else None,
        "journal_abbrev": (itertext(journal.find("ISOAbbreviation"))
                           if journal is not None else None),
        "volume": (journal.findtext(".//Volume") if journal is not None else None),
        "issue": (journal.findtext(".//Issue") if journal is not None else None),
        "pages": (article.findtext(".//Pagination/MedlinePgn")
                  if article is not None else None),
        "pub_date": date_from(journal.find(".//PubDate")) if journal is not None else None,
        # entrez = the date PubMed itself indexed the record, which is what
        # datetype=edat windows on and therefore the honest watermark.
        "entrez_date": history.get("entrez"),
        "pubmed_date": history.get("pubmed"),
        "modified_date": date_from(cite.find("DateRevised")),
        "doi": ids.get("doi"),
        "pmc": ids.get("pmc"),
        "pub_types": [itertext(t) for t in article.findall(".//PublicationType")]
                     if article is not None else [],
        "mesh": mesh,
        "keywords": [itertext(k) for k in cite.findall(".//KeywordList/Keyword")],
        "language": article.findtext(".//Language") if article is not None else None,
        "url": f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/",
        "doi_url": f"https://doi.org/{ids['doi']}" if ids.get("doi") else None,
        "pmc_url": (f"https://www.ncbi.nlm.nih.gov/pmc/articles/{ids['pmc']}/"
                    if ids.get("pmc") else None),
    }


def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--since", help="inclusive lower bound on --datetype, YYYY-MM-DD")
    ap.add_argument("--until", help="inclusive upper bound, YYYY-MM-DD (default: --since)")
    ap.add_argument("--datetype", default="edat", choices=["edat", "pdat", "mdat"],
                    help="edat = entered PubMed (default), pdat = published, "
                         "mdat = last modified")
    ap.add_argument("--term", action="append", help="search term (repeatable, ORed)")
    ap.add_argument("--term-field", default="Title/Abstract",
                    help="field tag --term searches (default: Title/Abstract)")
    ap.add_argument("--mesh", action="append", help="MeSH heading (repeatable, ORed)")
    ap.add_argument("--journal", action="append", help="journal (repeatable, ORed)")
    ap.add_argument("--pub-type", action="append",
                    help="publication type, e.g. Review (repeatable, ORed)")
    ap.add_argument("--query", help="raw Entrez query, ANDed with the rest")
    ap.add_argument("--limit", type=int, help="stop after N records (safety cap)")
    ap.add_argument("--api-key", default=os.environ.get("NCBI_API_KEY"),
                    help="NCBI API key (default: $NCBI_API_KEY); raises 3/s to 10/s")
    ap.add_argument("--email", default=os.environ.get("NCBI_EMAIL"),
                    help="contact address NCBI asks callers to send (default: $NCBI_EMAIL)")
    ap.add_argument("--list-syntax", action="store_true",
                    help="print the Entrez field vocabulary and exit")
    ap.add_argument("--out", help="output directory for manifest.json")
    args = ap.parse_args()

    if args.list_syntax:
        print(KNOWN_SYNTAX)
        return
    if not args.out:
        ap.error("--out is required")

    term = build_term(args)
    if not term:
        ap.error("give at least --term, --mesh, --journal, --pub-type or --query; "
                 "a date window alone would pull all of PubMed")

    os.makedirs(args.out, exist_ok=True)
    client = Client(args.api_key, args.email)
    print(f"term: {term}", file=sys.stderr)
    if not args.api_key:
        print("no API key: limited to 3 requests/second (set NCBI_API_KEY to "
              "raise it to 10)", file=sys.stderr)

    count, webenv, query_key = esearch(client, term, args)
    print(f"esearch count: {count}", file=sys.stderr)

    target = min(count, args.limit or count, HARD_CAP)
    if count > target:
        print(f"WARNING: {count} records match but only fetching {target}; "
              f"narrow the query or shorten the window.", file=sys.stderr)

    records, skipped = [], 0
    for start in range(0, target, BATCH):
        body = client.get(EFETCH, [
            ("db", "pubmed"), ("retmode", "xml"), ("WebEnv", webenv),
            ("query_key", query_key), ("retstart", str(start)),
            ("retmax", str(min(BATCH, target - start))),
        ])
        root = ET.fromstring(body)
        for art in root.findall("PubmedArticle"):
            rec = parse_article(art)
            if rec and rec["pmid"]:
                records.append(rec)
        # Book chapters come back in a different container and carry none of
        # the fields the relevance pass reads; count them rather than pretend.
        skipped += len(root.findall("PubmedBookArticle"))
        print(f"  {min(start + BATCH, target)}/{target}", file=sys.stderr)

    if skipped:
        print(f"NOTE: skipped {skipped} book/chapter record(s)", file=sys.stderr)

    for record in records:
        record["window_date"] = window_date(record, args.datetype, args.since)
    records.sort(key=lambda r: (r.get("window_date") or "", r["pmid"]))
    manifest = {
        "feed": "pubmed",
        "since": args.since,
        "until": args.until or args.since,
        "term": term,
        "datetype": args.datetype,
        "fetched_count": len(records),
        "api_reported_count": count,
        "documents": records,
    }
    path = os.path.join(args.out, "manifest.json")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, indent=2, ensure_ascii=False)

    print(f"\n{len(records)} records -> {path}", file=sys.stderr)
    if records:
        print(f"{args.datetype} window-date range: {records[0]['window_date']} .. "
              f"{records[-1]['window_date']}", file=sys.stderr)
        oa = sum(1 for r in records if r.get("pmc"))
        no_abs = sum(1 for r in records if not r.get("abstract"))
        print(f"{oa} with a PMC id (full text likely), {no_abs} with no abstract",
              file=sys.stderr)
        counts = {}
        for r in records:
            for t in r.get("pub_types") or []:
                counts[t] = counts.get(t, 0) + 1
        for k, v in sorted(counts.items(), key=lambda kv: -kv[1])[:10]:
            print(f"  {v:5d}  {k}", file=sys.stderr)


if __name__ == "__main__":
    main()
