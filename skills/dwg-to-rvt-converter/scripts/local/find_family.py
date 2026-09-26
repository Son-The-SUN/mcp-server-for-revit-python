"""Find and load default library, step 1: search the Autodesk content library for families.

    python find_family.py "awning window" ["double glass door" ...] [--lib <folder>] [--category Windows]
        [--top 8] [--types] [--size 700x700] [--json out.json]

- --lib: default = the newest C:\\ProgramData\\Autodesk\\RVT <year>\\Libraries\\English\\Australia
  (pass --year 2025 to pin the Revit version the model is in).
- Scores file names against the query words (with synonyms: slider/sliding, awning/top hung, 2 leaf/double...),
  prefers the "(AUS)" variants and the category folder when given.
- --types lists the type catalog (.txt next to the .rfa) of each hit; --size WxH picks the nearest types.
No third-party packages. Load the chosen files with scripts/revit/load_family.py.
"""
import argparse
import csv
import glob
import io
import json
import os
import re

SYN = {
    "slider": "sliding", "slide": "sliding", "sliding": "sliding",
    "awning": "awning", "tophung": "awning", "top-hung": "awning", "hopper": "hopper",
    "swing": "", "hinged": "", "door": "door", "doors": "door", "window": "window", "windows": "window",
    "double": "double", "2leaf": "double", "two": "double", "single": "single", "1leaf": "single",
    "glass": "glass", "glazed": "glass", "fixed": "fixed", "casement": "casement",
    "wc": "toilet", "toilet": "toilet", "pan": "toilet", "basin": "basin", "lavatory": "basin", "sink": "sink",
    "table": "table", "chair": "chair", "column": "column", "stair": "stair",
}
CAT_HINT = {"door": "Doors", "window": "Windows", "toilet": "Plumbing", "basin": "Plumbing", "sink": "Plumbing",
            "table": "Furniture", "chair": "Furniture", "column": "Columns"}


def default_lib(year=None):
    roots = sorted(glob.glob(r"C:\ProgramData\Autodesk\RVT *\Libraries\English\Australia"))
    if year:
        roots = [r for r in roots if "RVT {}".format(year) in r] or roots
    return roots[-1] if roots else None


def tokens(s):
    s = s.lower().replace("(aus)", " aus ")
    return [SYN.get(t, t) for t in re.findall(r"[a-z]+|\d+", s)]


def index(lib):
    out = []
    for root, _, files in os.walk(lib):
        for f in files:
            if f.lower().endswith(".rfa"):
                p = os.path.join(root, f)
                rel = os.path.relpath(p, lib)
                out.append({"path": p, "name": f[:-4], "category": rel.split(os.sep)[0], "folder": os.path.dirname(rel),
                            "catalog": os.path.isfile(p[:-4] + ".txt")})
    return out


def score(fam, q, category=None):
    ft = set(tokens(fam["name"]) + tokens(fam["folder"]))
    qt = [t for t in tokens(q) if t]
    hit = sum(1.0 for t in qt if t in ft)
    if not qt or hit == 0:
        return 0.0
    s = hit / len(qt)
    if "aus" in ft:
        s += 0.15
    cat = category or next((CAT_HINT[t] for t in qt if t in CAT_HINT), None)
    if cat and fam["category"].lower().startswith(cat.lower()):
        s += 0.3
    s -= 0.02 * max(0, len(ft) - len(qt))  # prefer plain names
    return s


def catalog(path):
    """Type catalog rows: [{"name":..., "Width": mm, ...}] (lengths converted to mm)."""
    txt = path[:-4] + ".txt"
    if not os.path.isfile(txt):
        return []
    raw = open(txt, "rb").read()
    for enc in ("utf-16", "utf-8-sig", "cp1252"):
        try:
            data = raw.decode(enc)
            break
        except Exception:
            continue
    rows = list(csv.reader(io.StringIO(data)))
    if not rows:
        return []
    head = rows[0][1:]
    cols = []
    for h in head:
        parts = h.split("##")
        cols.append((parts[0], parts[2].upper() if len(parts) > 2 else ""))
    out = []
    for r in rows[1:]:
        if not r:
            continue
        d = {"name": r[0]}
        for (n, unit), v in zip(cols, r[1:]):
            try:
                x = float(v)
                x = x * 304.8 if unit == "FEET" else x * 25.4 if unit == "INCHES" else x * 10 if unit == "CENTIMETERS" else x * 1000 if unit == "METERS" else x
                d[n] = round(x, 1)
            except ValueError:
                d[n] = v
        out.append(d)
    return out


def nearest_types(rows, size):
    w, h = [float(c) for c in size.lower().split("x")]
    def dist(r):
        rw = r.get("Width") or r.get("Rough Width") or 0
        rh = r.get("Height") or r.get("Rough Height") or 0
        return abs(rw - w) + abs(rh - h)
    return sorted(rows, key=dist)[:3]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("query", nargs="+")
    ap.add_argument("--lib", default=None)
    ap.add_argument("--year", default=None)
    ap.add_argument("--category", default=None)
    ap.add_argument("--top", type=int, default=6)
    ap.add_argument("--types", action="store_true")
    ap.add_argument("--size", default=None)
    ap.add_argument("--json", default=None)
    a = ap.parse_args()
    lib = a.lib or default_lib(a.year)
    if not lib or not os.path.isdir(lib):
        raise SystemExit("library folder not found; pass --lib")
    fams = index(lib)
    print("library: {} ({} families)".format(lib, len(fams)))
    res = {}
    for q in a.query:
        hits = sorted(((score(f, q, a.category), f) for f in fams), key=lambda t: -t[0])
        hits = [(s, f) for s, f in hits if s > 0][:a.top]
        print("\n'{}':".format(q))
        res[q] = []
        for s, f in hits:
            item = dict(f, score=round(s, 2))
            line = "  {:.2f}  {:<12} {}{}".format(s, f["category"][:12], f["name"], "  [catalog]" if f["catalog"] else "")
            print(line)
            if f["catalog"] and (a.types or a.size):
                rows = catalog(f["path"])
                item["types"] = nearest_types(rows, a.size) if a.size else rows[:20]
                for r in item["types"][:8 if not a.size else 3]:
                    print("        type {}  W{} H{}".format(r["name"], r.get("Width", "?"), r.get("Height", "?")))
            res[q].append(item)
    if a.json:
        with open(a.json, "w", encoding="utf-8") as f:
            json.dump(res, f, indent=1)


if __name__ == "__main__":
    main()
