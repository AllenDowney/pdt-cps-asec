"""Parse a CPS ASEC public-use data dictionary (asecYYYY_ddl_pub_full.pdf).

The dictionary prints two columns of entries on each page. Each column is
converted separately with `pdftotext -layout` (cropping each half of the page),
and the columns are read left, then right. A running header says which record
type the entries belong to ("Record Type: Person"). An entry looks like this:

    A_MARITL                      1 90                           (1:7)
    Marital status                            the label; may start "Item 18h - "
    Values: 1 = Married - civilian spouse present
            2 = Married - AF spouse present   a value that wraps continues on
            ...                               the next line, without " = "
    Universe: All Persons

Entries without codes give only a range, e.g. MARSUPWT "(0000000:9999999999)".

Returns {table: {NAME: entry}}, where entry has "length", "position", "range",
"label", "item" (e.g. "18h", when the label gives one), "values" [(code token,
label)], "universe", "page" and "column" ("left" or "right").
"""

import re
import subprocess
from pathlib import Path

HEADER = re.compile(r"^([A-Z][A-Z0-9_]*)\s+(\d+)\s+(\d+)\s+\(([^)]*)\)\s*$")
RECORD_TYPE = re.compile(r"Record Type:\s*(Household|Family|Person)", re.I)
VALUE = re.compile(r"^(?:Values:\s*)?(-?\d+(?:\s*[:\-]\s*-?\d+)?)\s*=\s*(.*)$")
ITEM = re.compile(r"^Item\s+(\S+?)\s*-\s*(.*)$")
NOISE = re.compile(r"^(Variable\s+Length\s+Position\s+Range|Record Type:.*|\d+|Page \d+.*)$")
TABLES = {"household": "household", "family": "family", "person": "person"}


def columns(pdf, cache_dir):
    """[(page, column, text)], converting each half page once and caching the text."""
    pdf = Path(pdf)
    cache = Path(cache_dir) / (pdf.stem + ".columns.txt")
    if not cache.exists():
        pages = int(re.search(r"Pages:\s+(\d+)", subprocess.run(
            ["pdfinfo", str(pdf)], capture_output=True, text=True, check=True).stdout).group(1))
        parts = []
        for p in range(1, pages + 1):
            for col, x in (("left", 0), ("right", 306)):
                out = subprocess.run(["pdftotext", "-layout", "-f", str(p), "-l", str(p), "-x", str(x), "-y", "0",
                                      "-W", "306", "-H", "792", str(pdf), "-"],
                                     capture_output=True, text=True, check=True).stdout
                parts.append(f"\f@@ page {p} {col}\n{out}")
        cache.write_text("".join(parts))
    out, page, col, lines = [], None, None, []
    for line in cache.read_text().splitlines():
        m = re.match(r"^\f?@@ page (\d+) (left|right)$", line)
        if m:
            if page is not None:
                out.append((page, col, lines))
            page, col, lines = int(m.group(1)), m.group(2), []
        else:
            lines.append(line)
    if page is not None:
        out.append((page, col, lines))
    return out


def parse(pdf, cache_dir):
    tables, table, entry, field = {}, None, None, None
    for page, col, lines in columns(pdf, cache_dir):
        for raw in lines:
            line = raw.strip()
            rt = RECORD_TYPE.search(line)
            if rt:
                table = TABLES[rt.group(1).lower()]
                continue
            m = HEADER.match(line)
            if m and table:
                entry = {"length": int(m.group(2)), "position": int(m.group(3)), "range": m.group(4).strip(),
                         "label": "", "values": [], "universe": "", "page": page, "column": col}
                tables.setdefault(table, {})[m.group(1)] = entry
                field = "label"
                continue
            if entry is None or not line or NOISE.match(line):
                if not line and field == "universe":
                    field = None
                continue
            if line.startswith("Universe:"):
                entry["universe"], field = line[len("Universe:"):].strip(), "universe"
                continue
            v = VALUE.match(line)
            if v and (line.startswith("Values:") or field == "values"):
                entry["values"].append([v.group(1).replace(" ", ""), v.group(2).strip()])
                field = "values"
                continue
            if line.startswith("Values:"):
                field = "values"
                rest = line[len("Values:"):].strip()
                if rest:
                    entry["values"].append([None, rest])  # a value range described in words
                continue
            if field == "label":
                entry["label"] = (entry["label"] + " " + line).strip()
            elif field == "values" and (re.match(r"^[A-Za-z][^=]*\s=\s", line)
                                        or re.match(r"^(negative|positive|dollar)\b.*\bam(oun)?t\b", line, re.I)):
                entry["values"].append([None, line])  # a value described in words: "negative amt = income (loss)"
            elif field == "values" and entry["values"]:
                entry["values"][-1][1] += " " + line
            elif field == "universe":
                entry["universe"] += " " + line
    for t in tables.values():
        for e in t.values():
            m = ITEM.match(e["label"])
            if m:
                e["item"], e["label"] = m.group(1), m.group(2)
    return tables
