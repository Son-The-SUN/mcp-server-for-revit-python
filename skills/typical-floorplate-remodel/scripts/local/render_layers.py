"""Plan of an extracted floor coloured by IFC layer.

    python render_layers.py L5 out.png [x0,y0,x1,y1] [labels]

Walls: black = concrete layer, blue = external, green = internal, orange = handrails, purple = accessories,
pink = other; doors red, windows cyan, framing grey, columns brown, magenta = IFC wall axis.
"""
import json, sys
from plot import Canvas, loop_pts, curve_pts
lv = sys.argv[1]; out = sys.argv[2]
crop = [float(v) for v in sys.argv[3].split(",")] if len(sys.argv) > 3 else None
labels = len(sys.argv) > 4 and sys.argv[4] == "labels"
D = json.load(open("ifc_%s.json" % lv))
rows = D["rows"]
LAYER_COL = [("2 Walls - Concrete", "black"), ("3 Walls - Ext Accessories", "purple"), ("3 Walls - Ext", "blue"),
             ("3 Walls - Int", "green"), ("3 Handrails", "orange")]
CAT_COL = {"Doors": "red", "Windows": "cyan", "Structural Framing": "grey", "Structural Columns": "brown",
           "Columns": "brown", "Floors": "lightgrey", "Stairs": "olive", "Railings": "magenta"}
xs, ys = [], []
for r in rows:
    if r["cat"] in ("Walls",) and r.get("bb"):
        xs += [r["bb"][0], r["bb"][3]]; ys += [r["bb"][1], r["bb"][4]]
b = crop or [min(xs)-1000, min(ys)-1000, max(xs)+1000, max(ys)+1000]
c = Canvas(b, width=2000 if not crop else 1800)
order = ["Floors", "Structural Framing", "Stairs", "Structural Columns", "Columns", "Walls", "Windows", "Doors"]
for cat in order:
    for r in rows:
        if r["cat"] != cat: continue
        col = next((v for k, v in LAYER_COL if (r["IfcPresentationLayer"] or "").startswith(k)), "pink") if cat == "Walls" else CAT_COL.get(cat, "pink")
        for loop in r["top"]:
            c.poly(loop_pts(loop), "lightgrey" if cat != "Walls" else col, 1, True)
        for loop in r["cut"]:
            c.poly(loop_pts(loop), col, 2 if cat == "Walls" else 1, True)
        if cat in ("Doors", "Windows"):
            for rec in r.get("plan", []):
                c.poly(curve_pts(rec), col, 1)
        if cat == "Walls":
            for a in r["axis"]:
                if a.get("style") == "Axis":
                    c.poly(curve_pts(a), "magenta", 1)
if labels:
    for r in rows:
        if r["cat"] in ("Walls", "Doors", "Windows") and r.get("bb"):
            bb = r["bb"]
            cx, cy = (bb[0]+bb[3])/2, (bb[1]+bb[4])/2
            if b[0] <= cx <= b[2] and b[1] <= cy <= b[3]:
                c.text(cx, cy, str(r["id"] % 10000), "red" if r["cat"] != "Walls" else "navy", 2)
c.save(out)
print(c.w, c.h, b)
