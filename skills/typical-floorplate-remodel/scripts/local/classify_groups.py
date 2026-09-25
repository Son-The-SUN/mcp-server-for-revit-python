"""Assign every planned element of a floor to a REP group category (see skills/revit-group-strategy).

    python classify_groups.py L5 --spaces L5 [--overrides overrides_L5.json] [--unit-prefix A1-]

Reads build_<LV>.json (plan_build.py) and ifc_spaces.json; writes groups_<LV>.json and groups_<LV>.png.

Rules (walls):
  - IFC layer external / handrail / external accessories                       -> Facade
  - otherwise sample both sides of the wall (3 points, W/2 + 250 mm out):
      same unit both sides                                                     -> Unit-<nn>
      two different units                                                      -> Intertenancy
      a balcony / non-unit space on a side                                     -> Facade
      unit on one side, nothing on the other (corridor)                        -> Intertenancy (corridor wall)
        ... unless that side lies in a core zone                               -> Core
      nothing on either side (lift shafts, stair, risers)                      -> Core
  Core zones = clusters of those "nothing both sides" walls, grown by 300 mm.
Doors and windows take the category of their host wall; walls created only to host an opening take the
opening's side test. Anything wrong goes in the overrides file: {"<ifc_id>": "Core", ...}.
"""
import argparse
import json
import math
from collections import Counter, defaultdict

from plot import Canvas, curve_pts

ap = argparse.ArgumentParser()
ap.add_argument("lv")
ap.add_argument("--spaces")
ap.add_argument("--overrides")
ap.add_argument("--unit-prefix", default="A1-", help="unit space name prefix for this level, e.g. A1-05.")
args = ap.parse_args()

B = json.load(open("build_%s.json" % args.lv))
SP = json.load(open("ifc_spaces.json")).get(args.spaces or args.lv, [])
SP = sorted(SP, key=lambda s: bool(s.get("approx")))      # bounding-box fallbacks are tried last
OVR = json.load(open(args.overrides)) if args.overrides else {}
FACADE_LAYERS = ("3 Walls - Ext", "3 Handrails", "3 Walls - Ext Accessories")


def pip(x, y, poly):
    inside = False
    for i in range(len(poly)):
        x1, y1 = poly[i][0], poly[i][1]
        x2, y2 = poly[(i + 1) % len(poly)][0], poly[(i + 1) % len(poly)][1]
        if (y1 > y) != (y2 > y) and x < x1 + (y - y1) * (x2 - x1) / (y2 - y1):
            inside = not inside
    return inside


def label_at(x, y):
    for s in SP:
        for lp in s["loops"]:
            if pip(x, y, lp):
                n = s["name"] or ""
                return ("unit", n[len(args.unit_prefix):]) if n.startswith(args.unit_prefix) else ("space", n)
    return ("none", None)


def frame(w):
    if w["kind"] == "line":
        L = math.hypot(w["p1"][0] - w["p0"][0], w["p1"][1] - w["p0"][1])
        d = ((w["p1"][0] - w["p0"][0]) / L, (w["p1"][1] - w["p0"][1]) / L)
        return [((w["p0"][0] + d[0] * L * f, w["p0"][1] + d[1] * L * f), (-d[1], d[0])) for f in (0.25, 0.5, 0.75)]
    C = w["center"]
    res = []
    for k in ("p0", "pm", "p1"):
        a = math.atan2(w[k][1] - C[1], w[k][0] - C[0])
        res.append(((C[0] + w["radius"] * math.cos(a), C[1] + w["radius"] * math.sin(a)), (math.cos(a), math.sin(a))))
    return res


def sides(w):
    off = w["W"] / 2.0 + 250
    L, R = [], []
    for (px, py), n in frame(w):
        L.append(label_at(px + n[0] * off, py + n[1] * off))
        R.append(label_at(px - n[0] * off, py - n[1] * off))
    return Counter(L).most_common(1)[0][0], Counter(R).most_common(1)[0][0], frame(w)[1]


def bbox(w, grow=0.0):
    xs = [w["p0"][0], w["p1"][0]]
    ys = [w["p0"][1], w["p1"][1]]
    h = w["W"] / 2.0 + grow
    return [min(xs) - h, min(ys) - h, max(xs) + h, max(ys) + h]


walls = B["walls"]
info = {}
for w in walls:
    layer = w["layer"] or ""
    a, b, mid = sides(w)
    info[str(w["ifc_id"])] = (a, b, mid)

# core zones: clusters of walls with nothing on either side
seeds = [w for w in walls if not (w["layer"] or "").startswith(FACADE_LAYERS)
         and info[str(w["ifc_id"])][0][0] == "none" and info[str(w["ifc_id"])][1][0] == "none"]
zones = []
for w in seeds:
    bb = bbox(w, 300)
    merged = [z for z in zones if not (bb[2] < z[0] or bb[0] > z[2] or bb[3] < z[1] or bb[1] > z[3])]
    for z in merged:
        zones.remove(z)
        bb = [min(bb[0], z[0]), min(bb[1], z[1]), max(bb[2], z[2]), max(bb[3], z[3])]
    zones.append(bb)
in_zone = lambda x, y: any(z[0] <= x <= z[2] and z[1] <= y <= z[3] for z in zones)

cat = {}
for w in walls:
    k = str(w["ifc_id"])
    layer = w["layer"] or ""
    (la, na), (lb, nb), ((px, py), n) = info[k]
    if layer.startswith(FACADE_LAYERS):
        c = "Facade"
    elif la == "unit" and lb == "unit":
        c = "Unit-%s" % na.split(".")[-1] if na == nb else "Intertenancy"
    elif "space" in (la, lb):
        c = "Facade"
    elif la == "none" and lb == "none":
        c = "Core"
    else:
        s = 1 if la == "none" else -1
        off = w["W"] / 2.0 + 250
        c = "Core" if in_zone(px + n[0] * off * s, py + n[1] * off * s) else "Intertenancy"
    cat[k] = OVR.get(k, c)

doors, windows = {}, {}
for key, items, store in (("door", B["doors"], doors), ("window", B.get("windows", []), windows)):
    for o in items:
        k = str(o["ifc_id"])
        h = o.get("host_ifc")
        if h is not None and str(h) in cat:
            c = cat[str(h)]
        elif key == "window" or o.get("layer", "").startswith("3 Walls - Ext"):
            c = "Facade"
        else:
            # opening without IFC host: side test at its own position
            p = o["pt"]
            la = label_at(p[0] + 500, p[1])[0], label_at(p[0] - 500, p[1])[0], label_at(p[0], p[1] + 500)[0], label_at(p[0], p[1] - 500)[0]
            c = "Core" if in_zone(p[0], p[1]) else ("Intertenancy" if "unit" in la else "Core")
        store[k] = OVR.get(k, c)
        if o.get("free"):
            cat["host-for-%s" % k] = store[k]      # the wall built only to host it goes with it

summary = Counter(list(cat.values()) + list(doors.values()) + list(windows.values()))
json.dump({"walls": cat, "doors": doors, "windows": windows, "core_zones": zones, "summary": summary},
          open("groups_%s.json" % args.lv, "w"), indent=1)
print(args.lv, dict(sorted(summary.items())))
print(" core zones:", [[round(v) for v in z] for z in zones])

# review render
COL = {"Facade": "blue", "Intertenancy": "green", "Core": "black"}
pal = ["orange", "magenta", "teal", "brown", "gold", "purple", "olive", "pink", "cyan", "navy"]
units = sorted(set(v for v in cat.values() if v.startswith("Unit")))
for i, u in enumerate(units):
    COL[u] = pal[i % len(pal)]
xs = [p for w in walls for p in (w["p0"][0], w["p1"][0])]
ys = [p for w in walls for p in (w["p0"][1], w["p1"][1])]
cv = Canvas([min(xs) - 800, min(ys) - 800, max(xs) + 800, max(ys) + 800], width=1400)
for s in SP:
    for lp in s["loops"]:
        cv.poly(lp, "lightgrey", 1, True)
for z in zones:
    cv.poly([(z[0], z[1]), (z[2], z[1]), (z[2], z[3]), (z[0], z[3])], "grey", 1, True)
for w in walls:
    col = COL.get(cat[str(w["ifc_id"])], "red")
    if w["kind"] == "line":
        L = math.hypot(w["p1"][0] - w["p0"][0], w["p1"][1] - w["p0"][1])
        d = ((w["p1"][0] - w["p0"][0]) / L, (w["p1"][1] - w["p0"][1]) / L)
        h = max(w["W"] / 2.0, 60)
        n = (-d[1] * h, d[0] * h)
        cv.fill([(w["p0"][0] + n[0], w["p0"][1] + n[1]), (w["p1"][0] + n[0], w["p1"][1] + n[1]),
                 (w["p1"][0] - n[0], w["p1"][1] - n[1]), (w["p0"][0] - n[0], w["p0"][1] - n[1])], col)
    else:
        C, R = w["center"], w["radius"]
        cv.poly(curve_pts({"arc": [C, R, w["p0"], w["p1"], w["pm"]]}), col, 4)
for items, store in ((B["doors"], doors), (B.get("windows", []), windows)):
    for o in items:
        p = o.get("pt")
        if p:
            col = COL.get(store[str(o["ifc_id"])], "red")
            cv.fill([(p[0] - 180, p[1] - 180), (p[0] + 180, p[1] - 180), (p[0] + 180, p[1] + 180), (p[0] - 180, p[1] + 180)], col)
y = 10
for name, col in sorted(COL.items()):
    cv.fill([(0, 0)], col)
    for dy in range(12):
        for dx in range(20):
            cv.set(10 + dx, y + dy, __import__("plot").COLORS.get(col, (0, 0, 0)))
    cv.text(36, y + 1, name.upper(), "black", 2, world=False)
    y += 18
cv.save("groups_%s.png" % args.lv)
print(" render: groups_%s.png" % args.lv)
