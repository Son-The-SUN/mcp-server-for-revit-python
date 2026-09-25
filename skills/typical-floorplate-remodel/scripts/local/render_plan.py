"""Render a build plan over the IFC outlines, to check it before building.

    python render_plan.py L5 plan_L5.png                 # whole floor
    python render_plan.py L5 crop.png 30000,0,40000,9000  # crop window in host mm (x0,y0,x1,y1)

Grey: IFC section outlines. Walls as rectangles/arcs coloured by target type (black WT52, blue WT51, navy WT50,
orange glass, purple generic); dropped walls dashed red. Doors: red cross (magenta = no IFC host), green = swing
side, cyan = hinge end, gold = exterior side. Windows: cyan cross (grey = no family yet).
"""
import json
import math
import sys

from plot import Canvas, curve_pts, loop_pts

lv, out = sys.argv[1], sys.argv[2]
crop = [float(v) for v in sys.argv[3].split(",")] if len(sys.argv) > 3 else None
D = json.load(open("ifc_%s.json" % lv))
B = json.load(open("build_%s.json" % lv))
lz = D["meta"]["level_z"]
rows = D["rows"]
wb = [r["bb"] for r in rows if r["cat"] == "Walls" and r.get("bb")]
b = crop or [min(x[0] for x in wb) - 500, min(x[1] for x in wb) - 500, max(x[3] for x in wb) + 500, max(x[4] for x in wb) + 500]
c = Canvas(b, width=1600 if crop else 1400)
for r in rows:
    if r["cat"] in ("Walls", "Doors", "Windows") and r.get("bb") and -800 <= r["bb"][2] - lz <= 1500:
        for lp in r["cut"] or r["top"]:
            c.poly(loop_pts(lp), "lightgrey", 1, True)


def tcol(t):
    for k, v in (("WT52", "black"), ("WT51", "blue"), ("WT50", "navy"), ("GLASS", "orange"), ("GENERIC", "purple")):
        if k in t:
            return v
    return "red"


def rect(p0, p1, W):
    L = math.hypot(p1[0] - p0[0], p1[1] - p0[1])
    d = ((p1[0] - p0[0]) / L, (p1[1] - p0[1]) / L)
    n = (-d[1] * W / 2, d[0] * W / 2)
    return [(p0[0] + n[0], p0[1] + n[1]), (p1[0] + n[0], p1[1] + n[1]), (p1[0] - n[0], p1[1] - n[1]), (p0[0] - n[0], p0[1] - n[1])]


def draw_wall(w, col):
    if w["kind"] == "line":
        c.poly(rect(w["p0"], w["p1"], w["W"]), col, 1, True)
        return
    C, R = w["center"], w["radius"]
    angs = [math.atan2(w[k][1] - C[1], w[k][0] - C[0]) for k in ("p0", "p1", "pm")]
    for RR in (R - w["W"] / 2, R + w["W"] / 2):
        pts = [[C[0] + RR * math.cos(a), C[1] + RR * math.sin(a)] for a in angs]
        c.poly(curve_pts({"arc": [C, RR, pts[0], pts[1], pts[2]]}), col, 1)


for w in B["walls"]:
    draw_wall(w, tcol(w["type"]))
for w in B.get("dropped", []):
    if w["kind"] == "line":
        for i in range(0, 10, 2):
            p = [w["p0"][k] + (w["p1"][k] - w["p0"][k]) * i / 10.0 for k in (0, 1)]
            q = [w["p0"][k] + (w["p1"][k] - w["p0"][k]) * (i + 1) / 10.0 for k in (0, 1)]
            c.line(p, q, "red", 2)


def cross(p, col, s=150):
    c.line((p[0] - s, p[1]), (p[0] + s, p[1]), col, 2)
    c.line((p[0], p[1] - s), (p[0], p[1] + s), col, 2)


for d in B["doors"]:
    p = d.get("pt")
    if not p:
        continue
    cross(p, "red" if d.get("host_ifc") else "magenta")
    for key, col, L in (("swing", "green", 700), ("hinge", "cyan", 500), ("exterior", "gold", 500)):
        if d.get(key):
            v = d[key]
            c.line(p, (p[0] + v[0] * L, p[1] + v[1] * L), col, 2)
for w in B.get("windows", []):
    p = w.get("pt")
    if p:
        cross(p, "cyan" if w.get("family") else "grey", 120)
        if w.get("exterior"):
            v = w["exterior"]
            c.line(p, (p[0] + v[0] * 400, p[1] + v[1] * 400), "gold", 1)
c.save(out)
print(out, c.w, c.h)
