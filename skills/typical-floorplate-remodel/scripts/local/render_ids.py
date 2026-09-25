"""Close-up of chosen IFC elements (red) with their neighbours.

    python render_ids.py L5 1009328,1009498 out.png [pad_mm]
"""
import json, sys
from plot import Canvas, loop_pts, curve_pts
lv = sys.argv[1]; ids = [int(x) for x in sys.argv[2].split(",")]; out = sys.argv[3]; pad = float(sys.argv[4]) if len(sys.argv) > 4 else 1500
D = json.load(open("ifc_%s.json" % lv)); rows = D["rows"]; lz = D["meta"]["level_z"]
sel = [r for r in rows if r["id"] in ids]
x0 = min(r["bb"][0] for r in sel) - pad; y0 = min(r["bb"][1] for r in sel) - pad
x1 = max(r["bb"][3] for r in sel) + pad; y1 = max(r["bb"][4] for r in sel) + pad
c = Canvas([x0, y0, x1, y1], width=1000)
for r in rows:
    b = r.get("bb")
    if not b or b[3] < x0 or b[0] > x1 or b[4] < y0 or b[1] > y1: continue
    hl = r["id"] in ids
    col = "red" if hl else {"Walls": "grey", "Doors": "pink", "Windows": "cyan"}.get(r["cat"], "lightgrey")
    for lp in r["cut"]: c.poly(loop_pts(lp), col, 2 if hl else 1, True)
    for lp in r["top"]: c.poly(loop_pts(lp), "orange" if hl else "lightgrey", 1, True)
    for a in r["axis"]:
        if a.get("style") == "Axis": c.poly(curve_pts(a), "magenta", 1)
    for rec in r.get("plan", []): c.poly(curve_pts(rec), col, 1)
    if r["cat"] in ("Walls", "Doors", "Windows"):
        c.text((b[0]+b[3])/2, (b[1]+b[4])/2, str(r["id"] % 10000), "navy" if not hl else "red", 2)
c.save(out); print(c.w, c.h)
