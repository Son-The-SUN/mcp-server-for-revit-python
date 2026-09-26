"""Overlay the user's example slab on the IFC slab outline of a level, to check conventions (edge position,
voids) before planning.

    python compare_example.py L10 L10 compare_L10.png          # <example key> <level key> <png>
    python compare_example.py L10 L10 compare_L10.png --outline slab_only   # IFC slab outer loops only
    python compare_example.py L10 L10 compare_L10.png --outline plan        # the planned slab (slab_L10.json)

Reads example_slabs.json (inspect_example.py) and ifc_slabs.json (extract_slabs.py) from the current directory.
The example comes from another file with its own coordinates, so it is fitted to the IFC outline: each of the
8 right-angle rotations/mirrors, bbox centres aligned, scored by the area where the two regions disagree
(rasterised at 100 mm). Black = IFC outline, blue = example (fitted). Prints areas, the fit and the mismatch.
"""
import json
import math
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "..", "..", "typical-floorplate-remodel", "scripts", "local"))
from plot import Canvas, loop_pts  # noqa: E402
from render_slabs import norm_loop  # noqa: E402

CELL = 100.0


def area(pts):
    return sum(pts[i][0] * pts[(i + 1) % len(pts)][1] - pts[(i + 1) % len(pts)][0] * pts[i][1] for i in range(len(pts))) / 2.0


def raster(loops, b):
    """Even-odd fill of all loops on a CELL grid over bbox b -> set of cells."""
    cells = set()
    nx = int((b[2] - b[0]) / CELL) + 1
    ny = int((b[3] - b[1]) / CELL) + 1
    for j in range(ny):
        y = b[1] + (j + 0.5) * CELL
        xs = []
        for pts in loops:
            for i in range(len(pts)):
                (xa, ya), (xb, yb) = pts[i], pts[(i + 1) % len(pts)]
                if (ya <= y < yb) or (yb <= y < ya):
                    xs.append(xa + (y - ya) * (xb - xa) / (yb - ya))
        xs.sort()
        for k in range(0, len(xs) - 1, 2):
            i0 = int(math.ceil((xs[k] - b[0]) / CELL - 0.5))
            i1 = int(math.floor((xs[k + 1] - b[0]) / CELL - 0.5))
            for i in range(max(i0, 0), min(i1, nx - 1) + 1):
                cells.add((i, j))
    return cells


def bbox(loops):
    xs = [p[0] for lp in loops for p in lp]
    ys = [p[1] for lp in loops for p in lp]
    return [min(xs), min(ys), max(xs), max(ys)]


def transform(loops, k, mirror, dx, dy):
    c, s = [(1, 0), (0, 1), (-1, 0), (0, -1)][k]
    out = []
    for lp in loops:
        q = []
        for x, y in lp:
            if mirror:
                x = -x
            q.append((c * x - s * y + dx, s * x + c * y + dy))
        out.append(q)
    return out


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    ex_key, lv, png = args[0], args[1], args[2]
    which = sys.argv[sys.argv.index("--outline") + 1] if "--outline" in sys.argv else "outline"
    E = json.load(open("example_slabs.json", encoding="utf-8"))[ex_key]
    D = json.load(open("ifc_slabs.json", encoding="utf-8"))["levels"][lv]
    ex = [[(p[0], p[1]) for p in loop_pts(lp)] for f in E["floors"] for lp in [norm_loop(x) for x in f["loops"]]]
    if which == "plan":
        P = json.load(open("slab_%s.json" % lv, encoding="utf-8"))
        ifc = [[(p[0], p[1]) for p in loop_pts(norm_loop(lp))] for lp in P["loops"]]
    else:
        ifc = [[(p[0], p[1]) for p in loop_pts(norm_loop(lp))] for o in D[which] for lp in o["loops"]]
    bi = bbox(ifc)
    ci = ((bi[0] + bi[2]) / 2, (bi[1] + bi[3]) / 2)
    best = None
    for mirror in (False, True):
        for k in range(4):
            t = transform(ex, k, mirror, 0, 0)
            be = bbox(t)
            dx, dy = ci[0] - (be[0] + be[2]) / 2, ci[1] - (be[1] + be[3]) / 2
            t = transform(ex, k, mirror, dx, dy)
            b = bbox(t + ifc)
            b = [b[0] - 500, b[1] - 500, b[2] + 500, b[3] + 500]
            a, c = raster(t, b), raster(ifc, b)
            bad = len(a ^ c) * CELL * CELL / 1e6
            if best is None or bad < best[0]:
                best = (bad, k, mirror, dx, dy, t)
    bad, k, mirror, dx, dy, t = best
    # refine the translation on a small grid (bbox centres differ when one outline has an extra bump)
    for step in (1000, 500, 200, 100):
        improved = True
        while improved:
            improved = False
            for ddx, ddy in ((step, 0), (-step, 0), (0, step), (0, -step)):
                t2 = transform(ex, k, mirror, dx + ddx, dy + ddy)
                b = bbox(t2 + ifc)
                b = [b[0] - 500, b[1] - 500, b[2] + 500, b[3] + 500]
                bad2 = len(raster(t2, b) ^ raster(ifc, b)) * CELL * CELL / 1e6
                if bad2 < bad - 1e-9:
                    bad, dx, dy, t = bad2, dx + ddx, dy + ddy, t2
                    improved = True
    b = bbox(t + ifc)
    c = Canvas([b[0] - 1000, b[1] - 1000, b[2] + 1000, b[3] + 1000], width=1500)
    for lp in ifc:
        c.poly(lp, "black", 2, True)
    for lp in t:
        c.poly(lp, "blue", 1, True)
    c.save(png)
    b = [b[0] - 500, b[1] - 500, b[2] + 500, b[3] + 500]
    ea = len(raster(t, b)) * CELL * CELL / 1e6          # even-odd, so voids count whatever their orientation
    ia = len(raster(ifc, b)) * CELL * CELL / 1e6
    print(json.dumps({"example_area_m2": round(ea, 1), "ifc_area_m2": round(ia, 1),
                      "rotation_deg": 90 * k, "mirrored": mirror, "shift_mm": [round(dx), round(dy)],
                      "mismatch_m2": round(bad, 1), "example_loops": len(ex), "ifc_loops": len(ifc), "png": png}))


if __name__ == "__main__":
    main()
