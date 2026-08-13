#!/usr/bin/env python3
"""Stage 3 for both congressional chambers: manifest items -> note bodies.

Shared because bill notes are identical between chambers and both chambers' votes
resolve their bill through the same `bill_ref`. Only the vote header differs, so
that is the only part that branches.

Everything expensive lives here on purpose: bill-text download, bill-XML→markdown
conversion, and vote->bill resolution all happen AFTER --keep is applied, so an
item dropped by the relevance pass costs zero requests.

Usage:
  python3 congress_convert.py --manifest DIR/manifest.json --out DIR/notes \
      [--keep keep.txt] [--jobs 6]
"""
import argparse
import concurrent.futures
import html
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from billxml2md import bill_xml_to_markdown  # noqa: E402
from congress_fetch import lookup_bill  # noqa: E402

UA = {"User-Agent": "sync-us-government-feeds (personal note archive)"}
HOUSE_VOTE_PAGE = "https://clerk.house.gov/Votes/{year}{roll}"
SENATE_VOTE_PAGE = ("https://www.senate.gov/legislative/LIS/roll_call_votes/"
                    "vote{congress}{session}/vote_{congress}_{session}_{num:05d}.htm")
VOTE_ORDER = ["Yea", "Aye", "Nay", "No", "Present", "Not Voting",
              "Guilty", "Not Guilty"]

PRETTY = {"HR": "H.R.", "HRES": "H.Res.", "HJRES": "H.J.Res.",
          "HCONRES": "H.Con.Res.", "S": "S.", "SRES": "S.Res.",
          "SJRES": "S.J.Res.", "SCONRES": "S.Con.Res."}


def get(url, retries=4):
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers=UA)
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
    return s[:maxlen].rstrip("-") or "item"


def bill_label(item):
    return f"{PRETTY.get(item['bill_type'], item['bill_type'])} {item['number']}"


def clean_summary(text):
    out = re.sub(r"<[^>]+>", " ", text)
    out = html.unescape(out).replace("\xa0", " ")
    return re.sub(r"\s+", " ", out).strip()


def bill_meta_lines(bill):
    """Metadata lines shared by bill notes and the 'About' block on vote notes."""
    head, when = [], []
    if bill.get("introduced_date"):
        when.append(f"Introduced **{bill['introduced_date']}**")
    la = bill.get("latest_action") or {}
    if la.get("date"):
        when.append(f"Latest action **{la['date']}**")
    if when:
        head.append(" · ".join(when))
    if bill.get("sponsors"):
        n = bill.get("cosponsors_count") or 0
        head.append("Sponsor: " + ", ".join(bill["sponsors"])
                    + (f" · {n} cosponsor{'s' if n != 1 else ''}" if n else ""))
    if bill.get("committees"):
        head.append("Committees: " + ", ".join(bill["committees"]))
    meta = []
    if bill.get("policy_area"):
        meta.append(f"Policy area: {bill['policy_area']}")
    if bill.get("subjects"):
        subs = bill["subjects"]
        meta.append("Subjects: " + ", ".join(subs[:8])
                    + (f" (+{len(subs) - 8} more)" if len(subs) > 8 else ""))
    if meta:
        head.append(" · ".join(meta))
    if bill.get("legislation_url"):
        head.append(f"[Congress.gov]({bill['legislation_url']})")
    return head


def bill_summary_lines(bill):
    summary = bill.get("summary") or {}
    if not summary.get("text"):
        return []
    return ["", f"> **CRS summary** ({summary.get('desc') or summary.get('date')})",
            "> " + clean_summary(summary["text"])]


# -------------------------------------------------------------------- bill note

def render_bill(item):
    label = bill_label(item)
    lines = [f"# {label} — {item.get('title') or label}", ""]
    head = [f"**{label}** · **{item.get('congress')}th Congress**"]
    lines.append("  \n".join(head + bill_meta_lines(item)))
    lines += bill_summary_lines(item)

    win = item.get("window_actions") or []
    if win:
        lines += ["", "## Activity in this window", ""]
        lines += [f"- **{a['date']}** — {a['text']}" for a in win]
    acts = item.get("actions") or []
    if acts:
        lines += ["", f"## Full action history ({len(acts)})", ""]
        lines += [f"- {a['date']} — {a['text']}" for a in acts]

    body, warning = "", None
    url = item.get("text_url")
    if url:
        try:
            body, _ = bill_xml_to_markdown(get(url))
        except Exception as exc:  # noqa: BLE001
            warning = f"bill text unavailable: {exc}"
    else:
        warning = "no text version published yet"

    versions = item.get("text_versions") or []
    if versions:
        latest = versions[-1]
        lines += ["", f"*Text shown: {latest.get('type')} "
                      f"({(latest.get('date') or '')[:10]})*"]
    lines += ["", "---", "", body if body else "*(no bill text available)*"]
    if warning:
        lines += ["", f"*[{warning}]*"]
    return "\n".join(lines), warning


# -------------------------------------------------------------------- vote note

def vote_header(item):
    """Chamber-specific header lines."""
    roll = item["roll"]
    head = [f"**Roll Call {roll}** · **{item.get('result') or '?'}**"]
    stamp = item.get("date") or ""
    if item.get("time"):
        stamp += f" {item['time']}"
    if item["chamber"] == "house":
        head.append(f"{stamp} · {item.get('congress')}th Congress, "
                    f"{item.get('session')} Session")
        if item.get("question"):
            head.append(f"Question: {item['question']}")
        if item.get("vote_type"):
            head.append(f"Vote type: {item['vote_type']}")
        head.append("[Clerk roll call]("
                    + HOUSE_VOTE_PAGE.format(year=(item.get("date") or "")[:4],
                                             roll=roll) + ")")
    else:
        head.append(f"{stamp} · {item.get('congress')}th Congress, "
                    f"session {item.get('session')}")
        if item.get("question"):
            head.append(f"Question: {item['question']}")
        if item.get("majority_requirement"):
            head.append(f"Majority required: {item['majority_requirement']}")
        if item.get("tie_breaker"):
            head.append(f"Tie broken by: {item['tie_breaker']}")
        head.append("[Senate.gov roll call]("
                    + SENATE_VOTE_PAGE.format(congress=item["congress"],
                                              session=item["session"],
                                              num=roll) + ")")
    return head


def render_vote(item):
    chamber = item["chamber"]
    subject = item.get("legis_num") if chamber == "house" else None
    title = item.get("title") or item.get("question") or ""
    heading = (f"# {'Senate Vote' if chamber == 'senate' else 'Roll Call'} "
               f"{item['roll']}")
    if chamber == "house" and subject:
        heading += f" — {subject}"
    elif chamber == "senate":
        heading += f" — {title}"
    lines = [heading, ""]
    if chamber == "house" and title:
        lines += [f"**{title}**", ""]

    lines.append("  \n".join(vote_header(item)))
    if item.get("result_text"):
        lines += ["", f"*{item['result_text']}*"]

    doc = item.get("document") or {}
    amdt = item.get("amendment") or {}
    if item.get("is_nomination"):
        lines += ["", "## Nomination", "",
                  f"**{doc.get('name') or ''}** — {doc.get('title') or ''}".strip(" —")]
    elif item.get("document_text"):
        # On amendment votes document_text repeats the amendment purpose verbatim.
        if (item["document_text"] or "").strip() != (amdt.get("purpose") or "").strip():
            lines += ["", f"> {item['document_text']}"]
    if amdt.get("number"):
        bits = [f"**{amdt['number']}**"]
        if amdt.get("to_document"):
            bits.append(f"to {amdt['to_document']}")
        lines += ["", "## Amendment", "", " ".join(bits)]
        if amdt.get("purpose"):
            lines += ["", f"*Purpose:* {amdt['purpose']}"]

    parties = item.get("party_totals") or []
    if parties:
        lines += ["", "| Party | Yea | Nay | Present | Not voting |",
                  "| --- | --- | --- | --- | --- |"]
        for p in parties:
            lines.append(f"| {p.get('party')} | {p.get('yea')} | {p.get('nay')} | "
                         f"{p.get('present')} | {p.get('not_voting')} |")
        t = item.get("totals") or {}
        if t:
            lines.append(f"| **Total** | **{t.get('yea')}** | **{t.get('nay')}** | "
                         f"**{t.get('present') or 0}** | **{t.get('not_voting')}** |")

    members = item.get("members") or []
    if members:
        buckets = {}
        for m in members:
            buckets.setdefault(m.get("vote") or "?", []).append(m)
        lines += ["", "## Member votes", ""]
        ordered = [v for v in VOTE_ORDER if v in buckets]
        ordered += [v for v in sorted(buckets) if v not in ordered]
        for v in ordered:
            if chamber == "house":
                names = ", ".join(f"{m['name']} ({m.get('party')}-{m.get('state')})"
                                  for m in sorted(buckets[v],
                                                  key=lambda m: m["name"] or ""))
            else:
                names = ", ".join(m["name"] for m in
                                  sorted(buckets[v], key=lambda m: m["name"] or ""))
            lines += [f"**{v} ({len(buckets[v])})**", "", names, ""]

    lines += render_vote_bill(item)
    return "\n".join(lines), None


def render_vote_bill(item):
    if item.get("is_nomination"):
        return ["", "---", "",
                "*Nomination vote — no bill record. The nominee is shown above.*"]
    bill = item.get("bill")
    if not bill:
        ref = item.get("bill_ref") or {}
        which = ref.get("name") or item.get("legis_num")
        return ["", "---", "",
                f"*No bill record resolved{f' for `{which}`' if which else ''} — "
                "procedural votes and non-legislative measures have none.*"]
    via = (item.get("bill_ref") or {}).get("via")
    note = " (via the amendment this vote concerned)" if via == "amendment" else ""
    out = ["", "---", "", f"## About {bill_label(bill)}{note}", ""]
    if bill.get("title"):
        out += [f"**{bill['title']}**", ""]
    out.append("  \n".join(bill_meta_lines(bill)))
    out += bill_summary_lines(bill)
    la = bill.get("latest_action") or {}
    if la.get("text"):
        out += ["", f"**Latest action.** {la['text']}"]
    acts = bill.get("actions") or []
    if acts:
        out += ["", f"### Action history ({len(acts)})", ""]
        out += [f"- {a['date']} — {a['text']}" for a in acts]
    return out


# ------------------------------------------------------------------------- main

def build(item, outdir):
    if item["kind"] == "vote" and "bill" not in item:
        ref = item.get("bill_ref")
        item["bill"] = (lookup_bill(item.get("congress"), ref["type_slug"],
                                    ref["number"]) if ref else None)

    if item["kind"] == "bill":
        md, warning = render_bill(item)
        note_title = (f"{bill_label(item)} — {item.get('title') or ''} "
                      f"({item['date']})").strip()
        fname = f"{item['date']}-{item['id']}-{slug(item.get('title'))}.md"
    else:
        md, warning = render_vote(item)
        if item["chamber"] == "house":
            note_title = (f"Roll Call {item['roll']} — "
                          f"{item.get('legis_num') or ''}: "
                          f"{item.get('title') or ''}").strip().rstrip(":")
            fname = (f"{item['date']}-hvote{item['roll']:03d}-"
                     f"{slug(item.get('title'))}.md")
        else:
            note_title = f"Senate Vote {item['roll']} — {item.get('title') or ''}".strip()
            fname = (f"{item['date']}-svote{item['roll']:03d}-"
                     f"{slug(item.get('title'))}.md")

    path = os.path.join(outdir, fname)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(md)
    return {"kind": item["kind"], "chamber": item.get("chamber"), "id": item["id"],
            "note_title": note_title, "date": item["date"],
            "file": os.path.abspath(path), "bytes": len(md), "warning": warning}


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--manifest", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--keep", help="file of item ids to keep, one per line")
    ap.add_argument("--jobs", type=int, default=6)
    args = ap.parse_args()

    with open(args.manifest, encoding="utf-8") as fh:
        items = json.load(fh)["items"]

    if args.keep:
        with open(args.keep, encoding="utf-8") as fh:
            keep = {ln.strip() for ln in fh if ln.strip() and not ln.startswith("#")}
        missing = keep - {i["id"] for i in items}
        if missing:
            print(f"WARNING: {len(missing)} keep ids not in manifest: "
                  f"{sorted(missing)[:5]}", file=sys.stderr)
        items = [i for i in items if i["id"] in keep]

    os.makedirs(args.out, exist_ok=True)
    if not items:
        with open(os.path.join(args.out, "index.json"), "w") as fh:
            json.dump([], fh)
        print("nothing to convert", file=sys.stderr)
        return

    print(f"converting {len(items)} items with {args.jobs} workers…", file=sys.stderr)
    index = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.jobs) as pool:
        futures = [pool.submit(build, i, args.out) for i in items]
        for n, fut in enumerate(concurrent.futures.as_completed(futures), 1):
            e = fut.result()
            index.append(e)
            flag = f"  [{e['warning']}]" if e.get("warning") else ""
            print(f"  {n}/{len(items)} {e['kind']} {e['id']} ({e['bytes']:,}b){flag}",
                  file=sys.stderr)

    index.sort(key=lambda e: (e["date"], e["kind"], str(e["id"])))
    with open(os.path.join(args.out, "index.json"), "w", encoding="utf-8") as fh:
        json.dump(index, fh, indent=2, ensure_ascii=False)

    warned = [e for e in index if e.get("warning")]
    print(f"\n{len(index)} notes -> {args.out}", file=sys.stderr)
    if warned:
        print(f"{len(warned)} with warnings:", file=sys.stderr)
        for e in warned[:10]:
            print(f"  {e['id']}: {e['warning']}", file=sys.stderr)


if __name__ == "__main__":
    main()
