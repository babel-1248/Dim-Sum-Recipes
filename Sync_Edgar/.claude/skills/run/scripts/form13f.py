#!/usr/bin/env python3
"""Parse a Form 13F information-table XML document into Markdown and facts.

The information table attached to 13F-HR and 13F-HR/A is structured XML. This
module preserves each reported row instead of trying to infer transactions or
compare it with another quarter. Values filed before January 3, 2023 were
reported in thousands of dollars; later information tables report dollars.

Usage: python3 form13f.py information-table.xml --filed YYYY-MM-DD [-o out.md]
"""
import argparse
import json
import sys
import xml.etree.ElementTree as ET
from decimal import Decimal, InvalidOperation


VALUE_UNIT_CHANGE = "2023-01-03"


def local_name(tag):
    """Return an XML tag without its optional namespace."""
    return tag.rsplit("}", 1)[-1]


def child(node, name):
    wanted = name.lower()
    for item in list(node) if node is not None else []:
        if local_name(item.tag).lower() == wanted:
            return item
    return None


def at(node, path):
    current = node
    for name in path.split("/"):
        current = child(current, name)
        if current is None:
            return None
    return current


def text(node, path, default=""):
    item = at(node, path)
    if item is None:
        return default
    wrapped = child(item, "value")
    value = (wrapped if wrapped is not None else item).text
    return " ".join((value or "").split()) or default


def number(value):
    try:
        return Decimal(str(value).replace(",", ""))
    except (InvalidOperation, ValueError):
        return None


def json_number(value):
    if value is None:
        return None
    return int(value) if value == value.to_integral_value() else float(value)


def markdown_cell(value):
    return (str(value or "—").replace("|", "\\|")
            .replace("\r", " ").replace("\n", " "))


def format_number(value):
    if value is None:
        return "—"
    if value == value.to_integral_value():
        return f"{int(value):,}"
    return f"{value:,.4f}".rstrip("0").rstrip(".")


def format_money(value):
    return "—" if value is None else f"${format_number(value)}"


def is_information_table(root):
    return local_name(root.tag).lower() == "informationtable"


def parse(source, filed=None):
    """Return (holdings, facts, warnings) without comparing another filing."""
    if isinstance(source, str):
        source = source.encode("utf-8")
    try:
        root = ET.fromstring(source)
    except ET.ParseError as exc:
        return [], {}, [f"13F information-table XML did not parse: {exc}"]
    if not is_information_table(root):
        return [], {}, [f"not a 13F information table (root <{local_name(root.tag)}>)"]
    if not filed:
        return [], {}, ["13F filing date is required to resolve the reported value unit"]

    # EDGAR changed an information-table row's value from thousands of dollars
    # to dollars in release 22.4.1. Normalize both eras to value_usd while also
    # retaining the exact reported number and its unit.
    scale = Decimal(1) if filed and filed >= VALUE_UNIT_CHANGE else Decimal(1000)
    reported_unit = "USD" if scale == 1 else "USD thousands"
    warnings = []
    holdings = []
    for row_number, item in enumerate(
            (el for el in root.iter() if local_name(el.tag).lower() == "infotable"), 1):
        reported_value = number(text(item, "value"))
        shares = number(text(item, "shrsOrPrnAmt/sshPrnamt"))
        sole = number(text(item, "votingAuthority/Sole"))
        shared = number(text(item, "votingAuthority/Shared"))
        none = number(text(item, "votingAuthority/None"))
        if reported_value is None:
            warnings.append(f"row {row_number} has no numeric value")
        holdings.append({
            "issuer": text(item, "nameOfIssuer"),
            "class": text(item, "titleOfClass"),
            "cusip": text(item, "cusip"),
            "figi": text(item, "figi"),
            "reported_value": json_number(reported_value),
            "reported_value_unit": reported_unit,
            "value_usd": (
                json_number(reported_value * scale)
                if reported_value is not None else None),
            "shares_or_principal": json_number(shares),
            "shares_or_principal_type": text(item, "shrsOrPrnAmt/sshPrnamtType"),
            "put_call": text(item, "putCall"),
            "investment_discretion": text(item, "investmentDiscretion"),
            "other_manager": text(item, "otherManager"),
            "voting_sole": json_number(sole),
            "voting_shared": json_number(shared),
            "voting_none": json_number(none),
        })

    total = sum((Decimal(str(row["value_usd"])) for row in holdings
                 if row["value_usd"] is not None), Decimal(0))
    facts = {
        "entry_count": len(holdings),
        "total_value_usd": json_number(total),
        "reported_value_unit": reported_unit,
        "value_unit_scale_to_usd": int(scale),
        "holdings": holdings,
    }
    if not holdings:
        warnings.append("13F information table contains no holdings")
    return holdings, facts, warnings


def render(holdings, facts):
    """Render all reported holdings as a stable, value-sorted Markdown table."""
    count = facts.get("entry_count", len(holdings))
    total = number(facts.get("total_value_usd")) or Decimal(0)
    unit = facts.get("reported_value_unit") or "unknown"
    lines = [
        "## Form 13F holdings",
        "",
        f"**{count:,} reported holding row(s)** · **{format_money(total)} total reported value**",
        "",
        f"SEC row values were reported in **{unit}** and are displayed below as normalized US dollars.",
        "No quarter-to-quarter changes or transaction directions are inferred from this filing.",
        "",
        "| Issuer | Class | CUSIP | FIGI | Value | Portfolio | Shares / principal | Type | Put/Call | Discretion | Other manager | Vote sole | Vote shared | Vote none |",
        "|---|---|---|---|---:|---:|---:|---|---|---|---|---:|---:|---:|",
    ]
    ordered = sorted(holdings, key=lambda row: (
        -(row["value_usd"] or 0), row["issuer"], row["cusip"], row["put_call"]))
    for row in ordered:
        value = number(row["value_usd"])
        weight = (value / total * 100) if value is not None and total else None
        weight_text = f"{weight:.2f}%" if weight is not None else "—"
        lines.append(
            "| " + " | ".join([
                markdown_cell(row["issuer"]),
                markdown_cell(row["class"]),
                markdown_cell(row["cusip"]),
                markdown_cell(row["figi"]),
                format_money(value),
                weight_text,
                format_number(number(row["shares_or_principal"])),
                markdown_cell(row["shares_or_principal_type"]),
                markdown_cell(row["put_call"]),
                markdown_cell(row["investment_discretion"]),
                markdown_cell(row["other_manager"]),
                format_number(number(row["voting_sole"])),
                format_number(number(row["voting_shared"])),
                format_number(number(row["voting_none"])),
            ]) + " |"
        )
    return "\n".join(lines).strip() + "\n"


def information_table_to_markdown(source, filed=None):
    """Return (markdown, warnings, facts) for one 13F information table."""
    holdings, facts, warnings = parse(source, filed=filed)
    return (render(holdings, facts) if facts else ""), warnings, facts


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("input")
    ap.add_argument("--filed", required=True,
                    help="filing date used to resolve the SEC value unit")
    ap.add_argument("-o", "--output")
    ap.add_argument("--json", dest="json_output",
                    help="also write normalized holdings and summary facts as JSON")
    args = ap.parse_args()
    with open(args.input, "rb") as fh:
        md, warnings, facts = information_table_to_markdown(fh.read(), args.filed)
    for warning in warnings:
        print(f"WARNING: {warning}", file=sys.stderr)
    if args.output:
        with open(args.output, "w", encoding="utf-8") as fh:
            fh.write(md)
    else:
        sys.stdout.write(md)
    if args.json_output:
        with open(args.json_output, "w", encoding="utf-8") as fh:
            json.dump(facts, fh, indent=2, ensure_ascii=False)
            fh.write("\n")


if __name__ == "__main__":
    main()
