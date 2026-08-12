#!/usr/bin/env python3
"""Stage 1 for both congressional chambers: bills and roll call votes.

Shared by the house and senate feeds because their bill half is identical —
govinfo bulkdata BILLSTATUS, differing only in which bill types are listed. Only
the vote half is chamber-specific, so that is the only part that branches.

  bills   govinfo bulkdata BILLSTATUS
            house   hr, hres, hjres, hconres
            senate  s,  sres, sjres, sconres
  votes   house   clerk.house.gov EVS  (paginated ROLL_NNN.asp index + roll XML)
          senate  senate.gov LIS       (one menu per session + one file per vote)

Both chambers emit the same item shape, including a `bill_ref` naming the bill a
vote concerned, so the converter needs no chamber-specific lookup logic.

"Since" means *any action in the window*, not introduced-in-the-window. Bulkdata
only exposes a file lastModified stamp, which is a superset of real activity, so
that is a prefilter and the action dates inside each BILLSTATUS are the real filter.

Full bill text and vote->bill detail are NOT fetched here; congress_convert.py
does that for survivors of the relevance pass only.

Usage:
  python3 congress_fetch.py --chamber house --since 2026-08-04 --out DIR
"""
import argparse
import concurrent.futures
import datetime
import html
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET

BULK = "https://www.govinfo.gov/bulkdata/json/BILLSTATUS/{congress}/{bt}"
BILLSTATUS_DOC = ("https://www.govinfo.gov/bulkdata/BILLSTATUS/{congress}/{bt}/"
                  "BILLSTATUS-{congress}{bt}{num}.xml")
# index.asp lists only the ~10 most recent votes; the full year is paginated in
# hundreds. Using index.asp silently returns nothing for any older window.
EVS_PAGE = "https://clerk.house.gov/evs/{year}/ROLL_{page:03d}.asp"
EVS_ROLL = "https://clerk.house.gov/evs/{year}/roll{roll:03d}.xml"
EVS_MAX_PAGES = 20
SEN_MENU = ("https://www.senate.gov/legislative/LIS/roll_call_lists/"
            "vote_menu_{congress}_{session}.xml")
SEN_VOTE = ("https://www.senate.gov/legislative/LIS/roll_call_votes/"
            "vote{congress}{session}/vote_{congress}_{session}_{num:05d}.xml")

UA = {"User-Agent": "sync-us-government-feeds (personal note archive)"}
MONTHS = "Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec".split()

CHAMBERS = {
    "house": ["hr", "hres", "hjres", "hconres"],
    "senate": ["s", "sres", "sjres", "sconres"],
}


def get(url, headers=None, retries=4, timeout=90):
    hdr = dict(UA)
    if headers:
        hdr.update(headers)
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers=hdr)
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.read()
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                raise
            if exc.code == 429 or exc.code >= 500:
                time.sleep(2 ** attempt)
                continue
            raise
        except (urllib.error.URLError, TimeoutError):
            if attempt == retries - 1:
                raise
            time.sleep(2 ** attempt)
    raise RuntimeError(f"exhausted retries: {url}")


def txt(el, path, default=None):
    if el is None:
        return default
    found = el.find(path)
    if found is None:
        return default
    return (("".join(found.itertext())).strip()) or default


def congress_start_year(congress):
    return (congress - 1) * 2 + 1789


def current_congress(when):
    year = when.year
    start = year if year % 2 else year - 1
    return (start - 1789) // 2 + 1


def session_for_year(congress, year):
    """119th Congress: 2025 is session 1, 2026 is session 2."""
    return year - congress_start_year(congress) + 1


# Free-text bill references: "H R 8884" (House Clerk), "H.R. 5334" (Senate LIS).
# 'R' must be an explicit alternative or "H R 8884" fails to match; it precedes
# RES so the regex backtracks correctly on "H RES 55".
LEGIS_RE = re.compile(r"^\s*([HS])\s*(R|J\s*RES|CON\s*RES|RES)?\s*(\d+)\s*$", re.I)


def parse_legis_num(s):
    """'H CON RES 89' -> ('hconres', '89'). None for procedural votes."""
    if not s:
        return None
    m = LEGIS_RE.match(s.replace(".", " "))
    if not m:
        return None
    chamber, kind, num = m.group(1).lower(), m.group(2), m.group(3)
    kind = re.sub(r"\s+", "", kind).lower() if kind else ""
    return f"{chamber}{kind}", num


# ------------------------------------------------------------------------ bills

def parse_billstatus(raw, since=None, until=None):
    """Parse one BILLSTATUS.

    With since/until, returns None unless the bill has an action in the window —
    that is the feed's real filter. With neither, parses unconditionally, which is
    how a roll call looks up the bill it voted on.
    """
    root = ET.fromstring(raw)
    b = root.find("bill")
    if b is None:
        return None

    # The same action is listed once per reporting source system; collapse on
    # (date, text) or every note shows duplicates.
    actions, seen = [], set()
    for it in b.findall("actions/item"):
        d = txt(it, "actionDate")
        if not d:
            continue
        body = txt(it, "text", "")
        if (d, body) in seen:
            continue
        seen.add((d, body))
        actions.append({"date": d, "text": body, "type": txt(it, "type", ""),
                        "chamber": txt(it, "sourceSystem/name", "")})
    actions.sort(key=lambda a: a["date"])
    if since and until:
        in_window = [a for a in actions if since <= a["date"] <= until]
        if not in_window:
            return None
    else:
        in_window = []

    versions = []
    for it in b.findall("textVersions/item"):
        url = None
        for f in it.findall(".//formats/item"):
            u = txt(f, "url")
            if u and u.endswith(".xml"):
                url = u
                break
        versions.append({"type": txt(it, "type"), "date": txt(it, "date"), "url": url})
    versions.sort(key=lambda v: v["date"] or "")

    # The element is <summary>, not <item>: summaries//item silently matches
    # nothing and drops every CRS summary.
    summaries = []
    for it in b.findall("summaries/summary"):
        body = txt(it, "text")
        if body:
            summaries.append({"date": txt(it, "actionDate"),
                              "desc": txt(it, "actionDesc"), "text": body})
    summaries.sort(key=lambda s: s["date"] or "")

    sponsors = [txt(it, "fullName") or txt(it, "lastName")
                for it in b.findall("sponsors/item")]
    sponsors = [s for s in sponsors if s]

    bt = (txt(b, "type") or "").lower()
    num = txt(b, "number") or ""
    return {
        "kind": "bill",
        "id": f"{bt}{num}",
        "bill_type": bt.upper(),
        "number": num,
        "congress": txt(b, "congress"),
        "title": txt(b, "title"),
        "introduced_date": txt(b, "introducedDate"),
        "update_date": txt(b, "updateDate"),
        "origin_chamber": txt(b, "originChamber"),
        "policy_area": txt(b, "policyArea/name"),
        "subjects": sorted({txt(s, "name")
                            for s in b.findall("subjects/legislativeSubjects/item")
                            if txt(s, "name")}),
        "sponsors": sponsors,
        "cosponsors_count": len(b.findall("cosponsors/item")),
        # Only the committee's own <name>; committees/item/activities/item/name
        # holds activity labels ("Referred To") that are not committees.
        "committees": sorted({txt(c, "name") for c in b.findall("committees/item")
                              if txt(c, "name")}),
        # bill/latestAction, not .//latestAction — the latter also matches a
        # nested latestAction and returns a stale date.
        "latest_action": {"date": txt(b, "latestAction/actionDate"),
                          "text": txt(b, "latestAction/text")},
        "actions": actions,
        "window_actions": in_window,
        "summary": summaries[-1] if summaries else None,
        "text_versions": versions,
        "text_url": (versions[-1]["url"] if versions else None),
        "legislation_url": txt(b, "legislationUrl"),
        "date": (max(a["date"] for a in in_window) if in_window
                 else txt(b, "latestAction/actionDate")),
    }


def lookup_bill(congress, bill_type, number):
    """Fetch the BILLSTATUS for a bill a roll call voted on."""
    if not (congress and bill_type and number):
        return None
    try:
        return parse_billstatus(get(BILLSTATUS_DOC.format(
            congress=congress, bt=bill_type, num=number)))
    except Exception:  # noqa: BLE001 - a missing bill must not kill the vote
        return None


def fetch_bills(since, until, congress, bill_types, jobs, limit=None):
    since_dt = datetime.datetime.strptime(since, "%Y-%m-%d")
    candidates = []
    for bt in bill_types:
        try:
            files = json.loads(get(BULK.format(congress=congress, bt=bt),
                                   {"Accept": "application/json"},
                                   timeout=180))["files"]
        except Exception as exc:  # noqa: BLE001
            print(f"  {bt}: listing failed ({exc})", file=sys.stderr)
            continue
        recent = []
        for f in files:
            # The listing also carries a per-congress .zip bundle.
            if not (f.get("justFileName") or "").lower().endswith(".xml"):
                continue
            try:
                when = datetime.datetime.strptime(
                    f["formattedLastModifiedTime"], "%d-%b-%Y %H:%M")
            except (ValueError, KeyError):
                continue
            if when >= since_dt:
                recent.append(f)
        print(f"  {bt}: {len(files)} total, {len(recent)} modified since {since}",
              file=sys.stderr)
        candidates.extend(recent)

    if limit:
        candidates = candidates[:limit]
    print(f"  checking {len(candidates)} BILLSTATUS files for in-window actions…",
          file=sys.stderr)

    def one(f):
        try:
            return parse_billstatus(get(f["link"]), since, until)
        except Exception as exc:  # noqa: BLE001
            print(f"    {f.get('justFileName')}: {exc}", file=sys.stderr)
            return None

    out, done = [], 0
    with concurrent.futures.ThreadPoolExecutor(max_workers=jobs) as pool:
        for res in pool.map(one, candidates):
            done += 1
            if done % 200 == 0:
                print(f"    {done}/{len(candidates)}…", file=sys.stderr)
            if res:
                out.append(res)
    return out


# ------------------------------------------------------------------- house votes

ROW_RE = re.compile(r"<TR>(.*?)</TR>", re.S | re.I)
CELL_RE = re.compile(r"<TD[^>]*>(.*?)</TD>", re.S | re.I)


def strip_tags(s):
    return html.unescape(re.sub(r"<[^>]+>", " ", s or "")).strip()


def parse_house_index(raw, year):
    rows = []
    for row in ROW_RE.findall(raw.decode("latin-1", "replace")):
        cells = [strip_tags(c) for c in CELL_RE.findall(row)]
        if len(cells) < 6 or not cells[0].isdigit():
            continue
        try:
            day, mon = cells[1].split("-")
            date = f"{year}-{MONTHS.index(mon[:3].title()) + 1:02d}-{int(day):02d}"
        except (ValueError, IndexError):
            continue
        rows.append({"roll": int(cells[0]), "date": date, "legis_num": cells[2],
                     "question": cells[3], "desc": cells[5]})
    return rows


def parse_house_vote(raw, row):
    root = ET.fromstring(raw)
    md = root.find(".//vote-metadata")
    party = []
    for p in root.findall(".//totals-by-party"):
        party.append({"party": txt(p, "party"), "yea": txt(p, "yea-total"),
                      "nay": txt(p, "nay-total"), "present": txt(p, "present-total"),
                      "not_voting": txt(p, "not-voting-total")})
    tt = root.find(".//totals-by-vote")
    totals = {"yea": txt(tt, "yea-total"), "nay": txt(tt, "nay-total"),
              "present": txt(tt, "present-total"),
              "not_voting": txt(tt, "not-voting-total")} if tt is not None else {}

    members = []
    for rv in root.findall(".//recorded-vote"):
        leg = rv.find("legislator")
        if leg is None:
            continue
        members.append({"name": (leg.text or "").strip(), "party": leg.get("party"),
                        "state": leg.get("state"), "vote": txt(rv, "vote")})

    date = row["date"]
    raw_date = txt(md, "action-date")
    if raw_date:
        try:
            date = datetime.datetime.strptime(raw_date, "%d-%b-%Y").strftime("%Y-%m-%d")
        except ValueError:
            pass

    legis = txt(md, "legis-num") or row["legis_num"]
    parsed = parse_legis_num(legis)
    return {
        "kind": "vote", "chamber": "house",
        "id": f"h{date[:4]}-{row['roll']:05d}",
        "roll": row["roll"], "congress": txt(md, "congress"),
        "session": txt(md, "session"), "date": date, "time": txt(md, "action-time"),
        "legis_num": legis, "question": txt(md, "vote-question") or row["question"],
        "result": txt(md, "vote-result"), "vote_type": txt(md, "vote-type"),
        "title": txt(md, "vote-desc") or row["desc"],
        "is_nomination": False,
        "bill_ref": ({"type_slug": parsed[0], "number": parsed[1],
                      "name": legis, "via": "document"} if parsed else None),
        "party_totals": party, "totals": totals, "members": members,
    }


def fetch_house_votes(since, until, congress, jobs):
    years = sorted({since[:4], until[:4]})
    rows = []
    for year in years:
        found, pages = [], 0
        for page in range(0, EVS_MAX_PAGES * 100, 100):
            try:
                raw = get(EVS_PAGE.format(year=year, page=page))
            except urllib.error.HTTPError as exc:
                if exc.code == 404:
                    break
                print(f"  votes {year}: page {page} failed ({exc})", file=sys.stderr)
                break
            except Exception as exc:  # noqa: BLE001
                print(f"  votes {year}: page {page} failed ({exc})", file=sys.stderr)
                break
            batch = parse_house_index(raw, year)
            if not batch:
                break
            found.extend(batch)
            pages += 1
        keep = [r for r in found if since <= r["date"] <= until]
        print(f"  votes {year}: {len(found)} rolls across {pages} pages, "
              f"{len(keep)} in window", file=sys.stderr)
        for r in keep:
            r["year"] = year
        rows.extend(keep)

    def one(r):
        try:
            return parse_house_vote(get(EVS_ROLL.format(year=r["year"],
                                                        roll=r["roll"])), r)
        except Exception as exc:  # noqa: BLE001
            print(f"    roll {r['roll']}: {exc}", file=sys.stderr)
            return None

    if not rows:
        return []
    with concurrent.futures.ThreadPoolExecutor(max_workers=jobs) as pool:
        return [v for v in pool.map(one, rows) if v]


# ------------------------------------------------------------------ senate votes

def doc_type_slug(document_type):
    """'S.J.Res.' -> 'sjres'; 'H.R.' -> 'hr'; 'PN' -> 'pn' (a nomination)."""
    if not document_type:
        return None
    return re.sub(r"[^a-z]", "", document_type.lower()) or None


NON_BILL_TYPES = {"pn", "treatydoc", "samdt", "hamdt"}


def senate_bill_reference(document, amendment):
    """Which bill a Senate vote ultimately concerns.

    Amendment votes carry an empty <document_name> and name the underlying bill in
    <amendment_to_document_number>, so without this fallback roughly a quarter of
    non-nomination votes lose their bill entirely.
    """
    slug = (document or {}).get("type_slug")
    if document and slug and slug not in NON_BILL_TYPES and document.get("number"):
        return {"type_slug": slug, "number": document["number"],
                "name": document.get("name"), "via": "document"}
    parsed = parse_legis_num((amendment or {}).get("to_document"))
    if parsed:
        return {"type_slug": parsed[0], "number": parsed[1],
                "name": (amendment or {}).get("to_document"), "via": "amendment"}
    return None


def parse_senate_date(raw):
    """Senate stamps 'August 8, 2026,  04:36 AM'."""
    if not raw:
        return None, None
    cleaned = re.sub(r"\s+", " ", raw).strip()
    for fmt in ("%B %d, %Y, %I:%M %p", "%B %d, %Y %I:%M %p", "%B %d, %Y"):
        try:
            dt = datetime.datetime.strptime(cleaned, fmt)
            return dt.strftime("%Y-%m-%d"), dt.strftime("%-I:%M %p")
        except ValueError:
            continue
    return None, None


def parse_senate_menu(raw):
    root = ET.fromstring(raw)
    year = txt(root, "congress_year")
    rows = []
    for v in root.findall("votes/vote"):
        num, stamp = txt(v, "vote_number"), txt(v, "vote_date")
        if not (num and stamp and year):
            continue
        try:
            day, mon = stamp.split("-")
            date = f"{year}-{MONTHS.index(mon[:3].title()) + 1:02d}-{int(day):02d}"
        except (ValueError, IndexError):
            continue
        rows.append({"number": int(num), "date": date})
    return rows


def parse_senate_vote(raw, congress, session):
    root = ET.fromstring(raw)
    date, clock = parse_senate_date(txt(root, "vote_date"))

    members = [{"name": txt(m, "member_full") or txt(m, "last_name"),
                "party": txt(m, "party"), "state": txt(m, "state"),
                "vote": txt(m, "vote_cast")} for m in root.findall(".//member")]

    # Senate XML carries no party totals; derive them from the roll.
    by_party = {}
    for m in members:
        slot = by_party.setdefault(m.get("party") or "?",
                                   {"party": m.get("party") or "?", "yea": 0,
                                    "nay": 0, "present": 0, "not_voting": 0})
        v = (m.get("vote") or "").lower()
        if v in ("yea", "aye", "guilty"):
            slot["yea"] += 1
        elif v in ("nay", "no", "not guilty"):
            slot["nay"] += 1
        elif v.startswith("present"):
            slot["present"] += 1
        else:
            slot["not_voting"] += 1
    order = {"R": 0, "D": 1, "I": 2}
    party_totals = sorted(by_party.values(),
                          key=lambda p: (order.get(p["party"], 9), p["party"]))

    count = root.find("count")
    totals = {"yea": txt(count, "yeas", "0"), "nay": txt(count, "nays", "0"),
              "present": txt(count, "present", "0"),
              "not_voting": txt(count, "absent", "0")} if count is not None else {}

    doc = root.find("document")
    document = None
    if doc is not None:
        document = {"type": txt(doc, "document_type"),
                    "type_slug": doc_type_slug(txt(doc, "document_type")),
                    "number": txt(doc, "document_number"),
                    "name": txt(doc, "document_name"),
                    "title": txt(doc, "document_title"),
                    "congress": txt(doc, "document_congress") or str(congress)}

    purpose = txt(root, "amendment/amendment_purpose")
    if purpose and purpose.lower().startswith("no statement"):
        purpose = None
    amendment = {"number": txt(root, "amendment/amendment_number"),
                 "to_document": txt(root, "amendment/amendment_to_document_number"),
                 "purpose": purpose}
    if not any(amendment.values()):
        amendment = None

    num = int(txt(root, "vote_number", "0"))
    return {
        "kind": "vote", "chamber": "senate",
        "id": f"s{congress}-{session}-{num:05d}",
        "roll": num, "congress": txt(root, "congress") or str(congress),
        "session": txt(root, "session") or str(session),
        "date": date, "time": clock,
        "question": txt(root, "question"), "result": txt(root, "vote_result"),
        "result_text": txt(root, "vote_result_text"),
        "majority_requirement": txt(root, "majority_requirement"),
        "title": txt(root, "vote_title"),
        "document_text": txt(root, "vote_document_text"),
        "document": document,
        "is_nomination": bool(document and document.get("type_slug") == "pn"),
        "amendment": amendment,
        "bill_ref": senate_bill_reference(document, amendment),
        "tie_breaker": txt(root, "tie_breaker/by_whom"),
        "party_totals": party_totals, "totals": totals, "members": members,
    }


def fetch_senate_votes(since, until, congress, jobs):
    rows = []
    for year in sorted({int(since[:4]), int(until[:4])}):
        session = session_for_year(congress, year)
        if session < 1 or session > 2:
            continue
        try:
            raw = get(SEN_MENU.format(congress=congress, session=session))
        except Exception as exc:  # noqa: BLE001
            print(f"  votes {year}: menu unavailable ({exc})", file=sys.stderr)
            continue
        found = parse_senate_menu(raw)
        keep = [r for r in found if since <= r["date"] <= until]
        print(f"  votes {year} (session {session}): {len(found)} listed, "
              f"{len(keep)} in window", file=sys.stderr)
        for r in keep:
            r["session"] = session
        rows.extend(keep)

    def one(r):
        try:
            return parse_senate_vote(get(SEN_VOTE.format(congress=congress,
                                                         session=r["session"],
                                                         num=r["number"])),
                                     congress, r["session"])
        except Exception as exc:  # noqa: BLE001
            print(f"    vote {r['number']}: {exc}", file=sys.stderr)
            return None

    if not rows:
        return []
    with concurrent.futures.ThreadPoolExecutor(max_workers=jobs) as pool:
        return [v for v in pool.map(one, rows) if v]


# ------------------------------------------------------------------------- main

def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--chamber", required=True, choices=sorted(CHAMBERS))
    ap.add_argument("--since", required=True, help="inclusive YYYY-MM-DD")
    ap.add_argument("--until", help="inclusive YYYY-MM-DD (default: today)")
    ap.add_argument("--source", default="bills,votes",
                    help="comma list: bills, votes (default both)")
    ap.add_argument("--bill-type", action="append", help="restrict bill types")
    ap.add_argument("--congress", type=int, help="congress number (default: derived)")
    ap.add_argument("--jobs", type=int, default=8)
    ap.add_argument("--limit", type=int, help="cap candidate bills (testing)")
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    until = args.until or datetime.date.today().isoformat()
    sources = {s.strip() for s in args.source.split(",") if s.strip()}
    congress = args.congress or current_congress(
        datetime.datetime.strptime(args.since, "%Y-%m-%d"))
    bill_types = args.bill_type or CHAMBERS[args.chamber]

    os.makedirs(args.out, exist_ok=True)
    print(f"{args.chamber}: window {args.since} .. {until}  congress {congress}  "
          f"sources {sorted(sources)}", file=sys.stderr)

    items = []
    if "bills" in sources:
        print("bills:", file=sys.stderr)
        items += fetch_bills(args.since, until, congress, bill_types, args.jobs,
                             args.limit)
    if "votes" in sources:
        print("votes:", file=sys.stderr)
        fetcher = (fetch_house_votes if args.chamber == "house"
                   else fetch_senate_votes)
        items += fetcher(args.since, until, congress, args.jobs)

    for i in items:
        i.setdefault("chamber", args.chamber)
    items.sort(key=lambda i: (i.get("date") or "", i.get("kind"), str(i.get("id"))))

    manifest = {"since": args.since, "until": until, "congress": congress,
                "chamber": args.chamber, "sources": sorted(sources),
                "fetched_count": len(items), "items": items}
    path = os.path.join(args.out, "manifest.json")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, indent=2, ensure_ascii=False)

    print(f"\n{len(items)} items -> {path}", file=sys.stderr)
    kinds = {}
    for i in items:
        k = i["kind"]
        if k == "vote" and i.get("is_nomination"):
            k = "vote (nomination)"
        kinds[k] = kinds.get(k, 0) + 1
    for k, v in sorted(kinds.items()):
        print(f"  {v:5d}  {k}", file=sys.stderr)
    if not items:
        print(f"  (empty window — the {args.chamber} may have been in recess)",
              file=sys.stderr)


if __name__ == "__main__":
    main()
