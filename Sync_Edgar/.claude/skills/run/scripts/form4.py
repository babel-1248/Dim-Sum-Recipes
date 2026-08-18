#!/usr/bin/env python3
"""Convert an SEC ownership document (Form 3, 4 or 5) XML into markdown.

Forms 3/4/5 are the one EDGAR form family that is *already* structured, so this
does not go through the HTML converter at all — it reads the raw XML and builds
a transaction table. That matters because the interesting question ("did an
insider actually buy, or was this just vesting?") is answered by a single
character, the transaction code, which the rendered HTML shows as a bare "S" or
"F" in a column of its own.

Decoding that column is most of this file's value. `P` and `S` are open-market
conviction trades. `A`, `F` and `M` are compensation mechanics — an award
vesting, shares withheld to pay the tax on it, an option being exercised — and
reading those as insider sentiment is the classic misreading of a Form 4. The
Rule 10b5-1 flag matters for the same reason: a sale scheduled months earlier by
a standing plan says nothing about what the insider thinks today.

The primaryDocument in the submissions API points at the XSL *rendering*
(`xslF345X03/form4.xml`); the raw XML this parses is the same filename with that
directory stripped. Doing it the other way round means parsing a stylesheet's
output rather than the data.

Usage:  python3 form4.py ownership.xml [-o out.md]
"""
import argparse
import sys
import xml.etree.ElementTree as ET

# Table I/II code column. The A/D acquired-disposed flag says which direction;
# the code says what kind of event it was, which is the part that carries
# meaning.
CODES = {
    "P": "open-market purchase",
    "S": "open-market sale",
    "V": "voluntary early-reported transaction",
    "A": "grant/award from the issuer",
    "D": "disposition back to the issuer",
    "F": "shares withheld to pay tax/exercise price",
    "I": "discretionary transaction",
    "M": "exercise/conversion of a derivative",
    "C": "conversion of a derivative security",
    "E": "expiration of a short derivative position",
    "H": "expiration (long position) or holding",
    "O": "exercise of an out-of-the-money derivative",
    "X": "exercise of an in-the-money derivative",
    "G": "bona fide gift",
    "L": "small acquisition",
    "W": "acquisition or disposition by will or inheritance",
    "Z": "deposit into or withdrawal from a voting trust",
    "J": "other (see footnotes)",
    "K": "equity swap or similar instrument",
    "U": "disposition pursuant to a tender of shares",
}

# Compact verbs for a note title. "S" is precise and unreadable; a title that
# says "sold $442,852" is the whole difference between a feed you skim and a
# feed you open.
SHORT = {"P": "bought", "S": "sold", "A": "award", "F": "tax withholding",
         "M": "option exercise", "X": "option exercise", "C": "conversion",
         "G": "gift", "D": "returned to issuer", "J": "other", "K": "swap",
         "U": "tendered", "W": "inherited", "Z": "voting trust", "I": "discretionary"}


def short_action(facts):
    """One phrase naming what the filing actually reports."""
    net = facts.get("net_open_market_value") or 0
    if net:
        return f"{'bought' if net > 0 else 'sold'} ${abs(net):,.0f}"
    codes = facts.get("codes") or []
    if not codes:
        return "holdings only"
    seen, out = set(), []
    for c in codes:
        label = SHORT.get(c, c)
        if label not in seen:
            seen.add(label)
            out.append(label)
    return ", ".join(out)


FORM_NAMES = {"3": "Form 3 — initial statement of beneficial ownership",
              "4": "Form 4 — changes in beneficial ownership",
              "5": "Form 5 — annual statement of beneficial ownership"}


def val(node, path, default=""):
    """Ownership XML wraps almost every leaf in <value>; unwrap transparently."""
    el = node.find(path) if node is not None else None
    if el is None:
        return default
    inner = el.find("value")
    text = (inner if inner is not None else el).text
    return (text or "").strip() or default


def footnote_refs(node, *paths):
    """Footnote ids attached anywhere under the given paths, as [F1] markers."""
    ids = []
    for p in paths:
        for el in (node.findall(p) if node is not None else []):
            for fn in el.iter("footnoteId"):
                fid = fn.get("id")
                if fid and fid not in ids:
                    ids.append(fid)
    return "".join(f"[{i}]" for i in ids)


def num(text):
    try:
        return float(text)
    except (TypeError, ValueError):
        return None


def fmt_shares(text):
    n = num(text)
    return f"{n:,.0f}" if n is not None and n == int(n) else (
        f"{n:,.4f}".rstrip("0").rstrip(".") if n is not None else (text or "—"))


def fmt_price(text):
    n = num(text)
    return f"${n:,.4f}".rstrip("0").rstrip(".") if n is not None else "—"


def owners(root):
    out = []
    for ro in root.findall("reportingOwner"):
        rel = ro.find("reportingOwnerRelationship")
        roles = []
        if val(rel, "isDirector") in ("1", "true"):
            roles.append("director")
        if val(rel, "isOfficer") in ("1", "true"):
            roles.append(val(rel, "officerTitle") or "officer")
        if val(rel, "isTenPercentOwner") in ("1", "true"):
            roles.append("10% owner")
        if val(rel, "isOther") in ("1", "true"):
            roles.append(val(rel, "otherText") or "other")
        out.append({
            "name": val(ro, "reportingOwnerId/rptOwnerName"),
            "cik": val(ro, "reportingOwnerId/rptOwnerCik"),
            "roles": roles,
        })
    return out


def transactions(root, derivative=False):
    table = "derivativeTable" if derivative else "nonDerivativeTable"
    kind = "derivativeTransaction" if derivative else "nonDerivativeTransaction"
    rows = []
    for t in root.findall(f"{table}/{kind}"):
        code = val(t, "transactionCoding/transactionCode")
        ad = val(t, "transactionAmounts/transactionAcquiredDisposedCode")
        shares = val(t, "transactionAmounts/transactionShares")
        price = val(t, "transactionAmounts/transactionPricePerShare")
        s, p = num(shares), num(price)
        row = {
            "security": val(t, "securityTitle") or val(t, "securityTitle/value"),
            "date": val(t, "transactionDate"),
            "code": code,
            "meaning": CODES.get(code, "unrecognised code"),
            "direction": {"A": "acquired", "D": "disposed"}.get(ad, ad or "—"),
            "shares": shares,
            "price": price,
            # The dollar figure is the first thing a reader wants and the one
            # thing the form never states; it is only ever shares x price.
            "value": (s * p) if (s is not None and p) else None,
            "after": val(t, "postTransactionAmounts/sharesOwnedFollowingTransaction"),
            "ownership": val(t, "ownershipNature/directOrIndirectOwnership"),
            "nature": val(t, "ownershipNature/natureOfOwnership"),
            "notes": footnote_refs(t, "."),
        }
        if derivative:
            row.update({
                "strike": val(t, "conversionOrExercisePrice"),
                "exercisable": val(t, "exerciseDate"),
                "expires": val(t, "expirationDate"),
                "underlying": val(t, "underlyingSecurity/underlyingSecurityTitle"),
                "underlying_shares": val(t, "underlyingSecurity/underlyingSecurityShares"),
            })
        rows.append(row)
    return rows


def holdings(root, derivative=False):
    table = "derivativeTable" if derivative else "nonDerivativeTable"
    kind = "derivativeHolding" if derivative else "nonDerivativeHolding"
    rows = []
    for h in root.findall(f"{table}/{kind}"):
        rows.append({
            "security": val(h, "securityTitle"),
            "after": val(h, "postTransactionAmounts/sharesOwnedFollowingTransaction"),
            "ownership": val(h, "ownershipNature/directOrIndirectOwnership"),
            "nature": val(h, "ownershipNature/natureOfOwnership"),
            "strike": val(h, "conversionOrExercisePrice"),
            "expires": val(h, "expirationDate"),
            "underlying": val(h, "underlyingSecurity/underlyingSecurityTitle"),
            "underlying_shares": val(h, "underlyingSecurity/underlyingSecurityShares"),
            "notes": footnote_refs(h, "."),
        })
    return rows


def summary_line(rows):
    """One sentence a reader can act on, before the tables."""
    bought = sum(r["value"] or 0 for r in rows
                 if r["code"] == "P" and r["direction"] == "acquired")
    sold = sum(r["value"] or 0 for r in rows
               if r["code"] == "S" and r["direction"] == "disposed")
    bits = []
    if bought:
        bits.append(f"**bought ${bought:,.0f}** on the open market")
    if sold:
        bits.append(f"**sold ${sold:,.0f}** on the open market")
    other = [r for r in rows if r["code"] not in ("P", "S")]
    if other and not bits:
        kinds = sorted({CODES.get(r["code"], r["code"]) for r in other})
        return "No open-market trade — " + "; ".join(kinds) + "."
    if other:
        bits.append(f"plus {len(other)} non-market transaction(s)")
    return ("Insider " + ", ".join(bits) + ".") if bits else ""


def render(root):
    doc_type = val(root, "documentType") or "4"
    period = val(root, "periodOfReport")
    issuer_name = val(root, "issuer/issuerName")
    symbol = val(root, "issuer/issuerTradingSymbol")
    plan = val(root, "aff10b5One") in ("1", "true")

    who = owners(root)
    nd, dv = transactions(root), transactions(root, True)

    lines = []
    head = [f"**{FORM_NAMES.get(doc_type, 'Form ' + doc_type)}**"]
    if period:
        head.append(f"Period **{period}**")
    lines.append("  \n".join(head))
    lines.append(f"Issuer: **{issuer_name}**" + (f" ({symbol})" if symbol else ""))
    for o in who:
        roles = ", ".join(o["roles"]) or "reporting person"
        lines.append(f"Reporting person: **{o['name']}** — {roles}")
    if plan:
        # This flag is why a large sale often means nothing: the trade was
        # scheduled by a standing plan, not decided this week.
        lines.append("Marked as made under a **Rule 10b5-1 trading plan**.")

    summary = summary_line(nd + dv)
    if summary:
        lines.append("\n" + summary)

    out = ["  \n".join(lines[:1]) + "\n\n" + "\n\n".join(lines[1:])]

    if nd:
        out.append("\n## Non-derivative transactions\n")
        out.append("| Date | Security | Code | What it is | Dir | Shares | Price | Value | Owned after | D/I |")
        out.append("|---|---|---|---|---|---|---|---|---|---|")
        for r in nd:
            value = f"${r['value']:,.0f}" if r["value"] else "—"
            out.append(
                f"| {r['date'] or '—'} | {r['security']}{r['notes']} | **{r['code']}** | "
                f"{r['meaning']} | {r['direction']} | {fmt_shares(r['shares'])} | "
                f"{fmt_price(r['price'])} | {value} | {fmt_shares(r['after'])} | "
                f"{r['ownership'] or '—'} |")

    if dv:
        out.append("\n## Derivative transactions\n")
        out.append("| Date | Security | Code | What it is | Dir | Shares | Price | "
                   "Strike | Expires | Underlying | Owned after |")
        out.append("|---|---|---|---|---|---|---|---|---|---|---|")
        for r in dv:
            und = f"{fmt_shares(r['underlying_shares'])} {r['underlying']}".strip()
            out.append(
                f"| {r['date'] or '—'} | {r['security']}{r['notes']} | **{r['code']}** | "
                f"{r['meaning']} | {r['direction']} | {fmt_shares(r['shares'])} | "
                f"{fmt_price(r['price'])} | {fmt_price(r['strike'])} | "
                f"{r['expires'] or '—'} | {und} | {fmt_shares(r['after'])} |")

    hold_nd, hold_dv = holdings(root), holdings(root, True)
    if hold_nd or hold_dv:
        # Form 3 is all holdings and no transactions; without this section such
        # a filing would convert to an empty note.
        out.append("\n## Holdings\n")
        out.append("| Security | Shares owned | D/I | Strike | Expires | Underlying |")
        out.append("|---|---|---|---|---|---|")
        for r in hold_nd + hold_dv:
            und = f"{fmt_shares(r['underlying_shares'])} {r['underlying']}".strip(" —")
            out.append(
                f"| {r['security']}{r['notes']} | {fmt_shares(r['after'])} | "
                f"{r['ownership'] or '—'} | {fmt_price(r['strike'])} | "
                f"{r['expires'] or '—'} | {und or '—'} |")

    indirect = [r for r in nd + dv + hold_nd + hold_dv if r.get("nature")]
    if indirect:
        out.append("\n**Nature of indirect ownership:** "
                   + "; ".join(sorted({r["nature"] for r in indirect})))

    notes = [(f.get("id"), " ".join((f.text or "").split()))
             for f in root.findall("footnotes/footnote")]
    if notes:
        out.append("\n## Footnotes\n")
        out += [f"- **[{i}]** {t}" for i, t in notes]

    remarks = val(root, "remarks")
    if remarks:
        out.append(f"\n**Remarks:** {remarks}")

    sig = val(root, "ownerSignature/signatureName")
    if sig:
        out.append(f"\n*Signed: {sig} ({val(root, 'ownerSignature/signatureDate')})*")

    return "\n".join(out).strip() + "\n"


def ownership_to_markdown(source):
    """(markdown, warnings, facts) for one Form 3/4/5 XML document."""
    if isinstance(source, str):
        source = source.encode("utf-8")
    try:
        root = ET.fromstring(source)
    except ET.ParseError as exc:
        return "", [f"ownership XML did not parse: {exc}"], {}
    if root.tag != "ownershipDocument":
        return "", [f"not an ownership document (root <{root.tag}>)"], {}

    nd, dv = transactions(root), transactions(root, True)
    facts = {
        "document_type": val(root, "documentType"),
        "period": val(root, "periodOfReport"),
        "issuer": val(root, "issuer/issuerName"),
        "symbol": val(root, "issuer/issuerTradingSymbol"),
        "owners": owners(root),
        "rule_10b5_1": val(root, "aff10b5One") in ("1", "true"),
        "codes": sorted({r["code"] for r in nd + dv if r["code"]}),
        "net_open_market_value": (
            sum(r["value"] or 0 for r in nd + dv
                if r["code"] == "P" and r["direction"] == "acquired")
            - sum(r["value"] or 0 for r in nd + dv
                  if r["code"] == "S" and r["direction"] == "disposed")),
        "summary": summary_line(nd + dv),
    }
    return render(root), [], facts


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("input")
    ap.add_argument("-o", "--output")
    args = ap.parse_args()
    with open(args.input, "rb") as fh:
        md, warnings, _ = ownership_to_markdown(fh.read())
    for w in warnings:
        print(f"WARNING: {w}", file=sys.stderr)
    if args.output:
        with open(args.output, "w", encoding="utf-8") as fh:
            fh.write(md)
    else:
        sys.stdout.write(md)


if __name__ == "__main__":
    main()
