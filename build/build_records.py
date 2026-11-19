"""Build the CPS ASEC slice's metadata records from Census's data dictionaries and CSV files.

Usage:
    python build/build_records.py      (make build sets PYTHONPATH for pdt)

Reads build/slice.yaml; for each release (ASEC year), the data dictionary PDF
(build/census_dictionary.py) and the CSV zip in data/raw/<year>/. Writes
metadata/releases/<year>/<table>/variables.jsonl, metadata/links.jsonl and
build/report.md.

Where each field comes from (STANDARD.md §3):
- label, universe, codes and labels, valid ranges, notes: the data dictionary
- storage type, which codes occur, availability counts: the CSV file
- measure: build/slice.yaml (hand)
- which codes are missing, and why: rule cps_missing_from_labels, because the
  dictionary doesn't mark missing codes
- links between consecutive years: rule cps_identical_dictionary

How a dictionary entry becomes values (rule cps_dictionary_values):
- "1 = Male": a code, valid unless the missing rule types it;
- "00-79 = 0-79 years of age", "1 hour ... 99 = 99 hours plus", a pair of codes
  labeled "Min Value" and "Max Value", or a value described in words
  ("negative amt = income (loss)"): part of the valid range, whose note keeps
  the labels verbatim. The range's ends come from the entry's (min:max), less
  any missing code at an end.
"""

import collections
import json
import re
import sys
from pathlib import Path

import yaml

import census_dictionary

try:
    from pdt.derived import scale_signatures
    from pdt.readers import read_csv_zip, zip_member
except ImportError:
    import os
    sys.path.insert(0, str(Path(os.environ.get("PDT", Path(__file__).resolve().parents[2] / "PublicDataToolkit")) / "lib"))
    from pdt.derived import scale_signatures
    from pdt.readers import read_csv_zip, zip_member

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "data" / "raw"
CACHE = ROOT / "data" / "interim" / "dictionary"
META = ROOT / "metadata"
DATASET = "cps-asec"

RULE_VERSION = "0.1"
LINK_RULE, LINK_RULE_VERSION = "cps_identical_dictionary", "0.1"


def dictionary_source(release):
    return f"cps-asec-{release}-dictionary"


def data_source(release):
    return f"cps-asec-{release}-csv"


# ------------------------------------------------------------------ rules

# Rule cps_missing_from_labels: the dictionary doesn't mark missing codes; a code
# is missing when its label says it's out of the universe, or the household wasn't
# interviewed. Labels such as "Children" (A_HGA 0) or "No Parent 1 present" are
# answers, and stay valid.
MISSING = [(re.compile(r"^(niu|not in universe|not in poverty universe)\b", re.I), ["inap"]),
           (re.compile(r"^noninterview household\b", re.I), ["not_asked"])]


def missing_types(label):
    for pattern, types in MISSING:
        if pattern.match(label.strip()):
            return types
    return None


def number(token):
    x = float(token.replace(",", ""))
    return int(x) if x.is_integer() else x


RANGE = re.compile(r"^(-?[\d,]+)\s*[-:]\s*(-?[\d,]+)$")
RANGE_IN_LABEL = re.compile(r"\s+(?=-?[\d,]+(?:\s*-\s*[\d,]+)?\s=\s)")
ELLIPSIS = re.compile(r"^(.*?)\s*\.\.\.\s*(-?\d+)\s*=\s*(.*)$")


def value_rows(entry):
    """[(code token or None, label)]: the dictionary's Values, with a range the PDF prints on the same
    line as a code split off ("-1 = Not in poverty universe 1-99,999 = dollar amount")."""
    rows = []
    for token, label in entry["values"]:
        parts = (RANGE_IN_LABEL.split(label.strip()) if token and not ELLIPSIS.match(label.strip())
                 else [label.strip()])
        rows.append((token, parts[0]))
        for extra in parts[1:]:
            t, _, lab = extra.partition(" = ")
            rows.append((t.strip(), lab.strip()))
    return rows


def dictionary_values(name, entry, source):
    """(values, valid_range or None, notes) from a dictionary entry (rule cps_dictionary_values)."""
    rows = value_rows(entry)
    values, ranges, described, notes = [], [], [], []
    minmax = {}
    for token, label in rows:
        if token is None:
            if re.search(r"implied decimal", label, re.I):
                notes.append(label)
            elif not RANGE.match(label.replace(" ", "")):
                described.append(label)
            else:
                lo, hi = RANGE.match(label.replace(" ", "")).groups()
                ranges.append((number(lo), number(hi), label))
            continue
        m = RANGE.match(token)
        if m:
            ranges.append((number(m.group(1)), number(m.group(2)), f"{token}: {label}"))
            continue
        code = number(token)
        if label.strip().lower() in {"min value", "max value"}:
            minmax[label.strip().lower()] = code
            continue
        e = ELLIPSIS.match(label)
        if e:  # "1 = 1 hour ... 99 = 99 hours plus": a range, and its two labeled ends are values too
            ranges.append((code, number(e.group(2)), f"{token}: {e.group(1)} ... {e.group(2)}: {e.group(3)}"))
            values.append({"code": code, "label": e.group(1).strip(), "kind": "valid"})
            values.append({"code": number(e.group(2)), "label": e.group(3).strip(), "kind": "valid"})
            continue
        types = missing_types(label)
        if types:
            values.append({"code": code, "label": label, "kind": "missing", "missing_types": types,
                           "origin": {"origin": "rule", "rule": "cps_missing_from_labels", "rule_version": RULE_VERSION,
                                      "source": source,
                                      "note": "The dictionary doesn't mark missing codes; kind and type from the label."}})
        else:
            values.append({"code": code, "label": label, "kind": "valid"})
    if len(minmax) == 2:
        ranges.append((minmax["min value"], minmax["max value"],
                       f"{minmax['min value']}: Min Value; {minmax['max value']}: Max Value"))
    valid_range = None
    if ranges or described:
        bounds = RANGE.match(entry["range"].replace(" ", ""))
        missing_codes = {v["code"] for v in values if v["kind"] == "missing"}
        singles = [v["code"] for v in values if v["kind"] == "valid" and isinstance(v["code"], (int, float))]
        lows = [r[0] for r in ranges] + singles
        highs = [r[1] for r in ranges] + singles
        if described and bounds:  # "negative amt = income (loss)": the entry's range gives the ends
            lo, hi = number(bounds.group(1)), number(bounds.group(2))
            while lo in missing_codes and lo < hi:  # (0:60000) with 0 = NIU: amounts start at 1
                lo += 1
            lows.append(lo)
            highs.append(hi)
        note = "; ".join(p.rstrip("; ") for p in [r[2] for r in ranges] + described)
        valid_range = {"min": min(lows), "max": max(highs),
                       "note": (note + ". " if note else "") + f"From the data dictionary, range ({entry['range']})."}
    elif not values and RANGE.match(entry["range"].replace(" ", "")):
        lo, hi = RANGE.match(entry["range"].replace(" ", "")).groups()
        valid_range = {"min": number(lo), "max": number(hi), "note": f"From the data dictionary, range ({entry['range']})."}
    return values, valid_range, notes


# ---------------------------------------------------------------- build

SAME_AS = re.compile(r"^Same as (\w+)$", re.I)
FLAG_FOR = re.compile(r"Allocation flag for (\w+)\b")
IMPLIED = re.compile(r"(\d+) implied decimal", re.I)


def build_variable(name, table, release, entry, col, measure, spec, table_entries, table_names):
    """One record. table_entries: this release's dictionary entries for the table; table_names: the
    slice's names in the table (flag_for names only those)."""
    dd, dat = dictionary_source(release), data_source(release)
    hand = {"origin": "hand", "author": spec["author"], "date": spec["date"]}
    record = {"dataset": DATASET, "release": release, "table": table, "name": name}
    if entry["label"]:
        record["label"] = entry["label"]
    same = SAME_AS.match(entry["values"][0][1]) if len(entry["values"]) == 1 and entry["values"][0][0] is None else None
    if same and same.group(1) in table_entries:  # PXPAR2: "Same as PXPAR1"
        entry = dict(entry, values=table_entries[same.group(1)]["values"])
    present = col.dropna()
    numbers = [float(x) for x in present]
    if numbers:
        record["storage_type"] = "integer" if all(x.is_integer() for x in numbers) else "float"
    record["measure"] = measure
    values, valid_range, notes = dictionary_values(name, entry, dd)
    if entry["universe"]:
        record["universe"] = entry["universe"]
    if notes:
        record["notes"] = "\n".join(notes)
    if values:
        record["values"] = values
    if valid_range:
        record["valid_range"] = valid_range
    missing = {v["code"] for v in values if v["kind"] == "missing"}
    record["availability"] = [{"wave": release, "n": sum(1 for x in numbers if x not in missing)}]
    record["source_location"] = {"source": dd, "page": entry["page"], "anchor": name}
    fields = {"values": {"origin": "rule", "rule": "cps_dictionary_values", "rule_version": RULE_VERSION, "source": dd,
                         "note": "Codes and labels from the dictionary's Values; ranges and described values go in valid_range."},
              "availability": {"origin": "data_file", "source": dat, "note": "n counts the values that aren't missing codes."},
              "measure": hand}
    if "storage_type" in record:
        fields["storage_type"] = {"origin": "rule", "rule": "storage_type_from_values", "rule_version": RULE_VERSION,
                                  "source": dat, "note": "integer if every value in the data is whole, else float."}
    if valid_range:
        fields["valid_range"] = {"origin": "rule", "rule": "cps_dictionary_values", "rule_version": RULE_VERSION, "source": dd}
    if same:
        fields["values"]["note"] = f"The entry says its codes are the same as {same.group(1)}'s; they're copied from there."
    implied = next((IMPLIED.search(n) for n in notes if IMPLIED.search(n)), None)
    if implied:  # "2 implied decimals (example: 255212=2552.12)"
        record["implied_decimals"] = int(implied.group(1))
        fields["implied_decimals"] = {"origin": "publisher", "source": dd, "location": f"{name}, Values"}
    flag = FLAG_FOR.search(entry["label"])
    if flag and flag.group(1) in table_names:
        record["flag_for"] = [flag.group(1)]
        record["variable_type"] = "imputation_flag"
        for f in ("flag_for", "variable_type"):
            fields[f] = {"origin": "rule", "rule": "cps_flag_from_label", "rule_version": RULE_VERSION, "source": dd,
                         "note": "From the label, \"Allocation flag for <name>\"."}
    q = spec.get("questions", {}).get(name)
    if q:
        question = {"text": q["text"]}
        if entry.get("item"):
            question["number"] = entry["item"]
        record["questions"] = [question]
        fields["questions"] = {"origin": "publisher", "source": f"cps-asec-{release}-techdoc", "location": q["location"],
                               "note": "Quoted in the technical documentation; the item number is from the data dictionary's label."}
    record["provenance"] = {"record": {"origin": "publisher", "source": dd}, "fields": fields}
    return record


def check(record, col):
    """C4 for one record: codes in the data that the record doesn't cover, and empty cells."""
    out = []
    listed = {v["code"] for v in record.get("values", [])}
    vr = record.get("valid_range")
    counts = collections.Counter(float(x) for x in col.dropna())
    for code, n in sorted(counts.items()):
        c = int(code) if code.is_integer() else code
        if c not in listed and not (vr and vr["min"] <= c <= vr["max"]):
            out.append(f"`{record['table']}/{record['name']}` code {c}: occurs {n} times, not in the dictionary")
    if col.isna().any():
        out.append(f"`{record['table']}/{record['name']}`: {int(col.isna().sum())} empty cells in the CSV")
    return out


# ---------------------------------------------------------------- links

def signature(entry):
    return {"range": entry["range"].replace(" ", ""), "label": " ".join(entry["label"].lower().split()),
            "universe": " ".join(entry["universe"].lower().split()),
            "values": [(t, " ".join(l.lower().split())) for t, l in value_rows(entry)]}


def link(a_rel, b_rel, table, name, a, b):
    """Rule cps_identical_dictionary: same_item when the two dictionary entries match (range, label,
    universe, codes and their labels), ignoring case and spacing. Otherwise the relations that hold:
    codes_changed (a code added or dropped, or the range changed), labels_changed (the label, or the
    label of a code in both), universe_changed. Census has no crosswalk between ASEC years; it keeps a
    name for the same item, but doesn't say so."""
    sa, sb = signature(a), signature(b)
    if sa == sb:
        relations = ["same_item"]
    else:
        relations = []
        ca, cb = dict(sa["values"]), dict(sb["values"])
        if sa["range"] != sb["range"] or set(ca) != set(cb):
            relations.append("codes_changed")
        if sa["label"] != sb["label"] or any(ca[c] != cb[c] for c in set(ca) & set(cb)):
            relations.append("labels_changed")
        if sa["universe"] != sb["universe"]:
            relations.append("universe_changed")
    ref = lambda rel: {"release": rel, "table": table, "name": name}
    out = {"from": ref(a_rel), "to": ref(b_rel), "relations": relations,
           "origin": {"origin": "rule", "rule": LINK_RULE, "rule_version": LINK_RULE_VERSION,
                      "source": dictionary_source(b_rel),
                      "note": f"Compares the entries in the {a_rel} and {b_rel} data dictionaries "
                              f"({dictionary_source(a_rel)}, {dictionary_source(b_rel)}): range, label, universe, and codes "
                              "with their labels, ignoring case and spacing. Census doesn't assert this link."}}
    if relations != ["same_item"]:
        changes = []
        if sa["range"] != sb["range"]:
            changes.append(f"range ({a['range']}) -> ({b['range']})")
        if sa["values"] != sb["values"]:
            changes.append(f"values {a['values']} -> {b['values']}")
        if sa["label"] != sb["label"]:
            changes.append(f"label {a['label']!r} -> {b['label']!r}")
        if sa["universe"] != sb["universe"]:
            changes.append(f"universe {a['universe']!r} -> {b['universe']!r}")
        out["note"] = "; ".join(changes)
    return out


# ---------------------------------------------------------------- main

def main():
    spec = yaml.safe_load((ROOT / "build" / "slice.yaml").read_text())
    import os
    if os.environ.get("RELEASES"):  # a partial build, e.g. RELEASES=2024,2025
        spec["releases"] = [r for r in spec["releases"] if r in os.environ["RELEASES"].split(",")]
    CACHE.mkdir(parents=True, exist_ok=True)
    by_key, entries, report, summary = {}, {}, [], []
    for release in spec["releases"]:
        dd = census_dictionary.parse(RAW / release / spec["dictionary"][release], CACHE)
        notes = []
        for table, t in spec["tables"].items():
            names = list(t["variables"])
            zpath = RAW / release / f"asecpub{release[2:]}csv.zip"
            # 2019's zip keeps the CSVs in a folder (cpspb/asec/prod/data/2019/)
            member = zip_member(zpath, rf"(^|/){re.escape(t['member'].format(yy=release[2:]))}$")
            frame = read_csv_zip(zpath, member, names)
            records = []
            for name in names:
                entry = dd.get(table, {}).get(name)
                if entry is None:
                    notes.append(f"`{table}/{name}`: not in the {release} dictionary")
                    continue
                rec = build_variable(name, table, release, entry, frame[name], t["variables"][name], spec,
                                     dd.get(table, {}), names)
                notes += check(rec, frame[name])
                records.append(rec)
                by_key[(release, table, name)] = rec
                entries[(release, table, name)] = entry
            write_jsonl(META / "releases" / release / table / "variables.jsonl", records)
            # C3 for the slice: every name is in both the dictionary and the CSV (read_csv_zip fails otherwise).
            summary.append(f"- {release} `{table}`: {len(records)} variables, {len(frame):,} rows; "
                           f"the dictionary has {len(dd.get(table, {}))} {table} entries")
        report += [f"### {release}", "", *([f"- {n}" for n in notes] or ["None."]), ""]

    links, changed = [], []
    releases = spec["releases"]
    for a_rel, b_rel in zip(releases, releases[1:]):
        for table, t in spec["tables"].items():
            for name in t["variables"]:
                a, b = entries.get((a_rel, table, name)), entries.get((b_rel, table, name))
                if a and b:
                    lk = link(a_rel, b_rel, table, name, a, b)
                    links.append(lk)
                    if lk["relations"] != ["same_item"]:
                        changed.append(f"- `{table}/{name}` {a_rel} -> {b_rel}: {', '.join(lk['relations'])}; {lk['note']}")
    write_jsonl(META / "links.jsonl", links)
    write_jsonl(META / "derived" / "scale_signature.jsonl", scale_signatures(list(by_key.values())))

    types = collections.Counter(tp for r in by_key.values() for v in r.get("values", []) if v["kind"] == "missing"
                                for tp in v["missing_types"])
    lines = ["# Build report: cps-asec", "", "Generated by `build/build_records.py`. Do not edit.", "",
             f"- Releases: {', '.join(releases)}; {len(by_key)} records", *summary, "",
             "## Missing types assigned, by number of codes", "", "| Type | Codes |", "|---|---|",
             *[f"| `{tp}` | {k} |" for tp, k in sorted(types.items())], "",
             "## Values to look at, by release", "", *report,
             "## Links between consecutive releases", "",
             f"- {len(links)} links; {len(links) - len(changed)} same_item; {len(changed)} with changes:", "",
             *(changed or ["None."]), ""]
    (ROOT / "build" / "report.md").write_text("\n".join(lines))
    print(f"{len(by_key)} records, {len(links)} links; see build/report.md")


def write_jsonl(path, records):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


if __name__ == "__main__":
    main()
