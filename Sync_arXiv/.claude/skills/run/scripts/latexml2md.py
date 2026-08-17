#!/usr/bin/env python3
"""Convert an arXiv LaTeXML HTML paper (arxiv.org/html/<id>) into markdown.

arXiv renders most papers to HTML with LaTeXML, which tags everything with
stable `ltx_*` classes — `ltx_section`, `ltx_para`, `ltx_p`, `ltx_tabular`,
`ltx_bibitem`. That regularity is what makes a small stdlib parser viable here;
this is not a general-purpose HTML-to-markdown converter and will not behave
like one on arbitrary pages.

Math is taken from each `<math>` element's `alttext` attribute, which holds the
original LaTeX. The MathML inside is skipped — rendering it as text produces
streams of loose symbols that read as corruption in a note.

The document title, author block and abstract are dropped on purpose: the
caller already has cleaner copies from the API manifest and puts them in the
note header, so keeping them here would duplicate every paper's opening.

Unknown elements are recursed into rather than dropped, so a LaTeXML change
degrades to plain text instead of losing the section.

Usage:  python3 latexml2md.py paper.html [-o out.md]
"""
import argparse
import html
import re
import sys
from html.parser import HTMLParser

# Elements with no end tag; they must not be pushed onto the tag stack.
VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link",
        "meta", "param", "source", "track", "wbr"}

# Whole subtrees that never belong in a note body. The HTML5 sectioning tags
# earn their place here: arXiv wraps its own chrome in <dialog> (a "Report
# GitHub Issue" modal near the top of <body>), <header> (logo and license line)
# and <footer> (an accessibility appeal after the bibliography). LaTeXML puts
# real paper content in <section> and <div> only, so none of these can hold it.
SKIP_TAGS = {"script", "style", "head", "nav", "svg", "button", "dialog",
             "form", "header", "footer", "aside"}
SKIP_CLASSES = (
    "ltx_page_navbar", "ltx_page_footer", "ltx_page_header", "ltx_TOC",
    "ltx_toclist", "ltx_title_document", "ltx_authors", "ltx_abstract",
    "ltx_dates", "ltx_role_institutetext", "ltx_role_affiliationtext",
    "package-alerts", "ltx_rdf", "infobox",
    # LaTeXML renders its own list markers ("1.", "•"); this parser emits
    # markdown ones, and keeping both yields "1. 1. text".
    "ltx_tag_item",
)

# arXiv's own site chrome (announcement banner, header, footer) is all classed
# with this prefix, and changes more often than the ltx_* vocabulary.
SKIP_CLASS_PREFIXES = ("ds-",)

EMPH_CLASSES = {
    "ltx_font_bold": "**",
    "ltx_font_italic": "*",
    "ltx_font_smallcaps": "",
    "ltx_emph": "*",
}


class LatexmlParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.out = []
        self.stack = []          # [{"tag":…, "cls":…, "close":…}]
        self.skip_depth = None   # stack depth at which a skipped subtree began
        self.pre = 0             # inside a code listing: preserve whitespace
        self.table = None        # list of rows, each a list of cell strings
        self.row = None
        self.cell = None         # active cell buffer
        self.warnings = []

    # ---- output plumbing -------------------------------------------------

    def emit(self, text):
        if self.cell is not None:
            self.cell.append(text)
        else:
            self.out.append(text)

    def _classes(self, attrs):
        return (dict(attrs).get("class") or "").split()

    # ---- tag handling ----------------------------------------------------

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        cls = self._classes(attrs)
        if tag not in VOID:
            self.stack.append({"tag": tag, "cls": cls, "close": ""})

        if self.skip_depth is not None:
            return
        if (tag in SKIP_TAGS
                or any(c in SKIP_CLASSES for c in cls)
                or any(c.startswith(SKIP_CLASS_PREFIXES) for c in cls)):
            self.skip_depth = len(self.stack)
            return

        # <math alttext="…"> — emit the LaTeX, skip the MathML underneath.
        if tag == "math":
            alt = a.get("alttext")
            if alt:
                alt = html.unescape(alt).strip()
                if a.get("display") == "block":
                    self.emit(f"\n\n$${alt}$$\n\n")
                else:
                    self.emit(f"${alt}$")
            self.skip_depth = len(self.stack)
            return

        if re.fullmatch(r"h[1-6]", tag):
            self.emit("\n\n" + "#" * int(tag[1]) + " ")
            self._set_close("\n\n")
            return

        if tag == "p":
            # LaTeXML wraps every list item's text in ltx_para/ltx_p. Treating
            # those as real paragraphs would split each item into its own
            # block and break the list apart.
            if self._nearest(("li",)):
                self._set_close(" ")
            else:
                self.emit("\n\n")
                self._set_close("\n\n")
            return

        if tag in ("ul", "ol"):
            self.emit("\n")
            return

        if tag == "li":
            parent = self._nearest(("ul", "ol"))
            marker = "1. " if parent == "ol" else "- "
            self.emit("\n" + marker)
            return

        if tag == "table":
            # Display equations are laid out as tables; the <math> inside
            # already emits $$…$$, so table machinery would only add noise.
            if "ltx_equation" not in cls and "ltx_eqn_table" not in cls:
                self.table = []
            return

        if tag == "tr" and self.table is not None:
            self.row = []
            return

        if tag in ("td", "th") and self.table is not None:
            self.cell = []
            return

        if tag == "figcaption":
            self.emit("\n\n*")
            self._set_close("*\n\n")
            return

        if tag == "br":
            self.emit("  \n")
            return

        if tag in ("pre",) or "ltx_listing" in cls or "ltx_verbatim" in cls:
            self.pre += 1
            self.emit("\n\n```\n")
            self._set_close("\n```\n\n", pre=True)
            return

        if tag == "a":
            href = a.get("href") or ""
            # Internal cross-references (#S2.E1) keep their text but lose the
            # link — the target does not exist outside the HTML.
            if href.startswith(("http://", "https://")):
                self.emit("[")
                self._set_close(f"]({href})")
            return

        if tag in ("b", "strong"):
            self.emit("**")
            self._set_close("**")
            return

        if tag in ("i", "em", "cite") and tag != "cite":
            self.emit("*")
            self._set_close("*")
            return

        for c in cls:
            if c in EMPH_CLASSES and EMPH_CLASSES[c]:
                self.emit(EMPH_CLASSES[c])
                self._set_close(EMPH_CLASSES[c])
                break

    def _set_close(self, text, pre=False):
        if self.stack:
            self.stack[-1]["close"] = text
            self.stack[-1]["pre"] = pre

    def _nearest(self, tags):
        for frame in reversed(self.stack[:-1]):
            if frame["tag"] in tags:
                return frame["tag"]
        return None

    def handle_endtag(self, tag):
        if tag in VOID:
            return
        frame = None
        for i in range(len(self.stack) - 1, -1, -1):
            if self.stack[i]["tag"] == tag:
                frame = self.stack[i]
                del self.stack[i:]
                break
        if frame is None:
            return

        if self.skip_depth is not None:
            if len(self.stack) < self.skip_depth:
                self.skip_depth = None
            return

        if tag in ("td", "th") and self.cell is not None:
            text = re.sub(r"\s+", " ", "".join(self.cell)).strip()
            self.cell = None
            if self.row is not None:
                self.row.append(text.replace("|", "\\|"))
            return

        if tag == "tr" and self.row is not None:
            if any(c for c in self.row):
                self.table.append(self.row)
            self.row = None
            return

        if tag == "table" and self.table is not None:
            self._flush_table()
            return

        if frame.get("pre"):
            self.pre = max(0, self.pre - 1)
        if frame.get("close"):
            self.emit(frame["close"])

    def handle_data(self, data):
        if self.skip_depth is not None or not data:
            return
        if self.pre:
            self.emit(data)
        else:
            text = re.sub(r"\s+", " ", data)
            if text.strip() or text == " ":
                self.emit(text)

    # ---- tables ----------------------------------------------------------

    def _flush_table(self):
        rows, self.table, self.row = self.table, None, None
        if not rows:
            return
        width = max(len(r) for r in rows)
        rows = [r + [""] * (width - len(r)) for r in rows]
        head, body = rows[0], rows[1:]
        if not body:                     # single-row table: keep it readable
            head, body = [""] * width, rows
        lines = ["| " + " | ".join(head) + " |",
                 "| " + " | ".join(["---"] * width) + " |"]
        lines += ["| " + " | ".join(r) + " |" for r in body]
        self.emit("\n\n" + "\n".join(lines) + "\n\n")

    # ---- result ----------------------------------------------------------

    def markdown(self):
        text = "".join(self.out)
        text = re.sub(r"[ \t]+\n", "\n", text)
        text = re.sub(r"\n{3,}", "\n\n", text)
        text = re.sub(r" {2,}(?!\n)", " ", text)
        text = re.sub(r" +([,.;:)\]])", r"\1", text)
        text = re.sub(r"\(\s+", "(", text)
        return text.strip() + "\n"


def looks_like_latexml(source):
    """True when arXiv actually rendered this paper, rather than serving a stub.

    Papers submitted as PDF, or too old for the HTML pipeline, still answer
    HTTP 200 with a placeholder page — status alone cannot be trusted.
    """
    head = source[:400000]
    return ("ltx_para" in head) or ("ltx_section" in head)


def html_to_markdown(source):
    """(markdown, warnings) from LaTeXML HTML given as str or bytes."""
    if isinstance(source, bytes):
        source = source.decode("utf-8", "replace")
    if not looks_like_latexml(source):
        return "", ["no LaTeXML content in the HTML"]
    p = LatexmlParser()
    p.feed(source)
    p.close()
    return p.markdown(), p.warnings


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("input")
    ap.add_argument("-o", "--output")
    args = ap.parse_args()

    with open(args.input, "rb") as fh:
        md, warnings = html_to_markdown(fh.read())
    for w in warnings:
        print(f"WARNING: {w}", file=sys.stderr)
    if args.output:
        with open(args.output, "w", encoding="utf-8") as fh:
            fh.write(md)
    else:
        sys.stdout.write(md)


if __name__ == "__main__":
    main()
