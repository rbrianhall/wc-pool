#!/usr/bin/env python3
"""Import pool entries from Pete's standings workbook into data.json + index.html.

Usage:
    python3 scripts/import_entries.py "World Cup Standings 2026 (3).xlsx" [--dry-run] [--force]

Reads the Standings sheet (Entrant, Team 1-5, PAID) and the Countries sheet
(pick counts used as a cross-check), prints a diff of added/changed/removed
entries, then rewrites the "entries" block in both data.json and the embedded
DEFAULT_DATA in index.html. Stdlib only. --dry-run prints the diff without
writing; --force writes even if the pick-count cross-check fails.
"""
import json
import re
import sys
import unicodedata
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_PATH = ROOT / "data.json"
INDEX_PATH = ROOT / "index.html"

M = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
R_ID = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id"

# Spelling variants seen in the workbook that don't match data.json team names
# even after accent/punctuation normalization.
ALIASES = {
    "turkey": "TUR",
    "turkiye": "TUR",
    "czech republic": "CZE",
    "ivory coast": "CIV",
    "bosnia and herzegovina": "BIH",
    "bosnia herzegovina": "BIH",
    "united states": "USA",
    "south korea": "KOR",
    "korea": "KOR",
    "korea republic": "KOR",
    "cape verde": "CPV",
    "dr congo": "COD",
    "congo": "COD",
    "congo dr": "COD",
    "drc": "COD",
    "holland": "NED",
}


def norm(name):
    """lowercase, strip accents and punctuation: 'Côte d'Ivoire' -> 'cote divoire'"""
    s = unicodedata.normalize("NFKD", name)
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = re.sub(r"[^a-z ]", "", s.lower())
    return re.sub(r"\s+", " ", s).strip()


def read_workbook(path):
    """Return {sheet_name: [{colnum: value}]} for all sheets, stdlib only."""
    z = zipfile.ZipFile(path)
    wb = ET.fromstring(z.read("xl/workbook.xml"))
    rels = {r.get("Id"): r.get("Target") for r in ET.fromstring(z.read("xl/_rels/workbook.xml.rels"))}
    shared = []
    if "xl/sharedStrings.xml" in z.namelist():
        for si in ET.fromstring(z.read("xl/sharedStrings.xml")).findall(f"{M}si"):
            shared.append("".join(t.text or "" for t in si.iter(f"{M}t")))

    def cell_value(c):
        v = c.find(f"{M}v")
        if v is None:
            inline = c.find(f"{M}is")
            return "".join(t.text or "" for t in inline.iter(f"{M}t")) if inline is not None else None
        return shared[int(v.text)] if c.get("t") == "s" else v.text

    def col_num(ref):
        n = 0
        for ch in re.match(r"[A-Z]+", ref).group(0):
            n = n * 26 + ord(ch) - 64
        return n

    sheets = {}
    for s in wb.find(f"{M}sheets"):
        root = ET.fromstring(z.read("xl/" + rels[s.get(R_ID)].lstrip("/")))
        rows = []
        for row in root.iter(f"{M}row"):
            cells = {col_num(c.get("r")): cell_value(c) for c in row.findall(f"{M}c")}
            rows.append(cells)
        sheets[s.get("name")] = rows
    return sheets


def team_code(raw, code_by_name, problems):
    key = norm(raw)
    code = code_by_name.get(key) or ALIASES.get(key)
    if code is None:
        problems.append(f"unmapped team name: {raw!r}")
    return code


def parse_standings(rows, code_by_name):
    """Rows after the 'Entrant' header -> [{name, picks, paid?}]; collects problems."""
    entries, problems = [], []
    header_seen = False
    for row in rows:
        name = (row.get(1) or "").strip()
        if not header_seen:
            header_seen = name == "Entrant"
            continue
        if not name:
            continue
        raw_picks = [(row.get(col) or "").strip() for col in (3, 5, 7, 9, 11)]
        if not any(raw_picks):
            continue
        if not all(raw_picks):
            problems.append(f"{name}: expected 5 picks, got {[p for p in raw_picks if p]}")
            continue
        picks = [team_code(p, code_by_name, problems) for p in raw_picks]
        if None in picks:
            continue
        entry = {"name": name, "picks": picks}
        if (row.get(16) or "").strip().upper() == "N":
            entry["paid"] = False
        entries.append(entry)
    return entries, problems


def cross_check(entries, countries_rows, code_by_name, teams):
    """Compare per-team pick counts (and multiples) against the Countries sheet."""
    mismatches, problems = [], []
    counts = {}
    for e in entries:
        for c in e["picks"]:
            counts[c] = counts.get(c, 0) + 1
    multiple_by_code = {t[0]: t[3] for t in teams}
    header_seen = False
    for row in countries_rows:
        name = (row.get(1) or "").strip()
        if not header_seen:
            header_seen = name == "Country"
            continue
        if not name:
            continue
        code = team_code(name, code_by_name, problems)
        if code is None:
            continue
        sheet_count = int(float(row.get(3) or 0))
        if sheet_count != counts.get(code, 0):
            mismatches.append(f"{code}: sheet says {sheet_count} entries, parsed {counts.get(code, 0)}")
        sheet_mult = float(row.get(2) or 0)
        if abs(sheet_mult - multiple_by_code.get(code, -1)) > 0.005:
            mismatches.append(f"{code}: sheet multiple {sheet_mult}, data.json has {multiple_by_code.get(code)}")
    return mismatches, problems


def entry_line(e):
    """One DEFAULT_DATA line, matching the existing style/key order."""
    out = {"name": e["name"]}
    if e.get("ai"):
        out["ai"] = True
    out["picks"] = e["picks"]
    if e.get("paid") is False:
        out["paid"] = False
    return json.dumps(out, ensure_ascii=False)


def print_diff(old_entries, new_entries):
    old_by_name = {e["name"]: e for e in old_entries}
    new_by_name = {e["name"]: e for e in new_entries}
    changed = False
    for name in sorted(new_by_name.keys() - old_by_name.keys(), key=str.lower):
        print(f"  + added   {entry_line(new_by_name[name])}")
        changed = True
    for name in sorted(old_by_name.keys() - new_by_name.keys(), key=str.lower):
        print(f"  - removed {entry_line(old_by_name[name])}")
        changed = True
    for name in sorted(new_by_name.keys() & old_by_name.keys(), key=str.lower):
        if entry_line(old_by_name[name]) != entry_line(new_by_name[name]):
            print(f"  ~ changed {entry_line(old_by_name[name])}")
            print(f"        ->  {entry_line(new_by_name[name])}")
            changed = True
    if not changed:
        print("  (no changes)")
    return changed


def update_index_html(new_entries):
    html = INDEX_PATH.read_text(encoding="utf-8")
    lines = ",\n".join("    " + entry_line(e) for e in new_entries)
    block = f"  entries: [\n{lines}\n  ],"
    new_html, n = re.subn(r"^  entries: \[\n.*?\n  \],$", block, html, count=1, flags=re.S | re.M)
    if n != 1:
        sys.exit("ERROR: could not locate the entries block in index.html DEFAULT_DATA")
    INDEX_PATH.write_text(new_html, encoding="utf-8")


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    flags = {a for a in sys.argv[1:] if a.startswith("--")}
    if len(args) != 1:
        sys.exit(__doc__)
    xlsx = Path(args[0])
    if not xlsx.exists():
        sys.exit(f"ERROR: {xlsx} not found")

    data = json.loads(DATA_PATH.read_text(encoding="utf-8"))
    code_by_name = {norm(t[1]): t[0] for t in data["teams"]}
    sheets = read_workbook(xlsx)
    for required in ("Standings", "Countries"):
        if required not in sheets:
            sys.exit(f"ERROR: workbook has no '{required}' sheet (found: {list(sheets)})")

    entries, problems = parse_standings(sheets["Standings"], code_by_name)
    ai_names = {e["name"] for e in data["entries"] if e.get("ai")}
    for e in entries:
        if e["name"] in ai_names:
            e["ai"] = True
    entries.sort(key=lambda e: e["name"].lower())

    if problems:
        print("Parse problems:")
        for p in problems:
            print(f"  ! {p}")
        sys.exit("ERROR: fix the problems above (add an alias or correct the sheet) and re-run")

    mismatches, problems = cross_check(entries, sheets["Countries"], code_by_name, data["teams"])
    for p in problems:
        print(f"  ! Countries sheet: {p}")
    if mismatches:
        print("Cross-check against Countries sheet FAILED:")
        for mm in mismatches:
            print(f"  ! {mm}")
        if "--force" not in flags:
            sys.exit("ERROR: cross-check failed (use --force to write anyway)")
    else:
        print(f"Cross-check OK: pick counts and multiples match the Countries sheet")

    paid = sum(1 for e in entries if e.get("paid") is not False)
    print(f"Parsed {len(entries)} entries ({paid} paid -> pot estimate ${paid * data['meta']['entryFeeUSD']})")
    print(f"Diff vs data.json ({len(data['entries'])} entries):")
    changed = print_diff(data["entries"], entries)

    if "--dry-run" in flags:
        print("Dry run: nothing written")
        return
    if not changed:
        print("Nothing to write")
        return

    data["entries"] = entries
    DATA_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    update_index_html(entries)
    print(f"Wrote {DATA_PATH.name} and synced DEFAULT_DATA in {INDEX_PATH.name}")


if __name__ == "__main__":
    main()
