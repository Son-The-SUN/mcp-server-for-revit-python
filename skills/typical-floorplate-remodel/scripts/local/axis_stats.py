"""How the IFC wall axis sits relative to each wall body (face / centre / arc / mismatch).

    python axis_stats.py L5 L10
"""
import json, math, sys
from collections import Counter
from plot import loop_pts
for lv in sys.argv[1:]:
    D = json.load(open("ifc_%s.json" % lv)); lz = D["meta"]["level_z"]
    c = Counter(); odd = []
    for r in D["rows"]:
        if r["cat"] != "Walls": continue
        b = r["bb"]
        if not (-800 <= b[2]-lz <= 200): continue
        ax = [a for a in r["axis"] if a.get("style") == "Axis"]
        loops = r["cut"] or r["top"]
        pts = [p for lp in loops for p in loop_pts(lp)]
        W = r["Width"]
        if not ax:
            c["noaxis"] += 1; continue
        a = ax[0]
        if "arc" in a:
            C, R = a["arc"][0], a["arc"][1]
            rr = [math.hypot(p[0]-C[0], p[1]-C[1]) - R for p in pts]
            if not rr: c["arc-nopts"] += 1; continue
            lo, hi = min(rr), max(rr)
            k = "arc lo=%d hi=%d W=%d" % (round(lo/10)*10, round(hi/10)*10, W)
            c["arc"] += 1
            if abs(abs(hi-lo)-W) > 25: odd.append((r["id"], k))
            continue
        p0, p1 = a["pts"][0], a["pts"][-1]
        L = math.hypot(p1[0]-p0[0], p1[1]-p0[1]); d = ((p1[0]-p0[0])/L, (p1[1]-p0[1])/L); n = (-d[1], d[0])
        s = [(p[0]-p0[0])*n[0] + (p[1]-p0[1])*n[1] for p in pts]
        t = [(p[0]-p0[0])*d[0] + (p[1]-p0[1])*d[1] for p in pts]
        if not s: c["nopts"] += 1; continue
        lo, hi = min(s), max(s)
        if abs((hi-lo) - W) <= 25:
            if abs(lo) < 15 or abs(hi) < 15: c["face"] += 1
            elif abs(lo+hi) < 30: c["center"] += 1
            else: c["other-offset"] += 1; odd.append((r["id"], "off lo=%.0f hi=%.0f W=%s" % (lo, hi, W)))
        else:
            c["thick-mismatch"] += 1; odd.append((r["id"], "mism lo=%.0f hi=%.0f W=%s tl=%.0f th=%.0f L=%.0f" % (lo, hi, W, min(t), max(t), L)))
    print(lv, dict(c))
    for o in odd[:30]: print("   ", o)
