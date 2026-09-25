"""Which levels share each reference floor's layout, per IFC layer - drives group level ranges.

    python typical_bands.py --refs "LEVEL 5" "LEVEL 10" [--prefix LEVEL] [--grid 50]

Reads ifc_all.json (scripts/revit/extract_all.py). For every IFC level whose name starts with --prefix, collects
the walls/doors/windows that start on it (base within -800..+200 mm, top > +300 mm) and compares them with each
reference level, per category+layer, as a Jaccard similarity of rounded bounding boxes (1.00 = identical).

On the first job: L6-L24 all scored 1.00 against LEVEL 10 (L17/L25 variants) while LEVEL 5 matched nothing
- so the "Level 5 typical floor" was really a one-off.
"""
import argparse
import json
from collections import defaultdict

ap = argparse.ArgumentParser()
ap.add_argument("--refs", nargs="+", required=True, help="reference level names, e.g. 'LEVEL 5' 'LEVEL 10'")
ap.add_argument("--prefix", default="LEVEL", help="only compare levels whose name starts with this")
ap.add_argument("--grid", type=float, default=50.0, help="bounding-box rounding in mm")
args = ap.parse_args()

D = json.load(open("ifc_all.json"))
levels = sorted([l for l in D["levels"] if l[0].startswith(args.prefix)], key=lambda l: l[1])


def key_of(cat, layer):
    short = (layer or "-").replace("\\", "/")
    return "%s:%s" % (cat[:4], short[:18])


sig = defaultdict(lambda: defaultdict(set))
for eid, cat, layer, w, h, sc, b in D["rows"]:
    for name, z in levels:
        if z - 800 <= b[2] <= z + 200 and b[5] > z + 300:
            g = args.grid
            sig[name][key_of(cat, layer)].add((round(b[0] / g), round(b[1] / g), round(b[3] / g), round(b[4] / g), round((b[5] - z) / g), w))
            break
groups = sorted(set(k for lv in sig.values() for k in lv))


def sim(a, b):
    return 1.0 if not a and not b else len(a & b) / float(len(a | b))


for ref in args.refs:
    print("\n=== similarity to %s (1.00 = identical)" % ref)
    print("%-14s" % "level" + "".join("%9s" % g.split(":")[1][:8] for g in groups) + "   n")
    print("%-14s" % "" + "".join("%9s" % g.split(":")[0] for g in groups))
    for name, z in levels:
        row = "".join("%9.2f" % sim(sig[name][g], sig[ref][g]) for g in groups)
        print("%-14s" % name[:14] + row + "   %d" % sum(len(sig[name][g]) for g in groups))
