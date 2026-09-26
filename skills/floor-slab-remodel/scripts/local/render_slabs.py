"""Render the IFC slab candidates and the merged outline of one level, to check them before planning.

    python render_slabs.py L5 slabs_L5.png                    # whole level
    python render_slabs.py L5 crop.png 30000,0,40000,9000     # crop window in host mm (x0,y0,x1,y1)
    python render_slabs.py L5 slabs_L5.png --ids              # label candidates with their link element id
    python render_slabs.py L5 plan_L5.png --plan              # overlay the planned slab (slab_L5.json)

Reads ifc_slabs.json (extract_slabs.py) from the current directory. Light grey: candidate up-faces that are not
part of the slab; cyan: faces merged into the slab; orange: walls/columns cut at mid-slab (fills); black: merged
outline; red: its inner loops (voids). With --plan: green = planned slab edge, blue = planned voids, gold = stair
rectangles, magenta box = shaft not modelled, grey box = penetration not modelled.
Uses the PNG plotter of the typical-floorplate-remodel skill.
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "..", "..", "typical-floorplate-remodel", "scripts", "local"))
from plot import Canvas, loop_pts  # noqa: E402


def norm_loop(loop):
    """extract_slabs.py curve records ({line}, {arc}, {poly}) -> plot.py records ({pts}, {arc})."""
    out = []
    for c in loop:
        if "line" in c:
            out.append({"pts": c["line"]})
        elif "poly" in c:
            out.append({"pts": c["poly"]})
        else:
            out.append(c)
    return out


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    lv, png = args[0], args[1]
    crop = [float(v) for v in args[2].split(",")] if len(args) > 2 else None
    D = json.load(open("ifc_slabs.json", encoding="utf-8"))["levels"][lv]
    cands = D["candidates"]
    if crop:
        b = crop
    else:
        bbs = [r["bb"] for r in cands if r["in_slab"]] or [r["bb"] for r in cands]
        b = [min(x[0] for x in bbs) - 1000, min(x[1] for x in bbs) - 1000, max(x[3] for x in bbs) + 1000, max(x[4] for x in bbs) + 1000]
    c = Canvas(b, width=1800 if crop else 1500)
    for r in cands:
        if r["in_slab"]:
            continue
        for f in r["faces"]:
            for lp in f["loops"]:
                c.poly(loop_pts(norm_loop(lp)), "lightgrey", 1, True)
    for r in cands:
        if not r["in_slab"]:
            continue
        for f in r["faces"]:
            for lp in f["loops"]:
                c.poly(loop_pts(norm_loop(lp)), "cyan", 1, True)
    for r in D.get("fills", []):
        for lp in r["loops"]:
            c.poly(loop_pts(norm_loop(lp)), "orange", 1, True)
    for o in D.get("outline") or []:
        for k, lp in enumerate(o["loops"]):
            c.poly(loop_pts(norm_loop(lp)), "black" if k == 0 else "red", 2, True)
    if "--plan" in sys.argv:
        P = json.load(open("slab_%s.json" % lv, encoding="utf-8"))
        for s in P.get("stair_rects", []):
            c.poly([tuple(p) for p in s["rect"]], "gold", 1, True)
        for h in P["holes"]:
            if not h["kept"]:
                b2 = h["bbox"]
                col = "grey" if h["class"] == "penetration" else "magenta"
                c.poly([(b2[0], b2[1]), (b2[2], b2[1]), (b2[2], b2[3]), (b2[0], b2[3])], col, 1, True)
        for k, lp in enumerate(P["loops"]):
            c.poly(loop_pts(norm_loop(lp)), "green" if k < P["outer_count"] else "blue", 2, True)
    if "--ids" in sys.argv:
        for r in cands:
            bb = r["bb"]
            c.text((bb[0] + bb[3]) / 2, (bb[1] + bb[4]) / 2, str(r["id"]), "blue" if r["in_slab"] else "grey", 1)
    c.save(png)
    print("%s: %d candidates, %d merged, outline faces %s -> %s" % (
        lv, len(cands), sum(1 for r in cands if r["in_slab"]),
        [(o["area"], len(o["loops"])) for o in D.get("outline") or []], png))


if __name__ == "__main__":
    main()
