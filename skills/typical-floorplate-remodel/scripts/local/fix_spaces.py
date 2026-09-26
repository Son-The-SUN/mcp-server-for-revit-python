"""List the IFC spaces of a level and drop the ones that would mislead the unit logic.

    python fix_spaces.py L7                                   # list: index, name, area, bbox, aspect
    python fix_spaces.py L7 --drop "C0-04.02@200,-3000"       # drop the space of that name containing the point

Reads/writes ifc_spaces.json (scripts/revit/extract_spaces.py) in the working directory. A dropped space is moved to
"<key>_dropped" so the change stays visible and reversible.

Why: on the second job (KSCW tower C) the corridor IfcSpace carried a unit number (C0-04.02, the same name as the
unit next to it on L7). plan_build.py and classify_groups.py would take it for a unit: corridor walls on that unit's
side became "Unit-02" and entry doors lost their exterior side. With the corridor space dropped, the corridor is
"no space" - the case the corridor/core rules were written for (unit + nothing = corridor wall, Intertenancy).
Don't rename it to a non-unit name: non-unit spaces (balconies) make a wall Facade.
"""
import argparse
import json

ap = argparse.ArgumentParser()
ap.add_argument("key")
ap.add_argument("--drop", action="append", default=[], help="NAME@x,y (host mm)")
args = ap.parse_args()


def pip(x, y, poly):
    inside = False
    for i in range(len(poly)):
        x1, y1 = poly[i][0], poly[i][1]
        x2, y2 = poly[(i + 1) % len(poly)][0], poly[(i + 1) % len(poly)][1]
        if (y1 > y) != (y2 > y) and x < x1 + (y - y1) * (x2 - x1) / (y2 - y1):
            inside = not inside
    return inside


def area(lp):
    return abs(sum(lp[i][0] * lp[(i + 1) % len(lp)][1] - lp[(i + 1) % len(lp)][0] * lp[i][1] for i in range(len(lp)))) / 2e6


S = json.load(open("ifc_spaces.json"))
sp = S[args.key]
for spec in args.drop:
    name, pt = spec.split("@")
    x, y = [float(v) for v in pt.split(",")]
    hit = [s for s in sp if s["name"] == name and any(pip(x, y, lp) for lp in s["loops"])]
    if not hit:
        raise SystemExit("no space %s contains %s" % (name, pt))
    for s in hit:
        sp.remove(s)
        S.setdefault(args.key + "_dropped", []).append(s)
        print("dropped", name, "at", pt)
if args.drop:
    json.dump(S, open("ifc_spaces.json", "w"))

for i, s in enumerate(sp):
    pts = [p for lp in s["loops"] for p in lp]
    xs, ys = [p[0] for p in pts], [p[1] for p in pts]
    w, h = max(xs) - min(xs), max(ys) - min(ys)
    print("%2d %-10s %6.1f m2  x %7.0f..%7.0f  y %7.0f..%7.0f  aspect %4.1f%s" % (
        i, s["name"], sum(area(lp) for lp in s["loops"]), min(xs), max(xs), min(ys), max(ys),
        max(w, h) / max(1.0, min(w, h)), "  (bbox fallback)" if s.get("approx") else ""))
