"""List the walls/doors/windows that differ between two extracted floors (tolerance match on bounding boxes).

    python diff_levels.py L5 L10 [--tol 30]

Reads ifc_<A>.json and ifc_<B>.json (scripts/revit/extract_view.py). Two elements match when category, IFC layer
and width agree and every bounding-box coordinate (z relative to its own level) is within --tol mm. Writes
diff_<A>_<B>.json with the unmatched IFC ids of each side - the elements that stop a group type being shared.
"""
import argparse
import json
from collections import Counter

ap = argparse.ArgumentParser()
ap.add_argument("a")
ap.add_argument("b")
ap.add_argument("--tol", type=float, default=30.0)
args = ap.parse_args()


def load(lv):
    D = json.load(open("ifc_%s.json" % lv))
    lz = D["meta"]["level_z"]
    out = []
    for r in D["rows"]:
        b = r.get("bb")
        if r["cat"] not in ("Walls", "Doors", "Windows") or not b or not (-800 <= b[2] - lz <= 200):
            continue
        out.append((r, [b[0], b[1], b[2] - lz, b[3], b[4], b[5] - lz]))
    return out


def same(x, y):
    (ra, ba), (rb, bb) = x, y
    return (ra["cat"] == rb["cat"] and ra["IfcPresentationLayer"] == rb["IfcPresentationLayer"]
            and abs((ra["Width"] or 0) - (rb["Width"] or 0)) < 1 and all(abs(ba[i] - bb[i]) < args.tol for i in range(6)))


A, B = load(args.a), load(args.b)
used, onlyA = set(), []
for x in A:
    hit = next((j for j, y in enumerate(B) if j not in used and same(x, y)), None)
    if hit is None:
        onlyA.append(x)
    else:
        used.add(hit)
onlyB = [y for j, y in enumerate(B) if j not in used]
print("%s %d | %s %d | matched %d | only %s %d | only %s %d" % (args.a, len(A), args.b, len(B), len(used), args.a, len(onlyA), args.b, len(onlyB)))
lay = lambda r: (r["IfcPresentationLayer"] or "-")[:18]
print("only %s:" % args.a, dict(Counter((r["cat"], lay(r)) for r, b in onlyA)))
print("only %s:" % args.b, dict(Counter((r["cat"], lay(r)) for r, b in onlyB)))
json.dump({"only_" + args.a: [r["id"] for r, b in onlyA], "only_" + args.b: [r["id"] for r, b in onlyB]},
          open("diff_%s_%s.json" % (args.a, args.b), "w"))
