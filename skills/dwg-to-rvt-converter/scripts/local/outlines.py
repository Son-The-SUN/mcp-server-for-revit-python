"""List the largest closed polylines of an extracted 1:1 view (slab edges, building outlines, roof outlines).

    uv run --with ezdxf python outlines.py L1.dxf [--top 5] [--layer "GHI CHU"]

Prints area (m2), layer, vertex count and the vertices in mm, ready to paste into build_envelope.py.
"""
import argparse
import json
import re

import ezdxf

import cadlib


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("dxf")
    ap.add_argument("--top", type=int, default=5)
    ap.add_argument("--layer", default=None, help="regex on the ascii-folded layer name")
    a = ap.parse_args()
    d = ezdxf.readfile(a.dxf)
    out = []
    for pl in d.modelspace().query("LWPOLYLINE"):
        if a.layer and not re.search(a.layer, cadlib.ascii_fold(pl.dxf.layer), re.I):
            continue
        pts = [(p[0], p[1]) for p in pl.get_points("xy")]
        if len(pts) >= 2 and abs(pts[0][0] - pts[-1][0]) < 1 and abs(pts[0][1] - pts[-1][1]) < 1:
            pts = pts[:-1]
        if len(pts) < 3 or not (pl.closed or len(pts) >= 4):
            continue
        # drop repeated vertices
        clean = [pts[0]]
        for p in pts[1:]:
            if abs(p[0] - clean[-1][0]) > 1 or abs(p[1] - clean[-1][1]) > 1:
                clean.append(p)
        area = 0.5 * abs(sum(clean[i][0] * clean[(i + 1) % len(clean)][1] - clean[(i + 1) % len(clean)][0] * clean[i][1]
                             for i in range(len(clean))))
        out.append((area, pl.dxf.layer, [[round(x), round(y)] for x, y in clean]))
    out.sort(key=lambda t: -t[0])
    for area, layer, pts in out[:a.top]:
        print("{:8.1f} m2  {:<12} {} vertices".format(area / 1e6, layer, len(pts)))
        print("          " + json.dumps(pts))


if __name__ == "__main__":
    main()
