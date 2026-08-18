#!/usr/bin/env python3
"""Convert an EDGAR filing document (HTML or inline XBRL) into markdown.

EDGAR HTML is not semantic. Filers publish whatever their document tool emits —
Workiva, Toppan Merrill, DFIN — so headings are bold <span>s rather than <h2>,
paragraphs are <div>s, and layout is done with tables. Three EDGAR-specific
problems shape this converter:

  * **Inline XBRL.** Modern filings are iXBRL: the visible document is wrapped in
    <ix:*> tags. <ix:header>, <ix:hidden>, <ix:references> and <ix:resources> hold
    tagging metadata and *hidden* facts — text that is never displayed. Rendering
    them produces pages of stray numbers before the document even starts. They are
    dropped whole. <ix:nonFraction> and <ix:nonNumeric>, by contrast, wrap the
    visible text and must be recursed into, not skipped.

  * **Spacer columns.** A financial table in EDGAR is typically half empty: one
    column for the "$", one for the ")" of a negative, one for a 2px gutter. A
    naive conversion yields `| $ |  | 391,035 |  |  | $ |  | 383,285 |`. Columns
    empty in every row are pruned before the table is emitted.

  * **Layout tables.** Filers indent a paragraph by putting it in a one-cell table.
    Those must come out as prose, not as a one-column markdown table, or the note
    fills with `| --- |` noise. A table is treated as data only when it has at
    least two columns and more than one row after pruning.

Non-breaking spaces (&nbsp;, \\xa0) are everywhere in EDGAR and are normalised to
ordinary spaces, so downstream Item-section matching sees "Item 1A." rather than
"Item\\xa01A.".

Usage:  python3 edgarhtml2md.py filing.htm [-o out.md]
"""
import argparse
import re
import sys
from html.parser import HTMLParser

VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link",
        "meta", "param", "source", "track", "wbr"}

# Whole subtrees that never belong in a note body. The ix:* four are iXBRL
# plumbing; ix:hidden in particular holds facts deliberately not displayed.
SKIP_TAGS = {"script", "style", "head", "title", "svg", "button", "form",
             "ix:header", "ix:hidden", "ix:references", "ix:resources",
             "xbrl", "link:schemaref"}

# A table needs this many rows and columns (after pruning empties) before it is
# worth rendering as a table rather than as prose.
MIN_TABLE_ROWS = 2
MIN_TABLE_COLS = 2

BOLD_RE = re.compile(r"font-weight\s*:\s*(bold|[6-9]00)", re.I)
ITALIC_RE = re.compile(r"font-style\s*:\s*italic", re.I)
# EDGAR renders page breaks as a styled div or an <hr>; both are noise in a note.
PAGEBREAK_RE = re.compile(r"page-break-(after|before)\s*:\s*always", re.I)


class EdgarParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.out = []
        self.stack = []
        self.skip_depth = None
        self.table_depth = 0     # >0 while inside any <table>
        self.tables = []         # stack of row-lists, for nested tables
        self.row = None
        self.cell = None
        self.colspan = 1
        self.cell_right = False
        self.warnings = []

    # ---- output plumbing -------------------------------------------------

    def emit(self, text):
        if self.cell is not None:
            self.cell.append(text)
        else:
            self.out.append(text)

    def _set_close(self, text):
        if self.stack:
            self.stack[-1]["close"] = text

    # ---- tags ------------------------------------------------------------

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        style = a.get("style") or ""
        if tag not in VOID:
            self.stack.append({"tag": tag, "close": ""})

        if self.skip_depth is not None:
            return
        if tag in SKIP_TAGS:
            self.skip_depth = len(self.stack)
            return

        if tag == "table":
            self.table_depth += 1
            self.tables.append([])
            return

        if tag == "tr" and self.tables:
            self.row = []
            return

        if tag in ("td", "th") and self.tables:
            self.cell = []
            # colspan is not cosmetic here: EDGAR spans header cells across the
            # value columns beneath them, so ignoring it shifts every later cell
            # left by one and the years stop lining up with their figures. The
            # span is padded out with empty cells, which the spacer-column prune
            # then removes if the whole column turns out to be empty.
            try:
                self.colspan = max(1, min(20, int(a.get("colspan") or 1)))
            except ValueError:
                self.colspan = 1
            # Where the padding goes depends on alignment. EDGAR gives the
            # currency symbol its own narrow column, and a row with no "$" just
            # spans its figure across both — right-aligned, so the figure sits
            # at the *right* edge of the span. Padding after it would put that
            # figure one column left of every "$"-bearing row's figure, which is
            # exactly the off-by-one that makes the statements unreadable.
            self.cell_right = "right" in (style + a.get("align", "")).lower()
            return

        if re.fullmatch(r"h[1-6]", tag):
            self.emit("\n\n" + "#" * int(tag[1]) + " ")
            self._set_close("\n\n")
            return

        if tag in ("p", "div", "tr_", "li"):
            if tag == "li":
                self.emit("\n- ")
                self._set_close("")
            else:
                self.emit("\n\n")
                self._set_close("\n\n")
            if PAGEBREAK_RE.search(style):
                self.emit("\n\n---\n\n")
            return

        if tag == "br":
            self.emit(" " if self.cell is not None else "  \n")
            return

        if tag == "hr":
            self.emit("\n\n---\n\n")
            return

        if tag == "a":
            href = a.get("href") or ""
            if href.startswith(("http://", "https://")):
                self.emit("[")
                self._set_close(f"]({href})")
            return

        # Emphasis is skipped inside tables: filers bold entire financial
        # statements, and `**` around every cell reads worse than plain text.
        if self.table_depth:
            return
        if tag in ("b", "strong") or BOLD_RE.search(style):
            self.emit("**")
            self._set_close("**")
            return
        if tag in ("i", "em") or ITALIC_RE.search(style):
            self.emit("*")
            self._set_close("*")

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
                pad = [""] * (self.colspan - 1)
                cell = text.replace("|", "\\|")
                right = self.cell_right or bool(
                    re.fullmatch(r"[\d.,()$%\u2014\u2013\- ]+", text or " "))
                self.row.extend(pad + [cell] if right else [cell] + pad)
            self.colspan = 1
            self.cell_right = False
            return

        if tag == "tr" and self.row is not None:
            self.tables[-1].append(self.row)
            self.row = None
            return

        if tag == "table" and self.tables:
            rows = self.tables.pop()
            self.table_depth = max(0, self.table_depth - 1)
            self._flush_table(rows)
            return

        if frame.get("close"):
            self.emit(frame["close"])

    def handle_data(self, data):
        if self.skip_depth is not None or not data:
            return
        # \xa0 is pervasive in EDGAR and breaks "Item 1A." matching downstream;
        # \u200b (zero-width space) is a line-height spacer in several filing
        # tools and survives .strip(), so an "empty" styled span would come out
        # of the emphasis tidy-up as a stray "****".
        text = re.sub("[\\s\u00a0\u2007\u202f\u200b\ufeff]+", " ", data)
        if text.strip() or text == " ":
            self.emit(text)

    # ---- tables ----------------------------------------------------------

    @staticmethod
    def _merge_glyph_cells(row):
        """Fold EDGAR's lone-glyph cells into the number they decorate.

        Filers put the currency symbol in its own <td> and the closing paren of
        a negative in another, and they emit the "$" only on the first row of a
        block. Left alone, one row reads `| $ | 294,866 |` and the next
        `| 96,169 | |`, so the same column holds a symbol in one row and a
        figure in the next — every column is one step out of phase with its
        neighbours. Merging the glyph into its number puts all the figures back
        in one column, and the emptied cells are then pruned away.
        """
        out = []
        pending = ""
        for cell in row:
            c = cell.strip()
            if c in ("$", "("):
                pending += c
                out.append("")
                continue
            if c in (")", "%") and out:
                for i in range(len(out) - 1, -1, -1):
                    if out[i]:
                        out[i] += c
                        break
                out.append("")
                continue
            out.append(pending + cell if c else cell)
            pending = ""
        return out

    def _flush_table(self, rows):
        """Prune spacer columns, then emit as a table or as prose."""
        rows = [self._merge_glyph_cells(r) for r in rows]
        rows = [r for r in rows if any(c.strip() for c in r)]
        if not rows:
            return
        width = max(len(r) for r in rows)
        rows = [r + [""] * (width - len(r)) for r in rows]

        keep = [i for i in range(width) if any(r[i].strip() for r in rows)]
        # A column holding only currency/parenthesis glyphs is a spacer too.
        keep = [i for i in keep
                if any(r[i].strip(" $()%") for r in rows)]
        if not keep:
            return
        rows = [[r[i] for i in keep] for r in rows]

        if len(rows) < MIN_TABLE_ROWS or len(keep) < MIN_TABLE_COLS:
            # Layout table: emit the text so nothing is lost, but as prose.
            for r in rows:
                line = " ".join(c for c in r if c.strip())
                if line.strip():
                    self.emit("\n\n" + line + "\n\n")
            return

        head, body = rows[0], rows[1:]
        # A leading row that is all-numeric is data, not a header; give the
        # table an empty header rather than eating its first line of figures.
        if all(re.fullmatch(r"[\d.,()$%\- ]*", c) for c in head) and any(head):
            head, body = [""] * len(keep), rows
        lines = ["| " + " | ".join(head) + " |",
                 "| " + " | ".join(["---"] * len(keep)) + " |"]
        lines += ["| " + " | ".join(r) + " |" for r in body]
        self.emit("\n\n" + "\n".join(lines) + "\n\n")

    # ---- result ----------------------------------------------------------

    def markdown(self):
        text = "".join(self.out)
        text = re.sub(r"[ \t]+\n", "\n", text)
        text = re.sub(r"\n{3,}", "\n\n", text)
        text = re.sub(r" {2,}(?!\n)", " ", text)
        text = re.sub(r"\n(---\n\n)+", "\n---\n\n", text)
        # Tidy emphasis without letting it swallow paragraph breaks. EDGAR puts
        # each heading line in its own <div><span font-weight:700>, so a greedy
        # `\*\*\s*\*\*` cleanup would delete the `**\n\n**` *between* two
        # headings and weld them into "UNITED STATESSECURITIES AND EXCHANGE".
        # Restricted to spaces and tabs, it only drops genuinely empty pairs and
        # moves padding outside the markers.
        text = re.sub(r"\*\*([ \t]*)([^*\n]*?)([ \t]*)\*\*",
                      lambda m: (f"{m.group(1)}**{m.group(2)}**{m.group(3)}"
                                 if m.group(2).strip() else m.group(1) + m.group(3)),
                      text)
        return text.strip() + "\n"


def html_to_markdown(source):
    """(markdown, warnings) from an EDGAR filing document, str or bytes."""
    if isinstance(source, bytes):
        # EDGAR serves a mix of utf-8, latin-1 and ascii; replace rather than fail.
        source = source.decode("utf-8", "replace")
    p = EdgarParser()
    p.feed(source)
    p.close()
    md = p.markdown()
    warnings = list(p.warnings)
    if len(md.strip()) < 200:
        warnings.append("converted document is nearly empty")
    return md, warnings


DOC_SPLIT_RE = re.compile(r"<DOCUMENT>(.*?)(?:</DOCUMENT>|\Z)", re.S | re.I)
TEXT_RE = re.compile(r"<TEXT>(.*?)(?:</TEXT>|\Z)", re.S | re.I)
TAG_RE = re.compile(r"<(TYPE|DESCRIPTION|FILENAME)>([^\n<]*)", re.I)
# Attachments with no prose: uuencoded images, spreadsheets, XBRL taxonomies.
BINARY_TYPES = ("GRAPHIC", "ZIP", "EXCEL", "EX-27", "EX-101", "EX-104", "XML")
HTML_MARKUP_RE = re.compile(r"<(td|tr|div|font|p)[\s>]", re.I)
# SGML layout markers in pre-2001 text: <TABLE>, the <S>/<C> column-type row,
# <PAGE> breaks and <FN> footnote wrappers. They are not content.
LEGACY_TAG_RE = re.compile(r"</?(TABLE|CAPTION|S|C|PAGE|FN|F\d+)>", re.I)


def text_from_txt(source):
    """Convert a complete SGML submission (the `<accession>.txt` file).

    Filings before roughly 2001 have no separate primary document — EDGAR serves
    the whole submission as one SGML file, and it opens with a PEM signature
    block and a header of filer metadata before any of the filing appears.
    Rendering that verbatim buries the 10-Q under a page of base64, so the
    wrapper is unwrapped here and each `<DOCUMENT>` handled on its own: HTML
    exhibits through the normal converter, fixed-width ASCII kept in a fenced
    block where its column alignment still reads correctly.
    """
    if isinstance(source, bytes):
        source = source.decode("utf-8", "replace")
    parts, warnings = [], []
    for chunk in DOC_SPLIT_RE.findall(source):
        tags = {k.upper(): v.strip() for k, v in TAG_RE.findall(chunk[:2000])}
        dtype = tags.get("TYPE", "")
        if any(dtype.upper().startswith(b) for b in BINARY_TYPES):
            continue
        body = TEXT_RE.search(chunk)
        body = body.group(1) if body else chunk
        # <TABLE> alone does not mean HTML here. A 1990s filing marks up its
        # financial statements as <TABLE><S><C><C> around fixed-width ASCII, with
        # no <TR> or <TD> anywhere — the columns are made of spaces. Sending that
        # through the HTML converter collapses the whitespace and the statements
        # become one unreadable line, so real cell or block markup is required
        # before the HTML path is taken.
        if HTML_MARKUP_RE.search(body[:20000]):
            md, warns = html_to_markdown(body)
            warnings += warns
        else:
            md = "```\n" + LEGACY_TAG_RE.sub("", body).strip() + "\n```"
        if md.strip(" `\n"):
            label = dtype or "Document"
            desc = tags.get("DESCRIPTION", "")
            head = f"### {label}" + (f" — {desc}" if desc and desc != label else "")
            parts.append(f"{head}\n\n{md}")
    if not parts:
        # Not an SGML submission after all — treat it as whatever it looks like.
        return (html_to_markdown(source) if "<HTML" in source[:5000].upper()
                else ("```\n" + source.strip() + "\n```\n", []))
    return "\n\n".join(parts) + "\n", warnings


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
