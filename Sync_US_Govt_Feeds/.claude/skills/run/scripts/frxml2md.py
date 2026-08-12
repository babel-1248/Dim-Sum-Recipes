#!/usr/bin/env python3
"""Convert Federal Register full-text XML into clean markdown.

FR XML is GPO's own schema, not DocBook/TEI, so nothing off-the-shelf converts it
well. This maps the tags that actually appear in FR documents; anything unknown is
recursed into rather than dropped, so unfamiliar markup degrades to plain text
instead of vanishing.

Usage:  python3 frxml2md.py input.xml [-o output.md]
"""
import argparse
import re
import sys
import xml.etree.ElementTree as ET

# ---------------------------------------------------------------- inline markup

# <E T="nn"> emphasis types used by GPO.
EMPH = {
    "01": ("**", "**"),   # bold
    "02": ("**", "**"),   # bold
    "03": ("*", "*"),     # italic
    "04": ("*", "*"),     # italic (publication titles, e.g. Federal Register)
    "51": ("", ""),       # superscript
    "52": ("", ""),       # subscript
}

# Markdown has no portable sub/superscript, and FR uses them for things like the
# X in NO_X. Render them as plain text joined tightly to the surrounding words so
# we get "NOX" rather than "NO ~X —oxides".
TIGHT = {"51", "52"}

# Tags that carry no renderable content of their own.
DROP_INLINE = {"FTREF", "PRTPAGE", "SU"}


def _ws(s):
    return re.sub(r"\s+", " ", s or "")


def inline(el, footnotes=None):
    """Render an element's inline content to markdown."""
    out = []

    if el.tag == "SU":
        # Superscript. In body text it is a footnote reference; inside a footnote
        # definition it is the footnote's own number (handled by the caller).
        num = _ws("".join(el.itertext())).strip()
        if footnotes is not None and num:
            out.append(f"[^{num}]")
        elif num:
            out.append(f"^{num}")
        out.append(_ws(el.tail))
        return "".join(out)

    if el.tag in DROP_INLINE:
        return _ws(el.tail)

    out.append(_ws(el.text))
    for child in el:
        if child.tag == "E":
            t = child.get("T", "03")
            open_, close = EMPH.get(t, ("*", "*"))
            inner = inline(child, footnotes).strip()
            tail = _ws(child.tail)
            if t in TIGHT:
                # Join to the neighbouring words with no intervening space.
                if out:
                    out[-1] = out[-1].rstrip()
                tail = tail.lstrip()
            # Keep whitespace outside the emphasis markers or markdown breaks.
            out.append(f"{open_}{inner}{close}" if inner else "")
            out.append(tail)
        else:
            # inline() never emits an element's own tail — the parent owns it.
            out.append(inline(child, footnotes))
            if child.tag not in DROP_INLINE:
                out.append(_ws(child.tail))

    text = "".join(out)
    return text


# A short italic letter token: the stereochemistry and locant markers in chemical
# names (7*R*, 1*H*, *N*-) and single-letter legal italics.
_SHORT_ITAL = r"(\*[A-Za-z][A-Za-z,]{0,2}\*)"


def inline_text(el, footnotes=None):
    """Inline content, normalised and trimmed."""
    text = re.sub(r" +", " ", inline(el, footnotes))
    # Emphasis runs often end just before punctuation ("*Federal Register* ,").
    text = re.sub(r" +([,.;:)\]])", r"\1", text)
    # FR pretty-prints its XML, so a newline before <E> may be indentation rather
    # than a real space. That is only decidable from the surrounding characters:
    # tighten short italics against a preceding digit/bracket or a following
    # closer/hyphen, which covers chemical names without touching prose like
    # "the *Federal Register*".
    text = re.sub(r"(?<=[\d\[\(]) +" + _SHORT_ITAL, r"\1", text)
    text = re.sub(_SHORT_ITAL + r" +(?=[\)\]\-,;.])", r"\1", text)
    return text.strip()


# ----------------------------------------------------------------- block markup

HEADING = {"HED": 2, "HD1": 2, "HD2": 3, "HD3": 4, "HD4": 5, "HD5": 6}

# Containers we walk straight through.
TRANSPARENT = {
    "PREAMB", "SUPLINF", "AGY", "ACT", "SUM", "EFFDATE", "DATES", "ADD", "FURINF",
    "LSTSUB", "AUTH", "REGTEXT", "PART", "SUBPART", "SECTION", "APPENDIX",
    "EOP", "PRESDOCU", "SUPLINF", "SIG", "FTNT", "NOTE", "IMPORTANT",
}

# Metadata we surface as a small header rather than body prose.
SKIP = {"BILCOD", "FRDOC", "DEPDOC", "AGENCY", "CFR", "SUBJECT", "PRTPAGE", "FTREF"}


def fp_indent(source):
    """FP SOURCE like 'FP-1', 'FP1-2', 'FP2-2' encodes an indent level."""
    m = re.match(r"FP(\d*)", source or "")
    if m and m.group(1):
        return int(m.group(1))
    return 0


def render_table(el, footnotes):
    """<GPOTABLE> -> markdown table. Caption from <TTITLE>."""
    lines = []
    title = el.find("TTITLE")
    if title is not None:
        cap = inline_text(title, footnotes)
        if cap:
            lines.append(f"**{cap}**")
            lines.append("")

    def cells(row, tag):
        return [inline_text(c, footnotes).replace("|", "\\|") for c in row.findall(tag)]

    header = []
    boxhd = el.find("BOXHD")
    if boxhd is not None:
        # CHED elements carry an H level; the deepest level is the real header row.
        cheds = boxhd.findall("CHED")
        if cheds:
            deepest = max(int(c.get("H", "1")) for c in cheds)
            header = [
                inline_text(c, footnotes).replace("|", "\\|")
                for c in cheds
                if int(c.get("H", "1")) == deepest
            ]

    rows = [cells(r, "ENT") for r in el.findall("ROW")]
    rows = [r for r in rows if any(c for c in r)]
    if not rows and not header:
        return []

    width = max([len(header)] + [len(r) for r in rows]) if (header or rows) else 0
    if not width:
        return []
    if not header:
        header = [""] * width
    header += [""] * (width - len(header))

    lines.append("| " + " | ".join(header) + " |")
    lines.append("|" + "|".join([" --- "] * width) + "|")
    for r in rows:
        r = r + [""] * (width - len(r))
        lines.append("| " + " | ".join(r) + " |")
    lines.append("")
    return lines


def render(el, footnotes, out, depth=0):
    """Walk an element, appending markdown blocks to `out`."""
    tag = el.tag

    if tag in SKIP:
        return

    if tag == "FTNT":
        # Footnote definition: first <SU> is its number, the rest is the text.
        p = el.find("P")
        target = p if p is not None else el
        su = target.find("SU")
        num = None
        if su is not None:
            num = _ws("".join(su.itertext())).strip()
            # Drop the leading SU so it is not rendered as a reference to itself.
            # Removing an element also drops its tail, so fold that back first.
            target.text = (target.text or "") + (su.tail or "")
            target.remove(su)
        text = inline_text(target, footnotes)
        if num:
            footnotes[num] = text
        elif text:
            footnotes[f"_{len(footnotes) + 1}"] = text
        return

    if tag == "GPOTABLE":
        out.extend(render_table(el, footnotes))
        return

    if tag == "HD":
        level = HEADING.get(el.get("SOURCE", "HD1"), 3)
        text = inline_text(el, footnotes).rstrip(":")
        if text:
            out.append(f"{'#' * level} {text}")
            out.append("")
        return

    if tag in ("P", "FP"):
        text = inline_text(el, footnotes)
        if not text:
            return
        indent = "  " * fp_indent(el.get("SOURCE", "")) if tag == "FP" else ""
        # FR uses a literal bullet for list items; make it a real markdown list.
        if text.startswith("•"):
            out.append(f"{indent}- {text.lstrip('• ').strip()}")
        else:
            out.append(f"{indent}{text}")
        out.append("")
        return

    if tag == "STARS":
        out.append("\\* \\* \\* \\* \\*")
        out.append("")
        return

    if tag in ("SECTNO", "AMDPAR", "DATED", "NAME", "TITLE", "PLACE", "SIGDATE"):
        text = inline_text(el, footnotes)
        if text:
            out.append(text)
            out.append("")
        return

    if tag == "EXTRACT":
        # Indented block: abbreviation lists, tables of contents.
        for child in el:
            render(child, footnotes, out, depth + 1)
        return

    # Unknown or transparent container: recurse so nothing is silently lost.
    if el.text and el.text.strip() and not len(el):
        out.append(_ws(el.text).strip())
        out.append("")
    for child in el:
        render(child, footnotes, out, depth + 1)
    if el.tail and el.tail.strip() and depth > 0:
        pass


def xml_to_markdown(xml_bytes):
    """Return (markdown, meta) for one FR document XML."""
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError as exc:
        raise ValueError(f"unparseable FR XML: {exc}") from exc

    meta = {}
    for tag, key in (
        ("AGENCY", "agency"),
        ("CFR", "cfr"),
        ("DEPDOC", "docket"),
        ("SUBJECT", "subject"),
        ("FRDOC", "fr_doc"),
    ):
        el = root.find(f".//{tag}")
        if el is not None:
            val = inline_text(el)
            if val:
                meta[key] = val

    footnotes = {}
    out = []
    render(root, footnotes, out)

    # Collapse runs of blank lines.
    body = "\n".join(out)
    body = re.sub(r"\n{3,}", "\n\n", body).strip()

    if footnotes:
        body += "\n\n---\n\n"
        for num, text in footnotes.items():
            label = num if not num.startswith("_") else num[1:]
            body += f"[^{label}]: {text}\n"

    return body, meta


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("input")
    ap.add_argument("-o", "--output")
    args = ap.parse_args()

    with open(args.input, "rb") as fh:
        md, meta = xml_to_markdown(fh.read())

    if args.output:
        with open(args.output, "w", encoding="utf-8") as fh:
            fh.write(md)
        print(f"wrote {args.output} ({len(md)} chars)", file=sys.stderr)
        print(f"meta: {meta}", file=sys.stderr)
    else:
        sys.stdout.write(md)


if __name__ == "__main__":
    main()
