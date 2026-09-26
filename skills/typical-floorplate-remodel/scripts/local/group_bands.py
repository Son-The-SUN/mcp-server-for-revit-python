"""Level range over which each group category of a reference floor repeats unchanged (for REP group names).

    python group_bands.py L10 --ref-level "LEVEL 10" [--prefix LEVEL] [--tol 30]

Inputs: groups_<LV>.json (classify_groups.py), ifc_<LV>.json (extract_view.py), ifc_all.json (extract_all.py).
For every IFC level it checks, per category, that (a) every element of the reference floor's category has a twin
on that level (same category, layer, width, bbox within --tol mm with z taken relative to each level) and (b) the
level has no unmatched extra element nearest to that category. The band is the contiguous run of such levels
around the reference level. Prints a table and writes bands_<LV>.json {category: [first level, last level]}.
"""
import argparse
import json
import math
from collections import defaultdict

ap = argparse.ArgumentParser()
ap.add_argument("lv")
ap.add_argument("--ref-level", required=True)
ap.add_argument("--prefix", default="LEVEL")
ap.add_argument("--tol", type=float, default=30.0, help="wall bbox tolerance, mm")
ap.add_argument("--tol-openings", type=float, default=400.0, help="door/window bbox tolerance, mm (3D leaves open at varying angles)")
args = ap.parse_args()

G = json.load(open("groups_%s.json" % args.lv))
D = json.load(open("ifc_%s.json" % args.lv))
A = json.load(open("ifc_all.json"))
lz = D["meta"]["level_z"]
catmap = {}
for key in ("walls", "doors", "windows"):
    for k, v in G[key].items():
        if not k.startswith("host-for"):
            catmap[k.split("-")[0]] = v
def unreliable(cat, h, b):
    """Wireframe IFC walls (edge curves, few or no solids) get an inflated Revit bounding box, and by a different
    amount on every level (tower C: a 2800 wall read -1824..4200 on L6 and 1426..7450 on L8), so their twins never
    match. Such walls (box taller than the IFC height + 300) never decide a band."""
    return cat == "Walls" and h and (b[5] - b[2]) > h + 300


ref = []
for r in D["rows"]:
    b = r.get("bb")
    if r["cat"] not in ("Walls", "Doors", "Windows") or not b or not (b[2] - lz >= -800 and b[2] - lz <= 1500 and b[5] - lz > 300):
        continue
    # elements left out of the build (screens, duplicates) still match, but never decide a band
    c = catmap.get(str(r["id"]), "excluded")
    if unreliable(r["cat"], r["Height"], b):
        c = "excluded"
    ref.append((c, r["cat"], r["IfcPresentationLayer"], r["Width"], [b[0], b[1], b[2] - lz, b[3], b[4], b[5] - lz]))
levels = sorted([l for l in A["levels"] if l[0].startswith(args.prefix)], key=lambda l: l[1])
by_level = defaultdict(list)
for eid, cat, layer, w, h, sc, b in A["rows"]:
    if unreliable(cat, h, b):
        continue
    for name, z in levels:
        if z - 800 <= b[2] <= z + 1500 and b[5] > z + 300:
            by_level[name].append((cat, layer, w, [b[0], b[1], b[2] - z, b[3], b[4], b[5] - z]))
            break


def match(x, y):
    tol = args.tol if x[0] == "Walls" else args.tol_openings
    return x[0] == y[1] and x[1] == y[2] and abs((x[2] or 0) - (y[3] or 0)) < 1 and all(abs(x[3][i] - y[4][i]) < tol for i in range(6))


cats = sorted(set(c for c, *_ in ref if c != "excluded"))
table = {}
for name, z in levels:
    els = by_level[name]
    used = set()
    missing = defaultdict(int)
    for rr in ref:
        hit = next((j for j, e in enumerate(els) if j not in used and match(e, rr)), None)
        if hit is None:
            if rr[0] != "excluded":
                missing[rr[0]] += 1
        else:
            used.add(hit)
    extra = defaultdict(int)
    for j, e in enumerate(els):
        if j in used:
            continue
        cx, cy = (e[3][0] + e[3][3]) / 2, (e[3][1] + e[3][4]) / 2
        near = min(ref, key=lambda rr: math.hypot((rr[4][0] + rr[4][3]) / 2 - cx, (rr[4][1] + rr[4][4]) / 2 - cy))
        if near[0] != "excluded":
            extra[near[0]] += 1
    table[name] = dict((c, (missing[c], extra[c])) for c in cats)

print("%-16s" % "level" + "".join("%16s" % c[:15] for c in cats) + "    (missing/extra)")
for name, z in levels:
    print("%-16s" % name[:16] + "".join("%16s" % ("ok" if table[name][c] == (0, 0) else "%d/%d" % table[name][c]) for c in cats))
bands = {}
names = [l[0] for l in levels]
i0 = names.index(args.ref_level)
for c in cats:
    lo = hi = i0
    while lo - 1 >= 0 and table[names[lo - 1]][c] == (0, 0):
        lo -= 1
    while hi + 1 < len(names) and table[names[hi + 1]][c] == (0, 0):
        hi += 1
    also = [n for k, n in enumerate(names) if table[n][c] == (0, 0) and not lo <= k <= hi]
    bands[c] = {"from": names[lo], "to": names[hi], "also_identical": also}
    print("%-14s band %s .. %s%s" % (c, names[lo], names[hi], ("   also identical: " + ", ".join(also)) if also else ""))
json.dump(bands, open("bands_%s.json" % args.lv, "w"), indent=1)
