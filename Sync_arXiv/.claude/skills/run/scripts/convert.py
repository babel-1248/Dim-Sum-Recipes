#!/usr/bin/env python3
"""Stage 2: turn kept arXiv manifest entries into one markdown note per paper.

Everything expensive lives here on purpose. The HTML download and the LaTeXML
conversion only ever run for papers that survived the relevance pass, so a
rejected paper costs nothing beyond the metadata already in the manifest.

Full text comes from arxiv.org/html/<id>, which arXiv renders with LaTeXML for
most modern submissions. Papers submitted as camera-ready PDF have no HTML, and
arXiv still answers those with HTTP 200 and a placeholder page — so the result
is validated by content, not status code, and falls back to an abstract-only
note with the reason recorded in the entry's `warning`.

Usage:
  python3 convert.py --manifest DIR/manifest.json --out DIR/notes \
      [--keep DIR/keep.txt] [--abstract-only] [--delay 15]
"""
import argparse
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from latexml2md import html_to_markdown  # noqa: E402

USER_AGENT = "arxiv-feed (personal note archive)"
MAX_AUTHORS = 12         # physics papers run to hundreds; the tail helps nobody

# https://arxiv.org/robots.txt grants `Allow: /html` to `User-agent: *` and in
# the same block sets `Crawl-delay: 15`. This host is the website, not the API,
# so the API's 3-second courtesy rate does not apply here — 15 does, and we are
# the generic agent. The file also opens with "Indiscriminate automated
# downloads from this site are not permitted".
#
# Downloads are therefore sequential with this gap between them, which is slow
# by design: 40 papers is about 10 minutes. Do not restore parallel workers.
# For bulk full text, arXiv points to its S3 bulk-data access instead.
CRAWL_DELAY = 15.0

# Ceiling on notes per run. The oldest survivors are taken and the remainder
# deferred, which is only safe because the caller then records the *last
# converted paper's date* as the window end rather than the date it asked for —
# see the resume line printed at the end of a capped run. Recording the
# requested end after a capped run would strand every deferred paper.
MAX_NOTES = 20


def get_bytes(url, tries=3):
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    for attempt in range(tries):
        try:
            with urllib.request.urlopen(req, timeout=90) as resp:
                return resp.read()
        except urllib.error.HTTPError as exc:
            # 403/429 from the website means the crawl-delay was breached.
            if exc.code not in (429, 500, 502, 503, 504) or attempt == tries - 1:
                raise
            wait = float(exc.headers.get("Retry-After") or 0) or CRAWL_DELAY * (attempt + 2)
            print(f"    HTTP {exc.code}; retry in {wait:.0f}s", file=sys.stderr)
            time.sleep(wait)
        except (urllib.error.URLError, TimeoutError):
            if attempt == tries - 1:
                raise
            time.sleep(2 * (attempt + 1))
    raise RuntimeError("unreachable")


# Authors paste raw LaTeX into the abstract field routinely, and the API hands
# it back verbatim. Only the markup with an unambiguous markdown equivalent is
# translated; anything else is left alone rather than guessed at.
LATEX_INLINE = [
    (re.compile(r"\\textbf\{([^{}]*)\}"), r"**\1**"),
    (re.compile(r"\\(?:textit|emph)\{([^{}]*)\}"), r"*\1*"),
    (re.compile(r"\\texttt\{([^{}]*)\}"), r"`\1`"),
    (re.compile(r"\\(?:text|mathrm)\{([^{}]*)\}"), r"\1"),
    (re.compile(r"\\%"), "%"),
    (re.compile(r"\\&"), "&"),
    (re.compile(r"\\_"), "_"),
    (re.compile(r"(?<!-)---(?!-)"), "—"),
    (re.compile(r"(?<!-)--(?!-)"), "–"),
    (re.compile(r"~"), " "),
]


def delatex(text):
    for pattern, repl in LATEX_INLINE:
        text = pattern.sub(repl, text)
    return text


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


def header(p):
    """Metadata block that opens every note."""
    lines = []

    ident = [f"arXiv:{p['arxiv_id']}"]
    if p.get("primary_category"):
        ident.append(p["primary_category"])
    lines.append("**" + "** · **".join(ident) + "**")

    when = [f"Submitted **{p.get('published_date', '?')}**"]
    if p.get("updated") and p["updated"][:10] != p.get("published_date"):
        when.append(f"Revised **{p['updated'][:10]}**")
    extra = [c for c in (p.get("categories") or [])
             if c != p.get("primary_category")]
    if extra:
        when.append("also " + ", ".join(extra))
    lines.append(" · ".join(when))

    au = author_line(p.get("authors"))
    if au:
        lines.append(f"Authors: {au}")

    pub = []
    if p.get("journal_ref"):
        pub.append(p["journal_ref"])
    if p.get("doi"):
        pub.append(f"doi:{p['doi']}")
    if pub:
        lines.append(" · ".join(pub))

    if p.get("comment"):
        lines.append(f"Comment: {p['comment']}")

    links = [f"[arXiv]({p['abs_url']})", f"[PDF]({p['pdf_url']})",
             f"[HTML]({p['html_url']})"]
    if p.get("doi_url"):
        links.append(f"[DOI]({p['doi_url']})")
    lines.append(" · ".join(links))

    block = "  \n".join(lines)
    if p.get("abstract"):
        block += "\n\n> " + delatex(p["abstract"].strip()).replace("\n", "\n> ")
    return block


def build(p, outdir, abstract_only=False):
    body, warning = "", None
    if abstract_only:
        warning = "abstract only (--abstract-only)"
    else:
        try:
            body, warns = html_to_markdown(get_bytes(p["html_url"]))
            if warns:
                warning = "; ".join(warns) + " — abstract only"
        except Exception as exc:  # noqa: BLE001 - one bad paper must not kill the run
            warning = f"full text unavailable: {exc}"

    md = f"# {p['title']}\n\n{header(p)}\n"
    if body.strip():
        md += f"\n---\n\n{body}\n"
    if warning:
        md += f"\n\n*[{warning}]*\n"

    fname = f"{p.get('published_date')}-{p['arxiv_id']}-{slug(p['title'])}.md"
    path = os.path.join(outdir, fname)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(md)

    return {
        "id": p["arxiv_id"],
        "arxiv_id": p["arxiv_id"],
        "note_title": p["title"],
        "title": p["title"],
        "published_date": p.get("published_date"),
        "primary_category": p.get("primary_category"),
        "categories": p.get("categories"),
        "doi": p.get("doi"),
        "url": p["abs_url"],
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
    ap.add_argument("--keep", help="file of arxiv_ids to keep, one per line "
                                   "(omit to convert everything in the manifest)")
    ap.add_argument("--abstract-only", action="store_true",
                    help="skip full-text download entirely")
    ap.add_argument("--delay", type=float, default=CRAWL_DELAY,
                    help=f"seconds between downloads (default {CRAWL_DELAY:.0f}, "
                         f"the Crawl-delay in arxiv.org/robots.txt); lowering it "
                         f"breaches arXiv's stated policy")
    ap.add_argument("--max", type=int, default=MAX_NOTES, dest="max_notes",
                    help=f"most papers to convert in one run (default {MAX_NOTES}); "
                         f"the oldest are taken and the rest deferred to the next run")
    args = ap.parse_args()

    with open(args.manifest, encoding="utf-8") as fh:
        papers = json.load(fh)["documents"]

    if args.keep:
        with open(args.keep, encoding="utf-8") as fh:
            keep = {ln.strip() for ln in fh if ln.strip() and not ln.startswith("#")}
        missing = keep - {p["arxiv_id"] for p in papers}
        if missing:
            print(f"WARNING: {len(missing)} keep-list ids not in manifest: "
                  f"{sorted(missing)[:5]}", file=sys.stderr)
        papers = [p for p in papers if p["arxiv_id"] in keep]

    # Manifests arrive in publication order, so the cap keeps the oldest and
    # defers the newest — a feed must not skip a day to show today's papers.
    deferred = []
    if args.max_notes and len(papers) > args.max_notes:
        deferred = papers[args.max_notes:]
        papers = papers[: args.max_notes]

    os.makedirs(args.out, exist_ok=True)
    if not papers:
        print("nothing to convert", file=sys.stderr)
        with open(os.path.join(args.out, "index.json"), "w") as fh:
            json.dump([], fh)
        return

    if args.delay < CRAWL_DELAY and not args.abstract_only:
        print(f"WARNING: --delay {args.delay:g}s is below the {CRAWL_DELAY:.0f}s "
              f"Crawl-delay arxiv.org asks of automated clients", file=sys.stderr)

    eta = "" if args.abstract_only else \
        f", about {max(0, len(papers) - 1) * args.delay / 60:.0f} min at {args.delay:g}s apart"
    print(f"converting {len(papers)} papers{eta}…", file=sys.stderr)

    index = []
    for i, p in enumerate(papers, 1):
        # Space the requests, not the parsing: the gap belongs between the
        # downloads themselves, and the first one need not wait.
        if i > 1 and not args.abstract_only:
            time.sleep(args.delay)
        entry = build(p, args.out, args.abstract_only)
        index.append(entry)
        flag = f"  [{entry['warning']}]" if entry.get("warning") else ""
        print(f"  {i}/{len(papers)} {entry['arxiv_id']} "
              f"({entry['bytes']:,}b){flag}", file=sys.stderr)

    index.sort(key=lambda e: (e["published_date"] or "", e["arxiv_id"]))
    path = os.path.join(args.out, "index.json")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(index, fh, indent=2, ensure_ascii=False)

    full = sum(1 for e in index if e["has_full_text"])
    print(f"\n{len(index)} notes -> {args.out}  ({full} with full text)", file=sys.stderr)
    print(f"index: {path}", file=sys.stderr)
    for e in [e for e in index if e.get("warning")]:
        print(f"  {e['arxiv_id']}: {e['warning']}", file=sys.stderr)

    if deferred:
        # The window end must be the last paper actually written, not the one
        # the caller asked for. seen_ids then suppresses the re-read duplicates
        # and the deferred papers arrive on the next run instead of vanishing.
        resume = index[-1]["published_date"]
        print(f"\nCAPPED at {args.max_notes}: {len(deferred)} paper(s) deferred "
              f"({deferred[0]['published_date']} .. {deferred[-1]['published_date']}).",
              file=sys.stderr)
        print(f"RECORD --until {resume} — NOT the requested window end, or the "
              f"deferred papers are stranded.", file=sys.stderr)


if __name__ == "__main__":
    main()
