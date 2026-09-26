"""Overlay extracted 1:1 views (DXF in mm) in different colours to check their alignment.

    uv run --with ezdxf --with matplotlib python overlay.py out.png GF.dxf L1.dxf [--layers "TUONG|COT"] [--box x0,y0,x1,y1]

Only line work of the matching layers (ascii-folded name regex, default walls + columns) is drawn:
first file black, then red, blue, green.
"""
import argparse
import re

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import ezdxf
from ezdxf import path as ezpath

import cadlib

COLORS = ["black", "red", "blue", "green", "orange"]


def segments(doc, rx):
    out = []
    for e in doc.modelspace():
        if rx and not re.search(rx, cadlib.ascii_fold(e.dxf.layer)):
            continue
        t = e.dxftype()
        try:
            if t == "INSERT":
                ents = list(e.virtual_entities())
            else:
                ents = [e]
            for ve in ents:
                if ve.dxftype() in ("TEXT", "MTEXT", "ATTRIB", "HATCH", "SOLID", "POINT"):
                    continue
                p = ezpath.make_path(ve)
                pts = [(v.x, v.y) for v in p.flattening(5)]
                if len(pts) >= 2:
                    out.append(pts)
        except Exception:
            pass
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("out")
    ap.add_argument("dxf", nargs="+")
    ap.add_argument("--layers", default=r"TUONG|WALL|COT|COLUMN")
    ap.add_argument("--box", default=None)
    a = ap.parse_args()
    fig = plt.figure(figsize=(14, 11), dpi=110)
    ax = fig.add_axes([0.02, 0.02, 0.96, 0.96])
    for i, f in enumerate(a.dxf):
        for pts in segments(ezdxf.readfile(f), a.layers):
            xs, ys = zip(*pts)
            ax.plot(xs, ys, color=COLORS[i % len(COLORS)], lw=0.7 if i == 0 else 0.5)
        ax.plot([], [], color=COLORS[i % len(COLORS)], label=f.replace("\\", "/").split("/")[-1])
    ax.set_aspect("equal")
    ax.grid(True, lw=0.3, alpha=0.5)
    ax.legend(loc="upper right")
    if a.box:
        x0, y0, x1, y1 = [float(c) for c in a.box.split(",")]
        ax.set_xlim(x0, x1)
        ax.set_ylim(y0, y1)
    fig.savefig(a.out, facecolor="white")
    print("wrote " + a.out)


if __name__ == "__main__":
    main()
