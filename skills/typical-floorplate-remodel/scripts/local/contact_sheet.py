"""Contact sheet: one close-up tile per IFC element id (handy for odd walls).

    python contact_sheet.py L5 1008994,1009071,... sheet.png
"""
import json, sys, math
from plot import Canvas, loop_pts, curve_pts
def crop_canvas(rows, r0, size=320, pad=900):
    b = r0["bb"]
    cx, cy = (b[0]+b[3])/2, (b[1]+b[4])/2
    half = max(b[3]-b[0], b[4]-b[1])/2 + pad
    c = Canvas([cx-half, cy-half, cx+half, cy+half], width=size, margin=4)
    for r in rows:
        bb = r.get("bb")
        if not bb or bb[3] < cx-half or bb[0] > cx+half or bb[4] < cy-half or bb[1] > cy+half: continue
        hl = r["id"] == r0["id"]
        if r["cat"] not in ("Walls", "Doors", "Windows") and not hl: continue
        col = "red" if hl else {"Walls": "grey", "Doors": "pink", "Windows": "cyan"}[r["cat"]]
        for lp in r["cut"]: c.poly(loop_pts(lp), col, 2 if hl else 1, True)
        for lp in r["top"]: c.poly(loop_pts(lp), "orange" if hl else "lightgrey", 1, True)
        if hl:
            for a in r["axis"]:
                if a.get("style") == "Axis": c.poly(curve_pts(a), "blue", 1)
    c.text(4, 4, "%d W%d" % (r0["id"] % 100000, r0["Width"] or 0), "navy", 2, world=False)
    c.text(4, 16, "H%d N%d" % (r0["Height"] or 0, r0["nsol"]), "navy", 2, world=False)
    return c
def sheet(rows, sel, out, cols=5, size=320):
    tiles = [crop_canvas(rows, r, size) for r in sel]
    W = cols*size; H = ((len(tiles)+cols-1)//cols)*size
    big = Canvas([0, 0, W, H], px_per_mm=1, margin=0)
    for k, t in enumerate(tiles):
        ox, oy = (k % cols)*size, (k//cols)*size
        for j in range(min(t.h, size)):
            for i in range(min(t.w, size)):
                q = (j*t.w+i)*3
                big.set(ox+i, oy+j, tuple(t.buf[q:q+3]))
        for i in range(size): big.set(ox+i, oy, (0,0,0))
        for j in range(size): big.set(ox, oy+j, (0,0,0))
    big.save(out); print(out, len(tiles))
if __name__ == "__main__":
    lv = sys.argv[1]; ids = [int(x) for x in sys.argv[2].split(",")]; out = sys.argv[3]
    D = json.load(open("ifc_%s.json" % lv)); rows = D["rows"]
    sel = [r for i in ids for r in rows if r["id"] == i]
    sheet(rows, sel, out)
