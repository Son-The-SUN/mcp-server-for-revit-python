"""Check each IFC storey's elevation against its own geometry, and correct ifc_all.json when they disagree.

    python storey_levels.py [--apply] [--min-walls 20]

Reads ifc_all.json (scripts/revit/extract_all.py). For every IFC storey it takes the walls contained in it
(IfcSpatialContainer == storey name) and finds the most common wall base z (50 mm bins). A storey whose walls sit
well off its nominal elevation has a "geometric" level: that is where the floor really is.

Second job (KSCW tower C, AR-KSCW-SSDA-FP-02.ifc): the export carried tower A's storey list, while tower C's floors
sit 1650 mm (L5-L13) / 1750 mm (L14-L19) higher - IFC "LEVEL 6" is at 33700 but its walls and slab top at 35350.
Host levels copied from the IFC storeys then cut the plan through the floor below.

--apply rewrites ifc_all.json "levels" with the geometric elevations (the originals are kept in "levels_ifc"), so
typical_bands.py and group_bands.py compare the right floors. Storeys with fewer than --min-walls walls keep
their nominal elevation.
"""
import argparse
import json
from collections import Counter, defaultdict

ap = argparse.ArgumentParser()
ap.add_argument("--apply", action="store_true")
ap.add_argument("--min-walls", type=int, default=20)
args = ap.parse_args()

D = json.load(open("ifc_all.json"))
nominal = D.get("levels_ifc") or D["levels"]
bases = defaultdict(Counter)
for eid, cat, layer, w, h, sc, b in D["rows"]:
    if cat == "Walls" and b:
        bases[sc][int(round(b[2] / 50.0) * 50)] += 1

out = []
print("%-24s %9s %9s %7s %6s" % ("storey", "IFC z", "geom z", "diff", "walls"))
for name, z in sorted(nominal, key=lambda l: l[1]):
    c = bases.get(name)
    n = sum(c.values()) if c else 0
    gz = z
    if c and n >= args.min_walls:
        gz = float(c.most_common(1)[0][0])
    out.append([name, gz])
    print("%-24s %9.0f %9.0f %7.0f %6d" % (name[:24], z, gz, gz - z, n))

if args.apply:
    D["levels_ifc"] = nominal
    D["levels"] = out
    json.dump(D, open("ifc_all.json", "w"))
    print("ifc_all.json levels replaced with geometric elevations (originals in levels_ifc)")
