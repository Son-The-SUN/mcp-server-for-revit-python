"""Plan one floor slab per level from ifc_slabs.json (extract_slabs.py): clean the outline, decide the voids and
take type / offset / structural from the user's example slab.

    python plan_slab.py L10 --example example_slabs.json:L10 [--config slab_config.json]
    -> slab_L10.json (+ a summary on stdout)

Outline clean-up (outer loops):
- loops that touch themselves (a column cut out at a corner) are split; the pinched-off holes are treated like
  any other hole;
- collinear runs are merged, runs of short segments (tessellated IFC mesh arcs) are fitted back to true arcs;
- arcs with a sagitta under 5 mm (a round column grazing the edge) become lines;
- "pockets" - notches cut into an otherwise straight edge (facade columns, recesses) - are closed with the
  straight chord when they are small (pocket_area, pocket_mouth, pocket_depth), as the example slab runs
  straight past them. Notches at a corner (a wall standing in the corner) are closed by extending both edges
  to the corner, only when walls/columns cut at mid-slab cover corner_fill of the added area;
- lines within snap_deg of the host axes are made exactly orthogonal ("slightly off axis" warnings otherwise).

Holes (inner loops left after extract_slabs.py filled the wall/column footprints back in):
- "stair": overlaps an IFC stair that reaches the slab. The void is the hole clipped to the stairs' plan
  rectangle, so floor landings stay slab (the example has only the flight void);
- "penetration": smaller than min_void (m2), or a sliver thinner than min_width (mm);
- "shaft": everything else (lifts, risers).
Only classes in keep_voids become voids of the floor; the rest are listed in the plan.

Config (all optional; --example fills floor_type/offset/structural from the example floor):
    {"floor_type": "CONCRETE 220MM", "type_source": "CONCRETE 200MM", "offset": -20, "structural": true,
     "keep_voids": ["stair"], "stair_bridge": 300, "min_void": 0.5, "min_width": 150, "min_island": 2.0,
     "pocket_area": 6.0, "pocket_mouth": 3500, "pocket_depth": 2500, "corner_fill": 0.5, "arc_tol": 5, "snap_deg": 0.2}
"""
import json
import math
import sys

DEFAULTS = {"floor_type": None, "type_source": "CONCRETE 200MM", "offset": 0.0, "structural": True,
            "keep_voids": ["stair"], "min_void": 0.5, "min_width": 150.0, "min_island": 2.0, "stair_bridge": 300.0,
            "pocket_area": 6.0, "pocket_mouth": 3500.0, "pocket_depth": 2500.0, "corner_fill": 0.5, "arc_tol": 5.0,
            "snap_deg": 0.2}


# ---------------------------------------------------------------- geometry helpers (host mm, 2D)

def sub(a, b):
    return (a[0] - b[0], a[1] - b[1])


def cross(a, b):
    return a[0] * b[1] - a[1] * b[0]


def dot(a, b):
    return a[0] * b[0] + a[1] * b[1]


def dist(a, b):
    return math.hypot(a[0] - b[0], a[1] - b[1])


def area(pts):
    return sum(cross(pts[i], pts[(i + 1) % len(pts)]) for i in range(len(pts))) / 2.0


def arc_points(c, r, p0, p1, pm, n=None):
    """Points along an arc (c, r) from p0 through pm to p1 (p0 excluded, p1 included)."""
    a0 = math.atan2(p0[1] - c[1], p0[0] - c[0])
    a1 = math.atan2(p1[1] - c[1], p1[0] - c[0])
    am = math.atan2(pm[1] - c[1], pm[0] - c[0])

    def nrm(a):
        return a % (2 * math.pi)
    span = nrm(a1 - a0)
    if nrm(am - a0) > span:
        span -= 2 * math.pi
    n = n or max(4, int(abs(span) * r / 150.0))
    return [(c[0] + r * math.cos(a0 + span * k / float(n)), c[1] + r * math.sin(a0 + span * k / float(n))) for k in range(1, n + 1)]


def point_in(p, pts):
    c = False
    for i in range(len(pts)):
        a, b = pts[i], pts[(i + 1) % len(pts)]
        if (a[1] > p[1]) != (b[1] > p[1]) and p[0] < a[0] + (p[1] - a[1]) * (b[0] - a[0]) / (b[1] - a[1]):
            c = not c
    return c


def circle3(a, b, c):
    d = 2 * (a[0] * (b[1] - c[1]) + b[0] * (c[1] - a[1]) + c[0] * (a[1] - b[1]))
    if abs(d) < 1e-9:
        return None
    ux = ((a[0] ** 2 + a[1] ** 2) * (b[1] - c[1]) + (b[0] ** 2 + b[1] ** 2) * (c[1] - a[1]) + (c[0] ** 2 + c[1] ** 2) * (a[1] - b[1])) / d
    uy = ((a[0] ** 2 + a[1] ** 2) * (c[0] - b[0]) + (b[0] ** 2 + b[1] ** 2) * (a[0] - c[0]) + (c[0] ** 2 + c[1] ** 2) * (b[0] - a[0])) / d
    return (ux, uy), dist((ux, uy), a)


def clip_convex(poly, clip):
    """Sutherland-Hodgman: polygon poly clipped by the convex CCW polygon clip."""
    out = poly
    for i in range(len(clip)):
        a, b = clip[i], clip[(i + 1) % len(clip)]
        inp, out = out, []
        if not inp:
            break
        for j in range(len(inp)):
            p, q = inp[j], inp[(j + 1) % len(inp)]
            pin = cross(sub(b, a), sub(p, a)) >= 0
            qin = cross(sub(b, a), sub(q, a)) >= 0
            if pin:
                out.append(p)
            if pin != qin:
                d1, d2 = cross(sub(b, a), sub(p, a)), cross(sub(b, a), sub(q, a))
                t = d1 / (d1 - d2)
                out.append((p[0] + (q[0] - p[0]) * t, p[1] + (q[1] - p[1]) * t))
    return out


def hull(pts):
    pts = sorted(set((round(p[0], 1), round(p[1], 1)) for p in pts))
    if len(pts) < 3:
        return pts
    lo, hi = [], []
    for p in pts:
        while len(lo) >= 2 and cross(sub(lo[-1], lo[-2]), sub(p, lo[-2])) <= 0:
            lo.pop()
        lo.append(p)
    for p in reversed(pts):
        while len(hi) >= 2 and cross(sub(hi[-1], hi[-2]), sub(p, hi[-2])) <= 0:
            hi.pop()
        hi.append(p)
    return lo[:-1] + hi[:-1]


def min_rect(pts):
    """Minimum-area rectangle around points (rotating calipers over the hull) -> CCW corners."""
    h = hull(pts)
    best = None
    for i in range(len(h)):
        e = sub(h[(i + 1) % len(h)], h[i])
        L = math.hypot(*e)
        if L < 1e-6:
            continue
        u = (e[0] / L, e[1] / L)
        v = (-u[1], u[0])
        us = [dot(p, u) for p in h]
        vs = [dot(p, v) for p in h]
        a = (max(us) - min(us)) * (max(vs) - min(vs))
        if best is None or a < best[0]:
            best = (a, u, v, min(us), max(us), min(vs), max(vs))
    a, u, v, u0, u1, v0, v1 = best
    return [(u[0] * s + v[0] * t, u[1] * s + v[1] * t) for s, t in ((u0, v0), (u1, v0), (u1, v1), (u0, v1))]


def rect_union(rects, bridge):
    """Union of rectangles sharing one orientation, with gaps narrower than `bridge` between neighbours filled
    (a scissor stair's two flights and the wall between them become one void). Coordinate-compressed grid,
    boundary traced into CCW loops. Returns a list of point loops, or None if the rectangles are not aligned."""
    r0 = rects[0]
    e = sub(r0[1], r0[0])
    u = (e[0] / math.hypot(*e), e[1] / math.hypot(*e))
    v = (-u[1], u[0])
    boxes = []
    for r in rects:
        su = [dot(p, u) for p in r]
        sv = [dot(p, v) for p in r]
        box = (min(su), min(sv), max(su), max(sv))
        if abs((box[2] - box[0]) * (box[3] - box[1]) - abs(area(r))) > 0.01 * abs(area(r)) + 1:
            return None                              # not aligned with the first rectangle
        boxes.append(box)
    extra = []
    for i in range(len(boxes)):
        for j in range(i + 1, len(boxes)):
            a, b = boxes[i], boxes[j]
            oy = (max(a[1], b[1]), min(a[3], b[3]))
            ox = (max(a[0], b[0]), min(a[2], b[2]))
            if oy[1] > oy[0]:
                gap = (min(a[2], b[2]), max(a[0], b[0]))
                if 0 < gap[1] - gap[0] <= bridge:
                    extra.append((gap[0], oy[0], gap[1], oy[1]))
            if ox[1] > ox[0]:
                gap = (min(a[3], b[3]), max(a[1], b[1]))
                if 0 < gap[1] - gap[0] <= bridge:
                    extra.append((ox[0], gap[0], ox[1], gap[1]))
    boxes += extra
    xs = sorted(set([b[0] for b in boxes] + [b[2] for b in boxes]))
    ys = sorted(set([b[1] for b in boxes] + [b[3] for b in boxes]))
    cov = set()
    for i in range(len(xs) - 1):
        for j in range(len(ys) - 1):
            cx, cy = (xs[i] + xs[i + 1]) / 2, (ys[j] + ys[j + 1]) / 2
            if any(b[0] <= cx <= b[2] and b[1] <= cy <= b[3] for b in boxes):
                cov.add((i, j))
    edges = {}
    for (i, j) in cov:
        for a, b in (((i, j), (i + 1, j)), ((i + 1, j), (i + 1, j + 1)), ((i + 1, j + 1), (i, j + 1)), ((i, j + 1), (i, j))):
            if (b, a) in edges:
                del edges[(b, a)]
            else:
                edges[(a, b)] = True
    nxt = {}
    for a, b in edges:
        nxt.setdefault(a, []).append(b)
    loops, used = [], set()
    for a0, b0 in list(edges):
        if (a0, b0) in used:
            continue
        lp, a, b = [a0], a0, b0
        used.add((a0, b0))
        while b != a0:
            lp.append(b)
            c = [w for w in nxt[b] if (b, w) not in used][0]
            used.add((b, c))
            a, b = b, c
        pts = [(u[0] * xs[i] + v[0] * ys[j], u[1] * xs[i] + v[1] * ys[j]) for i, j in lp]
        loops.append(pts)
    return loops


# ---------------------------------------------------------------- loops as vertex lists with arc tags
# A loop is a list of vertices; vertex k starts the segment k -> k+1. seg[k] is None (line) or an arc
# {"c", "r", "pm"} for that segment.

def loop_from_records(recs):
    V, S = [], []
    for c in recs:
        if "line" in c:
            V.append(tuple(c["line"][0][:2]))
            S.append(None)
        elif "arc" in c:
            cc, r, p0, p1, pm = c["arc"]
            V.append(tuple(p0[:2]))
            S.append({"c": tuple(cc[:2]), "r": r, "pm": tuple(pm[:2])})
        else:
            pts = c["poly"]
            for p in pts[:-1]:
                V.append(tuple(p[:2]))
                S.append(None)
    return V, S


def polyline(V, S):
    pts = []
    for k in range(len(V)):
        pts.append(V[k])
        if S[k] is not None:
            pts.extend(arc_points(S[k]["c"], S[k]["r"], V[k], V[(k + 1) % len(V)], S[k]["pm"])[:-1])
    return pts


def split_pinches(V, S, tol=3.0):
    """Split a loop that visits the same point twice (within tol mm) into simple loops."""
    out, stack = [], [(V, S)]
    while stack:
        V, S = stack.pop()
        done = False
        for k in range(len(V)):
            for i in range(k - 1):
                if dist(V[i], V[k]) < tol and not (i == 0 and k == len(V) - 1):
                    stack.append((V[i:k], S[i:k]))
                    stack.append((V[:i] + V[k:], S[:i] + S[k:]))
                    done = True
                    break
            if done:
                break
        if not done and len(V) >= 3:
            out.append((V, S))
    return out


def merge_collinear(V, S, ang=0.1, dev=1.0):
    changed = True
    while changed and len(V) > 3:
        changed = False
        n = len(V)
        for k in range(n):
            a, b, c = V[k - 1], V[k], V[(k + 1) % n]
            if S[k - 1] is not None or S[k] is not None:
                continue
            ab, bc = sub(b, a), sub(c, b)
            la, lb = math.hypot(*ab), math.hypot(*bc)
            if la < 1e-6 or lb < 1e-6:
                del V[k], S[k]
                changed = True
                break
            sa = abs(cross(ab, bc)) / (la * lb)
            if dot(ab, bc) > 0 and math.degrees(math.asin(min(1.0, sa))) < ang and abs(cross(sub(c, a), sub(b, a))) / dist(a, c) < dev:
                del V[k], S[k]
                changed = True
                break
    return V, S


def fit_arcs(V, S, tol, max_seg=700.0, min_edges=3):
    """Replace runs of >= min_edges short line segments that lie on one circle (within tol) by arcs."""
    n = len(V)
    short = [S[k] is None and dist(V[k], V[(k + 1) % n]) <= max_seg for k in range(n)]
    if all(short):                                   # a loop that is entirely a polygonised circle etc.
        return V, S
    start = next(k for k in range(n) if not short[k])
    order = [(start + 1 + i) % n for i in range(n)]  # begin right after a long edge so runs don't wrap
    newV, newS = [], []
    i = 0
    while i < n:
        k = order[i]
        if not short[k]:
            newV.append(V[k])
            newS.append(S[k])
            i += 1
            continue
        j = i
        while j < n and short[order[j]]:
            j += 1
        run = order[i:j]                           # consecutive short segments
        pts = [V[q] for q in run] + [V[(run[-1] + 1) % n]]
        s = 0
        while s < len(run):
            best = None
            e = s + min_edges
            while e <= len(run):
                p = pts[s:e + 1]
                cr = circle3(p[0], p[len(p) // 2], p[-1])
                if cr is None or cr[1] > 50000:
                    break
                (c, r) = cr
                if max(abs(dist(q, c) - r) for q in p) > tol:
                    break
                # a polygonised arc turns a little, the same way, at every vertex: rules out square corners
                # (concyclic too) and nearly straight runs
                turn = [math.degrees(math.atan2(cross(sub(p[m + 1], p[m]), sub(p[m + 2], p[m + 1])),
                                                dot(sub(p[m + 1], p[m]), sub(p[m + 2], p[m + 1])))) for m in range(len(p) - 2)]
                if not (all(0.3 < t < 30 for t in turn) or all(-30 < t < -0.3 for t in turn)):
                    break
                best = (e, c, r)
                e += 1
            if best:
                e, c, r = best
                p = pts[s:e + 1]
                v0, v1 = sub(p[0], c), sub(p[-1], c)
                if math.degrees(abs(math.atan2(cross(v0, v1), dot(v0, v1)))) < 20:
                    best = None                          # too little sweep to be a real fillet
            if best:
                mid = p[len(p) // 2]
                ang = math.atan2(mid[1] - c[1], mid[0] - c[0])
                newV.append(p[0])
                newS.append({"c": c, "r": r, "pm": (c[0] + r * math.cos(ang), c[1] + r * math.sin(ang))})
                s = e
            else:
                newV.append(pts[s])
                newS.append(None)
                s += 1
        i = j
    return newV, newS


def straightish(V, S, k):
    """Segment k as (start, end) if it is a line or a nearly straight arc (sagitta < 20 mm), else None."""
    n = len(V)
    a, b = V[k % n], V[(k + 1) % n]
    if S[k % n] is not None:
        c, r = dist(a, b), S[k % n]["r"]
        if r - math.sqrt(max(r * r - c * c / 4.0, 0.0)) > 20.0:
            return None
    return a, b


def covered(poly, fills, step=50.0):
    """Fraction of polygon poly covered by the fill polygons (each fill: list of point loops, even-odd)."""
    xs = [p[0] for p in poly]
    ys = [p[1] for p in poly]
    n = hit = 0
    y = min(ys) + step / 2
    while y < max(ys):
        x = min(xs) + step / 2
        while x < max(xs):
            if point_in((x, y), poly):
                n += 1
                for f in fills:
                    if f["bb"][0] <= x <= f["bb"][2] and f["bb"][1] <= y <= f["bb"][3] and \
                            sum(1 for lp in f["loops"] if point_in((x, y), lp)) % 2:
                        hit += 1
                        break
            x += step
        y += step
    return hit / float(n) if n else 0.0


def fill_pockets(V, S, cfg, log, fills):
    """Close notches cut into the slab edge (CCW outer loop: the notch lies to the left of the edges).
    - straight pocket: the edges on both sides are straight (or nearly) and collinear -> closed with the chord
      when small (facade columns, recesses; the example slab runs straight past them);
    - corner pocket: the edges on both sides meet at a corner -> both are extended to the corner, but only when
      walls/columns cut at mid-slab (fills) occupy at least corner_fill of the added area, so real steps in the
      slab edge stay.
    The notch itself may contain arcs (round columns cut out of the edge)."""
    changed = True
    while changed:
        changed = False
        n = len(V)
        for i in range(n):
            ent = straightish(V, S, i - 1)
            if ent is None:
                continue
            a0, a1 = ent
            d = sub(a1, a0)
            L = math.hypot(*d)
            if L < 1e-6:
                continue
            u = (d[0] / L, d[1] / L)
            for m in range(1, min(16, n - 2)):
                j = (i + m) % n
                lv = straightish(V, S, j)
                if lv is None:
                    continue
                b0, b1 = lv
                e = sub(b1, b0)
                Le = math.hypot(*e)
                if Le < 1e-6 or dist(a1, b0) > cfg["pocket_mouth"]:
                    continue
                w = (e[0] / Le, e[1] / Le)
                idx = [(i + q) % n for q in range(m + 1)]
                if m == 1 and S[i] is None:
                    continue                          # a plain segment, nothing to close
                pk = []
                for q in idx[:-1]:
                    pk.append(V[q])
                    if S[q] is not None:
                        pk.extend(arc_points(S[q]["c"], S[q]["r"], V[q], V[(q + 1) % n], S[q]["pm"])[:-1])
                pk.append(V[j])
                if abs(cross(u, w)) <= math.sin(math.radians(1.0)) and dot(u, w) > 0:
                    # straight pocket: chord a1 -> b0 on the edge line, notch on the inner (left) side
                    if abs(cross(u, sub(b0, a1))) > 15.0 or dot(u, sub(b0, a1)) <= 0:
                        continue
                    if min(cross(u, sub(p, a1)) for p in pk) < -15.0:
                        continue                      # goes outside the edge line: a bump, not a notch
                    depth = max(cross(u, sub(p, a1)) for p in pk)
                    ar = abs(area(pk)) / 1e6
                    if ar > cfg["pocket_area"] or depth > cfg["pocket_depth"] or ar < 1e-4:
                        continue
                    log.append({"kind": "straight", "area_m2": round(ar, 3), "mouth": round(dist(a1, b0)),
                                "depth": round(depth), "at": [round(a1[0]), round(a1[1])]})
                    keep = set(idx[1:-1])
                    S[i] = None
                    V[:] = [V[q] for q in range(n) if q not in keep]
                    S[:] = [S[q] for q in range(n) if q not in keep]
                    changed = True
                    break
                if abs(cross(u, w)) <= math.sin(math.radians(10.0)) or not fills:
                    continue
                # corner pocket: both edge lines meet at X = a1 + s*u = b0 - t*w; X may lie on either edge.
                # New loop ... a0 -> X -> b1 ...; accepted when it only grows the slab, by a small area that
                # walls/columns (fills) mostly occupy.
                den = cross(u, w)
                s = cross(sub(b0, a1), w) / den
                t = -cross(sub(b0, a1), u) / den
                if s < -L + 1.0 or t < -Le + 1.0:
                    continue
                X = (a1[0] + u[0] * s, a1[1] + u[1] * s)
                drop = set(idx)                       # a1 .. b0 go, X comes in
                newV, newS = [], []
                for q in range(n):
                    if q in drop:
                        if q == i:
                            newV.append(X)
                            newS.append(None)
                        continue
                    newV.append(V[q])
                    newS.append(None if q == (i - 1) % n else S[q])
                ar = (area(polyline(newV, newS)) - area(polyline(V, S))) / 1e6
                if not (1e-4 < ar <= cfg["pocket_area"]):
                    continue                          # must add area (a notch), and not much of it
                cov = covered([X] + pk, fills)
                if cov < cfg["corner_fill"]:
                    continue
                log.append({"kind": "corner", "area_m2": round(ar, 3), "fill_cover": round(cov, 2),
                            "at": [round(X[0]), round(X[1])]})
                V[:], S[:] = newV, newS
                changed = True
                break
            if changed:
                break
    return V, S


def flatten_tiny_arcs(V, S, sag=5.0):
    for k in range(len(V)):
        if S[k] is not None:
            c, r = dist(V[k], V[(k + 1) % len(V)]), S[k]["r"]
            if r - math.sqrt(max(r * r - c * c / 4.0, 0.0)) < sag:
                S[k] = None
    return V, S


def snap_orthogonal(V, S, deg):
    n = len(V)
    lim = math.sin(math.radians(deg))
    X = [None] * n
    Y = [None] * n
    for k in range(n):
        if S[k] is not None:
            continue
        a, b = V[k], V[(k + 1) % n]
        L = dist(a, b)
        if L < 1e-6:
            continue
        if abs(b[1] - a[1]) / L < lim:
            y = (a[1] + b[1]) / 2
            Y[k] = Y[(k + 1) % n] = y
        elif abs(b[0] - a[0]) / L < lim:
            x = (a[0] + b[0]) / 2
            X[k] = X[(k + 1) % n] = x
    moved = 0.0
    for k in range(n):
        p = (X[k] if X[k] is not None else V[k][0], Y[k] if Y[k] is not None else V[k][1])
        moved = max(moved, dist(p, V[k]))
        V[k] = p
    return V, S, moved


def records(V, S):
    out = []
    n = len(V)
    for k in range(n):
        a, b = V[k], V[(k + 1) % n]
        if S[k] is None:
            out.append({"line": [[round(a[0], 2), round(a[1], 2)], [round(b[0], 2), round(b[1], 2)]]})
        else:
            c, pm = S[k]["c"], S[k]["pm"]
            out.append({"arc": [[round(c[0], 2), round(c[1], 2)], round(S[k]["r"], 2), [round(a[0], 2), round(a[1], 2)],
                                [round(b[0], 2), round(b[1], 2)], [round(pm[0], 2), round(pm[1], 2)]]})
    return out


# ---------------------------------------------------------------- main

def main():
    lv = sys.argv[1]
    cfg = dict(DEFAULTS)
    if "--config" in sys.argv:
        cfg.update(json.load(open(sys.argv[sys.argv.index("--config") + 1], encoding="utf-8")))
    D = json.load(open("ifc_slabs.json", encoding="utf-8"))["levels"][lv]
    band = D["band"]
    ex = None
    if "--example" in sys.argv:
        path, key = sys.argv[sys.argv.index("--example") + 1].rsplit(":", 1)
        ex = json.load(open(path, encoding="utf-8"))[key]["floors"][0]
        cfg.setdefault("floor_type", None)
        if not cfg["floor_type"]:
            cfg["floor_type"] = ex["type"]
        if "--config" not in sys.argv or "offset" not in json.load(open(sys.argv[sys.argv.index("--config") + 1], encoding="utf-8")):
            cfg["offset"] = float(ex["params"].get("Height Offset From Level", "0").replace(",", ""))
            cfg["structural"] = ex["params"].get("Structural", "Yes") == "Yes"
    if not cfg["floor_type"]:
        cfg["floor_type"] = "CONCRETE %dMM" % round(band["thk"])
    notes = []
    if ex is not None:
        ex_thk = sum(l[1] for l in ex["layers"])
        if abs(ex_thk - band["thk"]) > 1:
            notes.append("IFC slab is %d thick, example type %s is %d" % (band["thk"], ex["type"], ex_thk))

    outers, holes = [], []
    for face in D["outline"]:
        for recs in face["loops"]:
            V, S = loop_from_records(recs)
            for V2, S2 in split_pinches(V, S):
                (outers if area(polyline(V2, S2)) > 0 else holes).append([V2, S2])

    # walls/columns cut at mid-slab, as point loops (corner pockets must be backed by them)
    fills = []
    for f in D.get("fills", []):
        lps = [polyline(*loop_from_records(lp)) for lp in f["loops"]]
        lps = [lp for lp in lps if len(lp) >= 3]
        if lps:
            xs = [p[0] for lp in lps for p in lp]
            ys = [p[1] for lp in lps for p in lp]
            fills.append({"loops": lps, "bb": (min(xs), min(ys), max(xs), max(ys))})

    # outer loops: tidy, fit arcs, close pockets, snap
    pockets, kept_outers, dropped = [], [], []
    for V, S in outers:
        V, S = merge_collinear(list(V), list(S))
        V, S = fit_arcs(V, S, cfg["arc_tol"])
        V, S = flatten_tiny_arcs(V, S)
        V, S = merge_collinear(V, S)
        V, S = fill_pockets(V, S, cfg, pockets, fills)
        V, S = merge_collinear(V, S)
        V, S, moved = snap_orthogonal(V, S, cfg["snap_deg"])
        a = area(polyline(V, S)) / 1e6
        kept_outers.append({"V": V, "S": S, "area": a, "snap_moved": round(moved, 1)})
    big = [o for o in kept_outers if o["area"] >= cfg["min_island"]]
    for o in kept_outers:
        if o["area"] < cfg["min_island"]:
            dropped.append({"kind": "island", "area_m2": round(o["area"], 2),
                            "at": [round(sum(p[0] for p in o["V"]) / len(o["V"])), round(sum(p[1] for p in o["V"]) / len(o["V"]))]})

    # stairs that reach the slab band (arriving at or leaving from this level) -> one plan rectangle each
    top = band["top_dz"]
    stairs = []
    for s in D.get("stairs", []):
        if s["dz"][0] < top + 300 and s["dz"][1] > top - band["thk"] - 300:
            r = min_rect([tuple(p) for p in s["hull"]])
            stairs.append({"id": s["id"], "rect": r if area(r) > 0 else r[::-1]})

    voids, table = [], []
    for V, S in holes:
        V, S = merge_collinear(list(V), list(S))
        pts = polyline(V, S)
        a = abs(area(pts)) / 1e6
        per = sum(dist(pts[k], pts[(k + 1) % len(pts)]) for k in range(len(pts)))
        width = 2 * a * 1e6 / per if per else 0
        cen = (sum(p[0] for p in pts) / len(pts), sum(p[1] for p in pts) / len(pts))
        rec = {"area_m2": round(a, 2), "width": round(width), "at": [round(cen[0]), round(cen[1])],
               "bbox": [round(min(p[0] for p in pts)), round(min(p[1] for p in pts)), round(max(p[0] for p in pts)), round(max(p[1] for p in pts))]}
        ccw = pts if area(pts) > 0 else pts[::-1]
        inside = []
        for s in stairs:
            cl = clip_convex(ccw, s["rect"])
            if cl and abs(area(cl)) > 0.5 * abs(area(s["rect"])):
                inside.append(s)
        if inside:
            # void = the flights' rectangles (joined across the wall between scissor flights), clipped to the hole;
            # the rest of the hole (floor landings) stays slab, as in the example
            rects, seen = [], []
            for s in inside:
                if not any(max(dist(p, q) for p, q in zip(s["rect"], r)) < 20 for r in seen):
                    seen.append(s["rect"])
            rects = seen
            loops_u = rect_union(rects, cfg.get("stair_bridge", 300.0)) or [clip_convex(ccw, r) for r in rects]
            rec["class"] = "stair"
            rec["stairs"] = [s["id"] for s in inside]
            geos = []
            for lp in loops_u:
                V2, S2 = merge_collinear(list(lp), [None] * len(lp))
                V2, S2, _ = snap_orthogonal(V2, S2, cfg["snap_deg"])
                geos.append((V2, S2))
            rec["void_area_m2"] = round(sum(abs(area(g[0])) for g in geos) / 1e6, 2)
            rec["void_dims"] = [[round(max(p[0] for p in g[0]) - min(p[0] for p in g[0])),
                                 round(max(p[1] for p in g[0]) - min(p[1] for p in g[0]))] for g in geos]
            geo = geos
        elif a < cfg["min_void"] or width < cfg["min_width"]:
            rec["class"] = "penetration"
            geo = None
        else:
            rec["class"] = "shaft"
            V, S, _ = snap_orthogonal(V, S, cfg["snap_deg"])
            geo = [(V, S)]
        rec["kept"] = rec["class"] in cfg["keep_voids"] and geo is not None
        if rec["kept"]:
            voids.extend(records(*g) for g in geo)
        table.append(rec)

    loops = [records(o["V"], o["S"]) for o in big] + voids
    net = sum(o["area"] for o in big) - sum(r.get("void_area_m2", r["area_m2"]) for r in table if r["kept"])
    plan = {"level": D["level"], "level_id": D["level_id"], "level_z": D["level_z"], "floor_type": cfg["floor_type"],
            "type_source": cfg["type_source"], "thickness": band["thk"], "offset": cfg["offset"],
            "structural": cfg["structural"], "ifc_top_dz": band["top_dz"], "ifc_slab_ids": D["slab_ids"],
            "loops": loops, "outer_count": len(big), "void_count": len(voids),
            "gross_area_m2": round(sum(o["area"] for o in big), 2), "net_area_m2": round(net, 2),
            "pockets_closed": pockets, "holes": table, "dropped": dropped,
            "stair_rects": [{"id": s["id"], "rect": [[round(p[0], 1), round(p[1], 1)] for p in s["rect"]]} for s in stairs],
            "snap_moved_max": max([o["snap_moved"] for o in big] or [0]), "notes": notes, "config": cfg}
    json.dump(plan, open("slab_%s.json" % lv, "w", encoding="utf-8"), indent=1)
    from collections import Counter
    print(json.dumps({"level": lv, "type": cfg["floor_type"], "offset": cfg["offset"], "structural": cfg["structural"],
                      "outer_loops": [(len(o["V"]), sum(1 for s in o["S"] if s), round(o["area"], 2)) for o in big],
                      "holes": dict(Counter("%s%s" % (r["class"], "" if r["kept"] else " (not modelled)")
                                            for r in table).most_common()),
                      "kept_voids": [(r["class"], r.get("void_area_m2", r["area_m2"])) for r in table if r["kept"]],
                      "pockets": pockets, "dropped": dropped, "gross": plan["gross_area_m2"], "net": plan["net_area_m2"],
                      "snap_moved_max": plan["snap_moved_max"], "notes": notes}, default=str))


if __name__ == "__main__":
    main()
