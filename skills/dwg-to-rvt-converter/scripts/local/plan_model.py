"""Plan the Revit elements of one floor from an extracted 1:1 plan (extract_view.py output, mm).

    uv run --with ezdxf --with matplotlib python plan_model.py GF.dxf --out plan_GF.json [--config dwg_config.json]
        [--schedule schedule.json] [--render plan_GF.png]

Reads (layer names are matched ascii-folded, so TƯỜNG = TUONG):
- walls: hatches on the wall-hatch layers/patterns (the fill between the wall lines). Each hatch loop is cut
  into rectangles (orthogonal decomposition, longest runs first): long side = wall axis, short side = thickness.
- columns: solid hatches / closed squares on the column layers, and small square wall hatches standing alone.
- openings: gaps along a wall line between two collinear pieces of the same thickness (or a piece and a column),
  with door/window line work in the gap. The nearest tag text (Đ1, S2, VK1, D01, W03...) gives the type; the
  schedule gives its size. The two pieces become one wall that hosts the opening.
- doors: swing side from the door arc (hinge = arc centre), so the build can flip the family to match.
Everything is in the plan's mm coordinates (= Revit model mm when imported origin-to-origin).
"""
import argparse
import collections
import json
import math
import re

import ezdxf

import cadlib

DEFAULT = {
    "wall_hatch_layers": r"HATCH|TUONG|WALL",
    "wall_hatch_patterns": r"^(ANSI3\d|ANSI37|AR-B\d+|BRICK|AR-CONC|SOLID)$",
    "wall_hatch_exclude_patterns": r"WOOD|GRASS|AR-SAND|DOTS|GRAVEL",
    "wall_line_layers": r"^TUONG$|WALL",
    "column_layers": r"^COT$|COLUMN|COLS",
    "door_layers": r"^CUA$|^CUA DI$|DOOR",
    "window_layers": r"^CUA$|CUA SO|CUASO|WIN|GLAZ",
    "tag_regex": r"^(Đ|D|S|W|VK|DW|SW)\s?-?\d{1,2}[A-Z]?$",
    "min_thickness": 60, "max_thickness": 450,
    "max_opening": 5000,
    "column_max": 600,
    "snap_deg": 0.5,
    "wall_outline_layers": None,      # regex: closed polylines on these layers whose parts are all wall-thin become walls
    "column_blocks": None,            # regex on block names drawn as columns (e.g. steel posts)
    "column_block_kind": "steel",
    "extra_walls": [],                # [{"a": [x, y], "b": [x, y], "t": 100}] added as drawn walls
    "drop_walls_near": [],            # [[x, y], ...] planned walls whose axis passes within 150 mm are dropped
}


def load_cfg(path):
    cfg = dict(DEFAULT)
    if path:
        with open(path, encoding="utf-8") as f:
            cfg.update(json.load(f))
    return cfg


def lay(e):
    return cadlib.ascii_fold(e.dxf.layer)


# ---------------------------------------------------------------- hatch loops -> rectangles

def hatch_loops(h):
    loops = []
    for p in h.paths:
        pts = []
        if hasattr(p, "vertices") and p.type == 1 or p.__class__.__name__ == "PolylinePath":
            pts = [(v[0], v[1]) for v in p.vertices]
        else:
            for ed in p.edges:
                if ed.type == 1 or ed.__class__.__name__ == "LineEdge":
                    pts.append((ed.start[0], ed.start[1]))
                else:
                    pts = []  # arcs etc: not an orthogonal wall loop
                    break
        if len(pts) >= 3:
            loops.append(pts)
    return loops


def ocs_fix(h, loops):
    # hatches with extrusion (0,0,-1) are mirrored in x
    ez = h.dxf.get("extrusion", (0, 0, 1))
    if ez[2] < 0:
        return [[(-x, y) for x, y in lp] for lp in loops]
    return loops


def pip(x, y, poly):
    inside = False
    n = len(poly)
    for i in range(n):
        x1, y1 = poly[i]
        x2, y2 = poly[(i + 1) % n]
        if (y1 > y) != (y2 > y):
            xi = x1 + (y - y1) * (x2 - x1) / (y2 - y1)
            if xi > x:
                inside = not inside
    return inside


def rotate(pts, ang):
    c, s = math.cos(ang), math.sin(ang)
    return [(x * c - y * s, x * s + y * c) for x, y in pts]


def main_angle(poly):
    """Direction of the longest edge (radians, 0..pi/2) - walls are drawn in one orthogonal frame."""
    best, ang = 0, 0.0
    for i in range(len(poly)):
        (x1, y1), (x2, y2) = poly[i], poly[(i + 1) % len(poly)]
        L = math.hypot(x2 - x1, y2 - y1)
        if L > best:
            best, ang = L, math.atan2(y2 - y1, x2 - x1) % (math.pi / 2)
    return ang


def rects_of_loop(poly, tol=2.0):
    """Orthogonal polygon -> list of rectangles (x0, y0, x1, y1, angle) covering it, longest runs first."""
    ang = main_angle(poly)
    if abs(ang) < math.radians(0.3) or abs(ang - math.pi / 2) < math.radians(0.3):
        ang = 0.0
    P = rotate(poly, -ang)
    xs = sorted(set(round(p[0] / tol) * tol for p in P))
    ys = sorted(set(round(p[1] / tol) * tol for p in P))
    # merge near-duplicate coordinates
    def dedup(v):
        out = []
        for a in v:
            if not out or a - out[-1] > tol:
                out.append(a)
        return out
    xs, ys = dedup(xs), dedup(ys)
    nx, ny = len(xs) - 1, len(ys) - 1
    if nx < 1 or ny < 1:
        return []
    inside = [[pip((xs[i] + xs[i + 1]) / 2, (ys[j] + ys[j + 1]) / 2, P) for j in range(ny)] for i in range(nx)]
    cells = set((i, j) for i in range(nx) for j in range(ny) if inside[i][j])
    if not cells:
        return []
    # maximal rectangles
    cands = []
    for i0 in range(nx):
        for i1 in range(i0, nx):
            for j0 in range(ny):
                for j1 in range(j0, ny):
                    if all(inside[i][j] for i in range(i0, i1 + 1) for j in range(j0, j1 + 1)):
                        cands.append((i0, i1, j0, j1))
    maximal = []
    for c in cands:
        i0, i1, j0, j1 = c
        if any(o != c and o[0] <= i0 and o[1] >= i1 and o[2] <= j0 and o[3] >= j1 for o in cands):
            continue
        maximal.append(c)
    covered = set()
    out = []
    while cells - covered:
        def gain(c):
            i0, i1, j0, j1 = c
            cs = set((i, j) for i in range(i0, i1 + 1) for j in range(j0, j1 + 1))
            new = cs - covered
            if not new:
                return (0, 0)
            w, h = xs[i1 + 1] - xs[i0], ys[j1 + 1] - ys[j0]
            area_new = sum((xs[i + 1] - xs[i]) * (ys[j + 1] - ys[j]) for i, j in new)
            return (area_new * max(w, h) / max(1.0, min(w, h)), area_new)
        best = max(maximal, key=gain)
        if gain(best)[0] <= 0:
            break
        i0, i1, j0, j1 = best
        covered |= set((i, j) for i in range(i0, i1 + 1) for j in range(j0, j1 + 1))
        out.append((xs[i0], ys[j0], xs[i1 + 1], ys[j1 + 1], ang))
    return out


def rect_to_wall(r):
    x0, y0, x1, y1, ang = r
    w, h = x1 - x0, y1 - y0
    if w >= h:
        a, b, t = (x0, (y0 + y1) / 2), (x1, (y0 + y1) / 2), h
    else:
        a, b, t = ((x0 + x1) / 2, y0), ((x0 + x1) / 2, y1), w
    a, b = rotate([a, b], ang)
    return {"a": [round(a[0], 1), round(a[1], 1)], "b": [round(b[0], 1), round(b[1], 1)], "t": round(t, 1)}


def wdir(w):
    dx, dy = w["b"][0] - w["a"][0], w["b"][1] - w["a"][1]
    L = math.hypot(dx, dy)
    return (dx / L, dy / L), L


# ---------------------------------------------------------------- openings

def collinear(w1, w2, tol=15.0, ang_tol=0.01):
    (u1, _), (u2, _) = wdir(w1), wdir(w2)
    if abs(u1[0] * u2[1] - u1[1] * u2[0]) > ang_tol:
        return False
    # perpendicular offset of w2's axis from w1's
    nx, ny = -u1[1], u1[0]
    off = (w2["a"][0] - w1["a"][0]) * nx + (w2["a"][1] - w1["a"][1]) * ny
    return abs(off) <= tol and abs(w1["t"] - w2["t"]) <= 12


def proj(w, p):
    (u, _) = wdir(w)
    return (p[0] - w["a"][0]) * u[0] + (p[1] - w["a"][1]) * u[1]


def seg_box_hit(seg, box):
    (x1, y1), (x2, y2) = seg
    bx0, by0, bx1, by1 = box
    # clip test (Liang-Barsky)
    t0, t1 = 0.0, 1.0
    dx, dy = x2 - x1, y2 - y1
    for p, q in ((-dx, x1 - bx0), (dx, bx1 - x1), (-dy, y1 - by0), (dy, by1 - y1)):
        if p == 0:
            if q < 0:
                return False
        else:
            r = q / p
            if p < 0:
                t0 = max(t0, r)
            else:
                t1 = min(t1, r)
            if t0 > t1:
                return False
    return True


def flatten(e):
    from ezdxf import path as ezpath
    try:
        return [(v.x, v.y) for v in ezpath.make_path(e).flattening(5)]
    except Exception:
        return []


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("dxf")
    ap.add_argument("--out", required=True)
    ap.add_argument("--config", default=None)
    ap.add_argument("--schedule", default=None, help="JSON {tag: {kind, width, height, sill, desc}}")
    ap.add_argument("--render", default=None)
    ap.add_argument("--below", default=None, help="plan JSON of the floor below: snap columns within 150 mm onto its columns")
    a = ap.parse_args()
    cfg = load_cfg(a.config)
    sched = json.load(open(a.schedule, encoding="utf-8")) if a.schedule else {}
    doc = ezdxf.readfile(a.dxf)
    msp = doc.modelspace()

    # ---- wall + column regions
    walls, cols, notes = [], [], []
    hatch_rects = []
    for h in msp.query("HATCH"):
        L, pat = lay(h), h.dxf.pattern_name.upper()
        loops = ocs_fix(h, hatch_loops(h))
        is_col_layer = re.search(cfg["column_layers"], L)
        if is_col_layer or (h.dxf.solid_fill and not re.search(cfg["wall_hatch_layers"], L)):
            for lp in loops:
                xs, ys = [p[0] for p in lp], [p[1] for p in lp]
                w, d = max(xs) - min(xs), max(ys) - min(ys)
                if is_col_layer and 100 <= min(w, d) and max(w, d) <= cfg["column_max"]:
                    cols.append({"c": [round((max(xs) + min(xs)) / 2, 1), round((max(ys) + min(ys)) / 2, 1)],
                                 "w": round(w), "d": round(d), "src": "hatch " + h.dxf.layer})
            continue
        if not re.search(cfg["wall_hatch_layers"], L) and not (L == "0" and re.search(cfg["wall_hatch_patterns"], pat)):
            continue
        if re.search(cfg["wall_hatch_exclude_patterns"], pat) or not re.search(cfg["wall_hatch_patterns"], pat):
            notes.append("hatch {} pattern {} skipped".format(h.dxf.layer, pat))
            continue
        for lp in loops:
            for r in rects_of_loop(lp):
                hatch_rects.append(r)
    # closed column squares (no hatch)
    for pl in msp.query("LWPOLYLINE"):
        if re.search(cfg["column_layers"], lay(pl)) and pl.closed:
            pts = [(p[0], p[1]) for p in pl.get_points("xy")]
            xs, ys = [p[0] for p in pts], [p[1] for p in pts]
            w, d = max(xs) - min(xs), max(ys) - min(ys)
            c = ((max(xs) + min(xs)) / 2, (max(ys) + min(ys)) / 2)
            if 100 <= min(w, d) and max(w, d) <= cfg["column_max"] and not any(math.hypot(c[0] - o["c"][0], c[1] - o["c"][1]) < 20 for o in cols):
                cols.append({"c": [round(c[0], 1), round(c[1], 1)], "w": round(w), "d": round(d), "src": "outline " + pl.dxf.layer})

    if cfg.get("wall_outline_layers"):
        for pl in msp.query("LWPOLYLINE"):
            if not pl.closed or not re.search(cfg["wall_outline_layers"], lay(pl)):
                continue
            pts = [(p_[0], p_[1]) for p_ in pl.get_points("xy")]
            rs = rects_of_loop(pts)
            if not rs:
                continue
            thin = all(cfg["min_thickness"] <= min(r[2] - r[0], r[3] - r[1]) <= cfg.get("outline_max_thickness", 200) for r in rs)
            longish = max(max(r[2] - r[0], r[3] - r[1]) for r in rs) >= 1000
            if thin and longish:
                # skip outlines already covered by hatches
                cx = sum(p_[0] for p_ in pts) / len(pts)
                cy = sum(p_[1] for p_ in pts) / len(pts)
                if any(abs(h[0] - r[0]) < 5 and abs(h[1] - r[1]) < 5 and abs(h[2] - r[2]) < 5 and abs(h[3] - r[3]) < 5 for h in hatch_rects for r in rs):
                    continue
                hatch_rects.extend(rs)
                notes.append("walls from outline on layer {} ({} parts)".format(pl.dxf.layer, len(rs)))
    if cfg.get("column_blocks"):
        for ins in msp.query("INSERT"):
            if re.search(cfg["column_blocks"], ins.dxf.name):
                b = cadlib.ext([ins])
                if not b:
                    continue
                w, d = b[2] - b[0], b[3] - b[1]
                if 50 <= min(w, d) and max(w, d) <= cfg["column_max"]:
                    cols.append({"c": [round((b[0] + b[2]) / 2, 1), round((b[1] + b[3]) / 2, 1)], "w": round(w), "d": round(d),
                                 "src": "block " + ins.dxf.name, "kind": cfg.get("column_block_kind", "steel")})
    for ew in cfg.get("extra_walls", []):
        walls.append({"a": list(ew["a"]), "b": list(ew["b"]), "t": ew["t"], "src": "config"})

    # stubs (aspect < 2): orient them like the long wall they line up with
    longs = [r for r in hatch_rects if max(r[2] - r[0], r[3] - r[1]) / max(1.0, min(r[2] - r[0], r[3] - r[1])) >= 2.0]

    def lines_up(r):
        """'x' if a long x-running wall shares r's y-band, 'y' for a y-running one, else None."""
        x0, y0, x1, y1, ang = r
        w, h = x1 - x0, y1 - y0
        for o in longs:
            if abs(o[4] - ang) > 1e-3:
                continue
            ow, oh = o[2] - o[0], o[3] - o[1]
            if ow >= oh and abs(oh - h) < 12 and abs((o[1] + o[3]) / 2 - (y0 + y1) / 2) < 15:
                return "x"
            if oh > ow and abs(ow - w) < 12 and abs((o[0] + o[2]) / 2 - (x0 + x1) / 2) < 15:
                return "y"
        return None

    for r in hatch_rects:
        x0, y0, x1, y1, ang = r
        w, h = x1 - x0, y1 - y0
        t = min(w, h)
        if t < cfg["min_thickness"] * 0.5:
            continue
        stub = max(w, h) / max(1.0, t) < 2.0
        along = lines_up(r) if stub else None
        if stub and along is None:
            if max(w, h) / max(1.0, t) < 1.35 and t >= 150 and max(w, h) <= cfg["column_max"]:
                c = rotate([((x0 + x1) / 2, (y0 + y1) / 2)], ang)[0]
                if not any(math.hypot(c[0] - o["c"][0], c[1] - o["c"][1]) < 50 for o in cols):
                    cols.append({"c": [round(c[0], 1), round(c[1], 1)], "w": round(w), "d": round(h), "src": "square wall hatch"})
                continue
        if along == "x":
            a_, b_ = rotate([(x0, (y0 + y1) / 2), (x1, (y0 + y1) / 2)], ang)
            walls.append({"a": [round(a_[0], 1), round(a_[1], 1)], "b": [round(b_[0], 1), round(b_[1], 1)], "t": round(h, 1), "stub": True})
            continue
        if along == "y":
            a_, b_ = rotate([((x0 + x1) / 2, y0), ((x0 + x1) / 2, y1)], ang)
            walls.append({"a": [round(a_[0], 1), round(a_[1], 1)], "b": [round(b_[0], 1), round(b_[1], 1)], "t": round(w, 1), "stub": True})
            continue
        if t > cfg["max_thickness"]:
            notes.append("region {:.0f}x{:.0f} too thick for a wall - left out".format(w, h))
            continue
        walls.append(rect_to_wall(r))

    # ---- door / window evidence
    door_ents = [e for e in msp if re.search(cfg["door_layers"], lay(e)) or re.search(cfg["window_layers"], lay(e))]
    arcs = []   # (center, radius, start_pt, end_pt)
    segs = []   # line work of openings

    def explode(e, depth=0):
        t = e.dxftype()
        if t in ("INSERT", "LWPOLYLINE", "POLYLINE") and depth < 4:
            try:
                out = []
                for ve in e.virtual_entities():
                    out += explode(ve, depth + 1)
                return out
            except Exception:
                return [e]
        return [e]

    for e in door_ents:
        for ve in explode(e):
            t = ve.dxftype()
            if t == "ARC":
                c, r = ve.ocs().to_wcs(ve.dxf.center), ve.dxf.radius      # mirrored blocks: centre is in OCS
                if 250 <= r <= 1600:
                    arcs.append(((c.x, c.y), r, (ve.start_point.x, ve.start_point.y), (ve.end_point.x, ve.end_point.y)))
            elif t == "ELLIPSE" and ve.dxf.ratio > 0.9:
                c, r = ve.dxf.center, ve.dxf.major_axis.magnitude
                if 250 <= r <= 1600:
                    sp, ep = ve.start_point, ve.end_point
                    arcs.append(((c.x, c.y), r, (sp.x, sp.y), (ep.x, ep.y)))
            if t in ("LINE", "ARC", "ELLIPSE"):
                pts = flatten(ve)
                segs += [(pts[i], pts[i + 1]) for i in range(len(pts) - 1)]
    tags = []
    for e in msp.query("TEXT MTEXT"):
        s_ = cadlib.text_of(e).strip().upper().replace(" ", "")
        if re.match(cfg["tag_regex"], s_):
            p_ = e.dxf.insert
            tags.append((s_.replace("-", ""), (p_.x, p_.y)))
    for e in msp.query("INSERT"):
        for at in e.attribs:
            s_ = cadlib.decode(at.dxf.text).strip().upper().replace(" ", "")
            if re.match(cfg["tag_regex"], s_):
                p_ = at.dxf.insert
                tags.append((s_.replace("-", ""), (p_.x, p_.y)))

    # ---- openings: from every wall end, the nearest solid along the axis
    pieces = [dict(w) for w in walls]

    def solids_along(w, sgn):
        """Sorted (distance from the end, kind, index) of the solids in the wall's band beyond one end."""
        (u, L) = wdir(w)
        nx, ny = -u[1], u[0]
        endpos = L if sgn > 0 else 0.0
        out = []
        for k, c in enumerate(cols):
            off = abs((c["c"][0] - w["a"][0]) * nx + (c["c"][1] - w["a"][1]) * ny)
            half_n = (abs(nx) * c["w"] + abs(ny) * c["d"]) / 2.0
            if off > w["t"] / 2 + half_n - 5:
                continue
            s_ = proj(w, c["c"])
            half = (abs(u[0]) * c["w"] + abs(u[1]) * c["d"]) / 2.0
            near = (s_ - half - endpos) if sgn > 0 else (endpos - (s_ + half))
            far = (s_ + half - endpos) if sgn > 0 else (endpos - (s_ - half))
            if far > 0:
                out.append((near, "col", k))
        for k, o in enumerate(pieces):
            if o is w:
                continue
            (uo, Lo) = wdir(o)
            if abs(uo[0] * u[1] - uo[1] * u[0]) < 0.01:      # parallel
                off = abs((o["a"][0] - w["a"][0]) * nx + (o["a"][1] - w["a"][1]) * ny)
                if off > (w["t"] + o["t"]) / 2 - 5:
                    continue
                sa, sb = sorted((proj(w, o["a"]), proj(w, o["b"])))
                near = (sa - endpos) if sgn > 0 else (endpos - sb)
                far = (sb - endpos) if sgn > 0 else (endpos - sa)
                if far > 0:
                    out.append((near, "wall", k))
            else:                                             # crossing / perpendicular
                offa = (o["a"][0] - w["a"][0]) * nx + (o["a"][1] - w["a"][1]) * ny
                offb = (o["b"][0] - w["a"][0]) * nx + (o["b"][1] - w["a"][1]) * ny
                if min(offa, offb) > w["t"] / 2 + o["t"] / 2 or max(offa, offb) < -w["t"] / 2 - o["t"] / 2:
                    continue
                s_ = proj(w, o["a"])
                near = (s_ - o["t"] / 2 - endpos) if sgn > 0 else (endpos - (s_ + o["t"] / 2))
                far = (s_ + o["t"] / 2 - endpos) if sgn > 0 else (endpos - (s_ - o["t"] / 2))
                if far > 0:
                    out.append((near, "cross", k))
        out.sort()
        return out

    def evidence(w, g0, g1):
        (u, L) = wdir(w)
        nx, ny = -u[1], u[0]
        gap = g1 - g0
        mid = (w["a"][0] + u[0] * (g0 + g1) / 2, w["a"][1] + u[1] * (g0 + g1) / 2)
        corners = [(w["a"][0] + u[0] * s_ + nx * o, w["a"][1] + u[1] * s_ + ny * o)
                   for s_ in (g0 + 5, g1 - 5) for o in (-w["t"] / 2 - 5, w["t"] / 2 + 5)]
        box = (min(c[0] for c in corners), min(c[1] for c in corners), max(c[0] for c in corners), max(c[1] for c in corners))
        inside = [sg for sg in segs if seg_box_hit(sg, box)]

        def in_band(q):
            return abs((q[0] - w["a"][0]) * nx + (q[1] - w["a"][1]) * ny) <= w["t"] / 2 + 15

        # window: 2+ lines parallel to the wall inside its band, together covering most of the gap
        bylines = collections.defaultdict(list)
        for sg in inside:
            Ls = math.hypot(sg[1][0] - sg[0][0], sg[1][1] - sg[0][1])
            if Ls < 1 or not (in_band(sg[0]) and in_band(sg[1])):
                continue
            if abs((sg[1][0] - sg[0][0]) * u[1] - (sg[1][1] - sg[0][1]) * u[0]) > 0.05 * Ls:
                continue
            off = round(((sg[0][0] - w["a"][0]) * nx + (sg[0][1] - w["a"][1]) * ny) / 5.0)
            lo, hi = sorted((proj(w, sg[0]), proj(w, sg[1])))
            bylines[off].append((max(lo, g0), min(hi, g1)))
        par = []
        for off, iv in bylines.items():
            iv = sorted(x for x in iv if x[1] > x[0])
            cov, cur = 0.0, None
            for lo, hi in iv:
                if cur is None or lo > cur[1]:
                    if cur:
                        cov += cur[1] - cur[0]
                    cur = [lo, hi]
                else:
                    cur[1] = max(cur[1], hi)
            if cur:
                cov += cur[1] - cur[0]
            if cov >= 0.8 * gap:
                par.append(off)
        # door: a swing arc hinged at one end of the gap (double doors: both ends)
        door_arcs = [ar for ar in arcs
                     if min(abs(proj(w, ar[0]) - g0), abs(proj(w, ar[0]) - g1)) < 180
                     and abs((ar[0][0] - w["a"][0]) * nx + (ar[0][1] - w["a"][1]) * ny) < w["t"] / 2 + 150
                     and 0.3 * gap <= ar[1] <= gap + 100
                     # the closed-leaf end of the arc lies in the wall, inside the gap
                     and any(abs((q[0] - w["a"][0]) * nx + (q[1] - w["a"][1]) * ny) <= w["t"] / 2 + 40 and g0 - 60 <= proj(w, q) <= g1 + 60
                             for q in (ar[2], ar[3]))]
        if not door_arcs and len(par) < 2:
            return None
        op = {"center": [round(mid[0], 1), round(mid[1], 1)], "gap": round(gap), "dir": [round(u[0], 4), round(u[1], 4)],
              "thickness": w["t"], "kind": "door" if door_arcs else ("window" if len(par) >= 2 else "opening")}
        if door_arcs:
            door_arcs.sort(key=lambda ar: -ar[1])
            ac = door_arcs[0]
            op["leaves"] = 2 if len(door_arcs) >= 2 and abs(door_arcs[0][1] - door_arcs[1][1]) < 80 and \
                math.hypot(door_arcs[0][0][0] - door_arcs[1][0][0], door_arcs[0][0][1] - door_arcs[1][0][1]) > 0.5 * gap else 1
            far = max((ac[2], ac[3]), key=lambda q: abs((q[0] - mid[0]) * nx + (q[1] - mid[1]) * ny))
            side = (far[0] - mid[0]) * nx + (far[1] - mid[1]) * ny
            op["swing_normal"] = [round(nx * (1 if side > 0 else -1), 4), round(ny * (1 if side > 0 else -1), 4)]
            op["hinge"] = [round(ac[0][0], 1), round(ac[0][1], 1)]
            op["leaf"] = round(ac[1])
        return op

    def span(w, lo, hi):
        (u, L) = wdir(w)
        a0 = (w["a"][0] + u[0] * lo, w["a"][1] + u[1] * lo)
        b0 = (w["a"][0] + u[0] * hi, w["a"][1] + u[1] * hi)
        return [round(a0[0], 1), round(a0[1], 1)], [round(b0[0], 1), round(b0[1], 1)]

    changed = True
    guard = 0
    while changed and guard < 500:
        changed = False
        guard += 1
        # union overlapping / touching collinear pieces of the same thickness
        for i, w1 in enumerate(pieces):
            for j, w2 in enumerate(pieces):
                if j <= i or not collinear(w1, w2):
                    continue
                (u, L1) = wdir(w1)
                sa, sb = sorted((proj(w1, w2["a"]), proj(w1, w2["b"])))
                if sa <= L1 + 1 and sb >= -1:
                    a0, b0 = span(w1, min(0.0, sa), max(L1, sb))
                    pieces[i] = dict(w1, a=a0, b=b0, openings=w1.get("openings", []) + w2.get("openings", []), stub=False)
                    pieces.pop(j)
                    changed = True
                    break
            if changed:
                break
        if changed:
            continue
        for i, w in enumerate(pieces):
            (u, L) = wdir(w)
            for sgn in (1, -1):
                hits = solids_along(w, sgn)
                if not hits:
                    continue
                g, kind, k = hits[0]
                if g <= 5 or g > cfg["max_opening"]:
                    continue
                g0, g1 = (L, L + g) if sgn > 0 else (-g, 0.0)
                op = evidence(w, g0, g1)
                if op is None:
                    continue
                if kind == "wall" and collinear(w, pieces[k]):
                    o = pieces[k]
                    sa, sb = sorted((proj(w, o["a"]), proj(w, o["b"])))
                    a0, b0 = span(w, min(0.0, sa), max(L, sb))
                    pieces[i] = dict(w, a=a0, b=b0, stub=False, openings=w.get("openings", []) + o.get("openings", []) + [op])
                    pieces.pop(k)
                else:
                    a0, b0 = span(w, min(0.0, g0), max(L, g1))
                    op["at"] = kind
                    pieces[i] = dict(w, a=a0, b=b0, stub=False, openings=w.get("openings", []) + [op])
                changed = True
                break
            if changed:
                break

    # ---- tags: each tag text goes to its nearest opening (one tag, one opening)
    all_ops = [o for w in pieces for o in w.get("openings", [])]
    for tg, pos in tags:
        best = min(all_ops, key=lambda o: math.hypot(o["center"][0] - pos[0], o["center"][1] - pos[1])) if all_ops else None
        if best is None:
            continue
        d = math.hypot(best["center"][0] - pos[0], best["center"][1] - pos[1])
        if d > 2500 + best["gap"] / 2:
            notes.append("tag {} at ({:.0f}, {:.0f}) has no opening near it".format(tg, pos[0], pos[1]))
            continue
        if best.get("tag_d") is None or d < best["tag_d"]:
            best["tag"], best["tag_d"] = tg, round(d)
    for o in all_ops:
        tg = o.get("tag")
        if tg and tg in sched:
            o["kind"] = sched[tg].get("kind", o["kind"])
        elif tg and tg[0] in "SW":
            o["kind"] = "window"

    # drop slivers, then parallel duplicates (a hatch and an outline of the same wall): keep the longer
    pieces = [w for w in pieces if w["t"] >= cfg["min_thickness"] or w.get("src") == "config"]
    pieces.sort(key=lambda w: (-wdir(w)[1], -w["t"]))
    kept = []
    for w in pieces:
        (u, L) = wdir(w)
        dup = None
        for k in kept:
            (uk, Lk) = wdir(k)
            if abs(u[0] * uk[1] - u[1] * uk[0]) > 0.01:
                continue
            off = abs((w["a"][0] - k["a"][0]) * -uk[1] + (w["a"][1] - k["a"][1]) * uk[0])
            if off > max(w["t"], k["t"]) / 2 + 20:
                continue
            sa, sb = sorted((proj(k, w["a"]), proj(k, w["b"])))
            ov = min(sb, Lk) - max(sa, 0.0)
            if ov <= 0:
                continue
            if off <= 15 and abs(w["t"] - k["t"]) <= 60 and ov >= 0.6 * min(L, Lk):
                dup = k
                break
            # different walls sharing a stretch (a wall that thickens): the thicker keeps the overlap
            thin, thick = (w, k) if w["t"] < k["t"] else (k, w)
            (ut, Lt) = wdir(thin)
            ta, tb = sorted((proj(thin, thick["a"]), proj(thin, thick["b"])))
            if ta <= 5 and tb < Lt - 5:
                a0, b0 = span(thin, tb, Lt)
                thin["a"], thin["b"] = a0, b0
            elif tb >= Lt - 5 and ta > 5:
                a0, b0 = span(thin, 0.0, ta)
                thin["a"], thin["b"] = a0, b0
        if dup is None:
            kept.append(w)
        else:
            dup.setdefault("openings", []).extend(w.get("openings", []))
            notes.append("duplicate wall {}-{} t{} merged into {}-{} t{}".format(w["a"], w["b"], w["t"], dup["a"], dup["b"], dup["t"]))
    pieces = kept

    for q in cfg.get("drop_walls_near", []):
        keep = []
        for w in pieces:
            (u, L) = wdir(w)
            sp = proj(w, q)
            off = abs((q[0] - w["a"][0]) * -u[1] + (q[1] - w["a"][1]) * u[0])
            if -50 <= sp <= L + 50 and off <= 150:
                notes.append("dropped wall {} - {} (config)".format(w["a"], w["b"]))
                continue
            keep.append(w)
        pieces = keep

    # ---- snap near-orthogonal walls, number everything
    for k, w in enumerate(pieces):
        w["id"] = "W{:03d}".format(k + 1)
        dx, dy = w["b"][0] - w["a"][0], w["b"][1] - w["a"][1]
        ang = math.degrees(math.atan2(dy, dx)) % 90
        if min(ang, 90 - ang) < cfg["snap_deg"] and min(ang, 90 - ang) > 0:
            if abs(dx) > abs(dy):
                y = (w["a"][1] + w["b"][1]) / 2
                w["a"][1] = w["b"][1] = round(y, 1)
            else:
                x = (w["a"][0] + w["b"][0]) / 2
                w["a"][0] = w["b"][0] = round(x, 1)
        for n, o in enumerate(w.get("openings", [])):
            o["host"] = w["id"]
            o["id"] = "{}-O{}".format(w["id"], n + 1)
            if o.get("tag") in sched:
                o.update({k2: v for k2, v in sched[o["tag"]].items() if k2 not in ("kind",)})
    def dist_to_walls(c):
        best = 1e18
        for w in pieces:
            (u, L) = wdir(w)
            sp = min(max(proj(w, c["c"]), 0.0), L)
            q = (w["a"][0] + u[0] * sp, w["a"][1] + u[1] * sp)
            best = min(best, math.hypot(c["c"][0] - q[0], c["c"][1] - q[1]))
        return best
    m = cfg.get("column_margin", 2500)
    if pieces:
        bx0 = min(min(w["a"][0], w["b"][0]) for w in pieces) - m
        bx1 = max(max(w["a"][0], w["b"][0]) for w in pieces) + m
        by0 = min(min(w["a"][1], w["b"][1]) for w in pieces) - m
        by1 = max(max(w["a"][1], w["b"][1]) for w in pieces) + m
    far = [c for c in cols if pieces and not (bx0 <= c["c"][0] <= bx1 and by0 <= c["c"][1] <= by1)]
    for c in far:
        notes.append("column at {} is outside the walls' extents - left out (legend symbol?)".format(c["c"]))
    cols = [c for c in cols if c not in far]
    if a.below:
        below = json.load(open(a.below, encoding="utf-8"))["columns"]
        for c in cols:
            near = min(below, key=lambda b: math.hypot(b["c"][0] - c["c"][0], b["c"][1] - c["c"][1]), default=None)
            if near and math.hypot(near["c"][0] - c["c"][0], near["c"][1] - c["c"][1]) <= 150:
                c["snapped_from"] = list(c["c"])
                c["c"] = list(near["c"])
    for k, c in enumerate(cols):
        c["id"] = "C{:03d}".format(k + 1)
    ops = [o for w in pieces for o in w.get("openings", [])]
    res = {"source": a.dxf, "walls": pieces, "columns": cols, "notes": notes,
           "summary": {"walls": len(pieces), "columns": len(cols),
                       "openings": dict(collections.Counter(o["kind"] for o in ops)),
                       "thickness": dict(collections.Counter(int(round(w["t"])) for w in pieces)),
                       "tags": dict(collections.Counter(o.get("tag") for o in ops)),
                       "untagged": [o["id"] for o in ops if not o.get("tag")]}}
    with open(a.out, "w", encoding="utf-8") as f:
        json.dump(res, f, ensure_ascii=False, indent=1)
    print(json.dumps(res["summary"], ensure_ascii=False))
    for n in notes[:10]:
        print("note: " + n)
    if a.render:
        render(doc, res, a.render)


def render(doc, res, out):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from ezdxf.addons.drawing import Frontend, RenderContext
    from ezdxf.addons.drawing.config import BackgroundPolicy, ColorPolicy, Configuration
    from ezdxf.addons.drawing.matplotlib import MatplotlibBackend
    fig = plt.figure(figsize=(16, 13), dpi=100)
    ax = fig.add_axes([0.01, 0.01, 0.98, 0.98])
    cfg = Configuration(background_policy=BackgroundPolicy.WHITE, color_policy=ColorPolicy.MONOCHROME_LIGHT_BG)
    try:
        Frontend(RenderContext(doc), MatplotlibBackend(ax), config=cfg).draw_layout(doc.modelspace(), finalize=False)
    except Exception:
        pass
    for ln in ax.lines + list(ax.collections):
        try:
            ln.set_alpha(0.25)
        except Exception:
            pass
    colors = {100: "tab:blue", 200: "tab:red", 110: "tab:blue", 220: "tab:red"}
    for w in res["walls"]:
        c = colors.get(int(round(w["t"])), "tab:purple")
        ax.plot([w["a"][0], w["b"][0]], [w["a"][1], w["b"][1]], color=c, lw=max(1.0, w["t"] / 40.0), solid_capstyle="butt")
        mx, my = (w["a"][0] + w["b"][0]) / 2, (w["a"][1] + w["b"][1]) / 2
        ax.text(mx, my, w["id"][1:], fontsize=5, color="k")
        for o in w.get("openings", []):
            oc = {"door": "tab:green", "window": "tab:cyan", "opening": "tab:orange"}.get(o["kind"], "k")
            u = o["dir"]
            half = o.get("width", o["gap"]) / 2.0
            ax.plot([o["center"][0] - u[0] * half, o["center"][0] + u[0] * half], [o["center"][1] - u[1] * half, o["center"][1] + u[1] * half],
                    color=oc, lw=4, solid_capstyle="butt")
            ax.text(o["center"][0], o["center"][1] + 150, o.get("tag") or "?", fontsize=6, color=oc)
            if o.get("swing_normal"):
                sn = o["swing_normal"]
                ax.annotate("", xy=(o["center"][0] + sn[0] * 500, o["center"][1] + sn[1] * 500), xytext=tuple(o["center"]),
                            arrowprops=dict(arrowstyle="->", color="tab:green", lw=0.8))
    for c in res["columns"]:
        ax.add_patch(plt.Rectangle((c["c"][0] - c["w"] / 2, c["c"][1] - c["d"] / 2), c["w"], c["d"], color="k"))
    xs = [p for w in res["walls"] for p in (w["a"][0], w["b"][0])]
    ys = [p for w in res["walls"] for p in (w["a"][1], w["b"][1])]
    if xs:
        ax.set_xlim(min(xs) - 1500, max(xs) + 1500)
        ax.set_ylim(min(ys) - 1500, max(ys) + 1500)
    ax.set_aspect("equal")
    fig.savefig(out, facecolor="white")
    print("rendered " + out)


if __name__ == "__main__":
    main()
