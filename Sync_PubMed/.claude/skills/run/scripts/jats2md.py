#!/usr/bin/env python3
"""Convert a PubMed Central JATS article (efetch db=pmc) into markdown.

JATS is a different schema again from the Federal Register's GPO XML or a bill's
legis-body: articles are <front> metadata, a <body> of arbitrarily nested <sec>
elements, and a <back> holding references and acknowledgements. Section depth,
not element name, decides heading level.

Only <body> and the reference list are converted. <front> is skipped because the
caller already holds richer, more consistent metadata from the PubMed record —
converting it here would restate every article's title and authors twice.

Math is taken from <tex-math> when present; MathML alone is skipped rather than
flattened, because rendering its tokens as text produces noise that reads as
corruption.

Unknown elements are recursed into rather than dropped, so schema variation
degrades to plain text instead of vanishing.

Usage:  python3 jats2md.py article.xml [-o out.md]
"""
import argparse
import re
import sys
import xml.etree.ElementTree as ET

XLINK = "{http://www.w3.org/1999/xlink}href"

INLINE_EMPH = {
    "italic": ("*", "*"),
    "bold": ("**", "**"),
    "underline": ("", ""),
    "sc": ("", ""),
    "monospace": ("`", "`"),
    "sub": ("~", "~"),
    "sup": ("^", "^"),
}

# Front matter, floating apparatus and machine-readable cruft. These are either
# already in the manifest or meaningless outside the original layout.
SKIP = {
    "front", "front-stub", "processing-meta", "journal-meta", "article-meta",
    "permissions", "author-notes", "history", "custom-meta-group",
    "supplementary-material", "table-wrap-foot", "fn-group", "glossary",
    "object-id", "graphic", "inline-graphic", "media", "alternatives",
}


def local(el):
    return el.tag.split("}")[-1]


def _ws(s):
    return re.sub(r"\s+", " ", s or "")


def inline(el):
    """Render inline content, preserving element tails."""
    out = [_ws(el.text)]
    for child in el:
        tag = local(child)
        if tag in SKIP:
            out.append(_ws(child.tail))
            continue
        if tag in INLINE_EMPH:
            open_, close = INLINE_EMPH[tag]
            inner = inline(child).strip()
            out.append(f"{open_}{inner}{close}" if inner else "")
        elif tag == "ext-link":
            # xlink:href is often a bare database accession ("ON756181") rather
            # than a URL; linking those produces dead relative links.
            href = (child.get(XLINK) or "").strip()
            text = inline(child).strip() or href
            out.append(f"[{text}]({href})"
                       if href.startswith(("http://", "https://", "ftp://"))
                       else text)
        elif tag == "inline-formula":
            out.append(formula(child, block=False))
        elif tag == "math":
            pass                       # MathML with no tex-math sibling
        else:
            # xref, named-content, styled-content and friends: keep the text.
            out.append(inline(child))
        out.append(_ws(child.tail))
    return "".join(out)


def inline_text(el):
    text = re.sub(r" +", " ", inline(el))
    text = re.sub(r" +([,.;:)\]])", r"\1", text)
    return text.strip()


def formula(el, block=True):
    tex = el.find(".//tex-math")
    if tex is None or not (tex.text or "").strip():
        return ""
    body = _ws(tex.text).strip().strip("$")
    return f"\n\n$${body}$$\n\n" if block else f"${body}$"


def table(el):
    """<table-wrap> or a bare <table> -> a markdown table."""
    node = el.find(".//table")
    if node is None:
        return []
    rows = []
    for tr in node.iter():
        if local(tr) != "tr":
            continue
        cells = [inline_text(td).replace("|", "\\|")
                 for td in tr if local(td) in ("td", "th")]
        if any(cells):
            rows.append(cells)
    if not rows:
        return []
    width = max(len(r) for r in rows)
    rows = [r + [""] * (width - len(r)) for r in rows]
    head, body = rows[0], rows[1:]
    if not body:
        head, body = [""] * width, rows
    lines = ["| " + " | ".join(head) + " |",
             "| " + " | ".join(["---"] * width) + " |"]
    lines += ["| " + " | ".join(r) + " |" for r in body]
    return lines


def caption(el, prefix=""):
    label = el.findtext("label") or ""
    cap = el.find("caption")
    text = inline_text(cap) if cap is not None else ""
    joined = " ".join(x for x in (label, text) if x).strip()
    return [f"*{prefix}{joined}*"] if joined else []


def render(el, depth=1):
    """Element -> list of markdown blocks."""
    tag = local(el)
    if tag in SKIP:
        return []

    if tag == "sec":
        out = []
        title = el.find("title")
        if title is not None:
            level = min(depth + 1, 6)      # body sections start at h2
            out.append("#" * level + " " + inline_text(title))
        for child in el:
            if local(child) == "title":
                continue
            out += render(child, depth + 1)
        return out

    if tag in ("p", "title"):
        text = inline_text(el)
        return [text] if text else []

    if tag == "list":
        ordered = el.get("list-type") in ("order", "arabic", "roman-lower")
        items = []
        for i, item in enumerate(el.findall("list-item"), 1):
            inner = [b for child in item for b in render(child, depth)]
            body = " ".join(b for b in inner if b).strip()
            if body:
                items.append(f"{i}. {body}" if ordered else f"- {body}")
        return ["\n".join(items)] if items else []

    if tag == "table-wrap":
        rows = table(el)
        return (caption(el) + (["\n".join(rows)] if rows else []))

    if tag == "fig":
        return caption(el)

    if tag == "disp-quote":
        inner = [b for child in el for b in render(child, depth)]
        text = " ".join(b for b in inner if b).strip()
        return ["> " + text.replace("\n", "\n> ")] if text else []

    if tag == "disp-formula":
        f = formula(el).strip()
        return [f] if f else []

    if tag in ("code", "preformat"):
        text = (el.text or "").rstrip()
        return [f"```\n{text}\n```"] if text.strip() else []

    if tag == "ref-list":
        out = []
        title = el.find("title")
        out.append("## " + (inline_text(title) if title is not None else "References"))
        for i, ref in enumerate(el.findall("ref"), 1):
            text = inline_text(ref)
            if text:
                out.append(f"{i}. {text}")
        return out if len(out) > 1 else []

    if tag in ("body", "back", "sec-meta", "boxed-text", "app", "app-group",
               "ack", "notes"):
        out = []
        title = el.find("title")
        if title is not None and tag in ("ack", "app", "notes"):
            out.append("## " + inline_text(title))
        for child in el:
            if child is title:
                continue
            out += render(child, depth if tag in ("body", "back") else depth + 1)
        return out

    # Unknown container: recurse rather than drop the subtree.
    if len(el):
        return [b for child in el for b in render(child, depth)]
    text = inline_text(el)
    return [text] if text else []


def jats_to_markdown(source):
    """(markdown, warnings) from JATS XML given as str or bytes."""
    warnings = []
    root = ET.fromstring(source)
    article = root if local(root) == "article" else root.find(".//article")
    if article is None:
        return "", ["no <article> element in the response"]
    body = article.find("body")
    if body is None:
        return "", ["no <body> in the JATS record (metadata-only PMC entry)"]

    blocks = render(body)
    back = article.find("back")
    if back is not None:
        blocks += render(back)

    md = "\n\n".join(b for b in blocks if b and b.strip())
    md = re.sub(r"\n{3,}", "\n\n", md).strip()
    if not md:
        warnings.append("JATS body converted to nothing")
    return md + "\n", warnings


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("input")
    ap.add_argument("-o", "--output")
    args = ap.parse_args()

    with open(args.input, "rb") as fh:
        md, warnings = jats_to_markdown(fh.read())
    for w in warnings:
        print(f"WARNING: {w}", file=sys.stderr)
    if args.output:
        with open(args.output, "w", encoding="utf-8") as fh:
            fh.write(md)
    else:
        sys.stdout.write(md)


if __name__ == "__main__":
    main()
