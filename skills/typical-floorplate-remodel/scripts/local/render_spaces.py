"""Wall plan of an extracted floor with the IFC unit spaces filled and labelled.

    python render_spaces.py L10 L6 out.png [unit_prefix]     # floor key, ifc_spaces.json key, output
"""
import json, sys
from plot import Canvas, loop_pts, curve_pts
lv, splv, out = sys.argv[1], sys.argv[2], sys.argv[3]
prefix = sys.argv[4] if len(sys.argv) > 4 else "A1-"
D = json.load(open("ifc_%s.json" % lv)); rows = D["rows"]; lz = D["meta"]["level_z"]
S = json.load(open("ifc_spaces.json"))[splv]
xs, ys = [], []
for r in rows:
    if r["cat"] == "Walls" and r.get("bb"):
        xs += [r["bb"][0], r["bb"][3]]; ys += [r["bb"][1], r["bb"][4]]
b = [min(xs)-1000, min(ys)-1000, max(xs)+1000, max(ys)+1000]
c = Canvas(b, width=1400)
pal = ["pink", "cyan", "gold", "lightgrey", "orange", "green", "magenta", "teal", "olive", "purple", "brown"]
for i, s in enumerate(S):
    for lp in s["loops"]:
        c.fill(lp, (230, 230, 250) if not (s["name"] or "").startswith(prefix) else [(255,220,220),(220,255,220),(220,220,255),(255,255,200),(255,220,255),(200,255,255),(240,230,200),(230,200,240),(200,240,230)][i % 9])
for r in rows:
    if r["cat"] != "Walls": continue
    if not (-800 <= r["bb"][2] - lz <= 200): continue
    col = {"2 Walls - Concrete": "black", "3 Walls - Ext": "blue", "3 Walls - Int": "green"}.get(r["IfcPresentationLayer"], "orange")
    for loop in r["cut"] or r["top"]:
        c.poly(loop_pts(loop), col, 2, True)
for r in rows:
    if r["cat"] == "Doors":
        for rec in r.get("plan", []): c.poly(curve_pts(rec), "red", 1)
for s in S:
    for lp in s["loops"]:
        cx = sum(p[0] for p in lp)/len(lp); cy = sum(p[1] for p in lp)/len(lp)
        c.text(cx-600, cy, s["name"].replace("_", " ").replace(prefix, ""), "navy", 3)
c.save(out); print(c.w, c.h)
