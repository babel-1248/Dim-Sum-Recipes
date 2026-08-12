#!/usr/bin/env python3
"""Convert a govinfo bill-text XML document into markdown.

This is the House analogue of the Federal Register converter, but a different
schema entirely: bills use <form> for the caption block and <legis-body> holding
a section/subsection/paragraph/subparagraph hierarchy, each level carrying an
<enum> ("(a)"), an optional <header>, and <text>.

Unknown elements are recursed into rather than dropped, so schema variation
degrades to plain text instead of vanishing.

Usage:  python3 billxml2md.py input.xml [-o output.md]
"""
import argparse
import re
import sys
import xml.etree.ElementTree as ET

# Hierarchy levels below <section>, rendered as an indented outline.
LEVELS = [
    "subsection", "paragraph", "subparagraph", "clause", "subclause",
    "item", "subitem",
]

INLINE_EMPH = {
    "italic": ("*", "*"),
    "term": ("*", "*"),
    "short-title": ("*", "*"),
}


def _ws(s):
    return re.sub(r"\s+", " ", s or "")


def inline(el):
    """Render inline content, preserving element tails."""
    out = [_ws(el.text)]
    for child in el:
        tag = child.tag.split("}")[-1]
        if tag == "quote":
            out.append("“" + inline(child).strip() + "”")
        elif tag in INLINE_EMPH:
            open_, close = INLINE_EMPH[tag]
            inner = inline(child).strip()
            out.append(f"{open_}{inner}{close}" if inner else "")
        else:
            # external-xref, internal-xref and friends: keep the text.
            out.append(inline(child))
        out.append(_ws(child.tail))
    return "".join(out)


def inline_text(el):
    text = re.sub(r" +", " ", inline(el))
    text = re.sub(r" +([,.;:)\]])", r"\1", text)
    return text.strip()


def local(el):
    return el.tag.split("}")[-1]


def render_form(form):
    """The caption block: congress, session, bill number, sponsor action."""
    if form is None:
        return []
    lines, bits = [], []
    for tag in ("congress", "session"):
        el = form.find(tag)
        if el is not None:
            bits.append(inline_text(el))
    if bits:
        lines.append("*" + ", ".join(b for b in bits if b) + "*")
    chamber = form.find("current-chamber")
    if chamber is not None and inline_text(chamber):
        lines.append(inline_text(chamber))
    # <action> holds <action-date> and <action-desc> as siblings with no
    # separating whitespace, so they must be joined explicitly.
    action = form.find("action")
    if action is not None:
        parts = [inline_text(c) for c in action] or [inline_text(action)]
        for part in parts:
            if part:
                lines.append(part)
    title = form.find("official-title")
    if title is not None:
        txt = inline_text(title)
        if txt:
            lines.append(f"**{txt}**")
    return [ln for ln in lines if ln] + [""] if lines else []


def render_level(el, out, depth=0):
    """Render a section or any nested level as an indented outline."""
    tag = local(el)

    if tag == "quoted-block":
        # Restart the outline inside the quote. Carrying the outer depth in would
        # push nested bullets past four spaces, which markdown reads as a code block.
        inner = []
        for child in el:
            render_level(child, inner, 0)
        body = "\n".join(inner).strip()
        if body:
            out.extend("> " + ln if ln else ">" for ln in body.split("\n"))
            out.append("")
        return

    enum = el.find("enum")
    header = el.find("header")
    enum_s = inline_text(enum) if enum is not None else ""
    head_s = inline_text(header) if header is not None else ""

    # A section becomes a heading; everything below it becomes a list item.
    if tag == "section":
        num = enum_s if enum_s.endswith(".") else (enum_s + "." if enum_s else "")
        label = " ".join(p for p in [num, head_s] if p)
        if label:
            out.append(f"### {label}")
            out.append("")
    elif enum_s or head_s:
        indent = "  " * max(0, depth - 1)
        label = f"**{enum_s}**" if enum_s else ""
        if head_s:
            label = f"{label} {head_s}".strip()
        out.append(f"{indent}- {label}".rstrip())

    for child in el:
        ctag = local(child)
        if ctag in ("enum", "header"):
            continue
        if ctag == "text":
            txt = inline_text(child)
            if not txt:
                continue
            if tag == "section":
                out.append(txt)
                out.append("")
            else:
                indent = "  " * max(0, depth - 1)
                # Attach the text to the bullet just opened, when there is one.
                if out and out[-1].lstrip().startswith("-") and out[-1].strip() != "-":
                    out[-1] = out[-1] + " " + txt
                else:
                    out.append(f"{indent}  {txt}")
        elif ctag in LEVELS or ctag in ("section", "quoted-block"):
            render_level(child, out, depth + 1)
        else:
            render_level(child, out, depth)


def bill_xml_to_markdown(xml_bytes):
    """Return (markdown, meta) for one govinfo bill-text XML."""
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError as exc:
        raise ValueError(f"unparseable bill XML: {exc}") from exc

    meta = {"stage": root.get("bill-stage")}
    form = root.find("form")
    if form is not None:
        for tag, key in (("legis-num", "legis_num"), ("official-title", "official_title"),
                         ("congress", "congress"), ("session", "session")):
            el = form.find(tag)
            if el is not None:
                meta[key] = inline_text(el)

    out = render_form(form)
    body = root.find("legis-body")
    if body is not None:
        for child in body:
            render_level(child, out, 0)
    else:
        # Resolutions sometimes carry their text in resolution-body.
        for alt in ("resolution-body", "engrossed-amendment-body"):
            b = root.find(alt)
            if b is not None:
                for child in b:
                    render_level(child, out, 0)
                break

    md = re.sub(r"\n{3,}", "\n\n", "\n".join(out)).strip()
    return md, meta


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("input")
    ap.add_argument("-o", "--output")
    args = ap.parse_args()
    with open(args.input, "rb") as fh:
        md, meta = bill_xml_to_markdown(fh.read())
    if args.output:
        with open(args.output, "w", encoding="utf-8") as fh:
            fh.write(md)
        print(f"wrote {args.output} ({len(md)} chars); meta={meta}", file=sys.stderr)
    else:
        sys.stdout.write(md)


if __name__ == "__main__":
    main()
