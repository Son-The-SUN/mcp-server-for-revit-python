"""Turn extracted IFC data (ifc_<LV>.json) into a Revit build plan (build_<LV>.json).

Run from the work directory (where extract_view.py / extract_spaces.py wrote their JSON):

    python plan_build.py L5 --spaces L5
    python plan_build.py L10 --spaces L6 --config remodel_config.json

Walls:   centreline (line or arc, host mm), thickness, base offset, unconnected height, target type.
Doors:   host IFC wall, insertion point, family + "<W> x <H>" size, swing side + hinge end from the IFC arcs.
Windows: host IFC wall, insertion point, sill, family (from config by IFC operation) + size, exterior side.
Screens: IFC "walls" with no axis made of many small solids - listed, not built.

See ../../SKILL.md for why each rule exists.
"""
import argparse
import json
import math
from collections import Counter

from plot import loop_pts

DEFAULT_CONFIG = {
    "unit_prefix": "A1-",
    "wall_types": {
        "concrete": {"150": "WT50 - CONCRETE - 150mm", "200": "WT51 - CONCRETE - 200mm", "300": "WT52 - CONCRETE - 300mm"},
        "generic": "GROUPGSA - GENERIC-{W}mm",
        "glass": "GROUPGSA - GENERIC - {W}mm GLASS",
    },
    "door_families": {
        "single": "DOOR - SINGLE - STD",
        "double": "DOOR - DOUBLE - STD",
        "lift": "DOOR - SLIDER 2P",
        "slider_small": "DOOR - SLIDER 2P",
        "slider_large": "DOOR - SLIDER 3P",
        "slider_split_width": 2900,
        "head_lining": 50,
    },
    # IFC window operation -> window family name; "default" catches the rest. None = don't build windows.
    "window_families": {"default": None},
    "snap_degrees": 0.2,
    # walls count for this level when their base is -800..wall_base_max mm from it (tower C: a curved glass
    # corner wall starts 250 above the slab)
    "wall_base_max": 200,
}

ap = argparse.ArgumentParser()
ap.add_argument("lv", help="level key used in file names, e.g. L5")
ap.add_argument("--spaces", help="key in ifc_spaces.json whose unit outlines apply to this level (default: lv)")
ap.add_argument("--config", help="JSON file overriding DEFAULT_CONFIG keys")
ap.add_argument("--unit-prefix", help="unit space name prefix for this level, e.g. A1-05. (overrides config)")
args = ap.parse_args()
CFG = dict(DEFAULT_CONFIG)
if args.config:
    CFG.update(json.load(open(args.config)))
if args.unit_prefix:
    CFG["unit_prefix"] = args.unit_prefix

LV = args.lv
D = json.load(open("ifc_%s.json" % LV))
LZ = D["meta"]["level_z"]
ROWS = D["rows"]
try:
    SPACES = sorted(json.load(open("ifc_spaces.json")).get(args.spaces or LV, []), key=lambda s: bool(s.get("approx")))
except IOError:
    SPACES = []
UNIT_POLYS = [(s["name"], lp) for s in SPACES for lp in s["loops"] if s["name"] and s["name"].startswith(CFG["unit_prefix"])]


# ---------------------------------------------------------------- geometry helpers
def point_in_poly(x, y, poly):
    inside = False
    n = len(poly)
    for i in range(n):
        x1, y1 = poly[i][0], poly[i][1]
        x2, y2 = poly[(i + 1) % n][0], poly[(i + 1) % n][1]
        if (y1 > y) != (y2 > y) and x < x1 + (y - y1) * (x2 - x1) / (y2 - y1):
            inside = not inside
    return inside


def unit_at(x, y):
    for name, poly in UNIT_POLYS:
        if point_in_poly(x, y, poly):
            return name
    return None


def unit_dir(p0, p1):
    L = math.hypot(p1[0] - p0[0], p1[1] - p0[1])
    return ((p1[0] - p0[0]) / L, (p1[1] - p0[1]) / L), L


def on_level(r, lo=-800, hi=None):
    hi = CFG["wall_base_max"] if hi is None else hi
    b = r.get("bb")
    return b is not None and lo <= b[2] - LZ <= hi and b[5] - LZ > 300


def curve_points(r, axis_too=True):
    pts = []
    for a in r["axis"]:
        if axis_too or a.get("style") != "Axis":
            pts += a.get("pts") or [a["arc"][2], a["arc"][3], a["arc"][4]]
    return pts


def tighten_wall_bb(r):
    """Some IFC walls come in as wireframes (edge curves, few or no solids), and Revit's bounding box of those is
    inflated - on tower C by up to 1.8 m in z, so a typical facade wall 0..2800 above the level read as -1824..4200
    and was dropped as 'not on this level'. Replace the box with the geometry's own extent when it is narrower."""
    # only walls with an IFC axis (it lies at the wall base): for a solid without one, cut and top faces would miss
    # the bottom
    if axis_of(r) is None:
        return False
    pts = curve_points(r) + [p for lp in (r["cut"] + r["top"]) for c in lp for p in (c.get("pts") or c["arc"][2:])]
    b = r.get("bb")
    if not b:
        return False
    g = [min(p[0] for p in pts), min(p[1] for p in pts), min(p[2] for p in pts),
         max(p[0] for p in pts), max(p[1] for p in pts), max(p[2] for p in pts)]
    if g[2] - b[2] > 50 or b[5] - g[5] > 50:
        r["bb_revit"] = b
        r["bb"] = g
        return True
    return False


def outline_points(r):
    """Section points: the cut at the view's cut plane, or the top face for walls below it; for wireframe walls
    (no solid) the wall's own edge curves."""
    pts = [p for lp in (r["cut"] or r["top"]) for p in loop_pts(lp)]
    return pts or [p[:2] for p in curve_points(r, axis_too=False)]


def extent_points(r):
    """Points that bound the wall's length: section + top face (a wall with a tall opening is cut only at its
    jambs, but its top face runs over the lintel)."""
    return outline_points(r) + [p for lp in r["top"] for p in loop_pts(lp)]


def axis_of(r):
    for a in r["axis"]:
        if a.get("style") == "Axis":
            return a
    return None


def exterior_side(pt, n):
    """Unit normal pointing away from the unit interior, or None if both/neither side is inside a unit."""
    ua = unit_at(pt[0] + n[0] * 500, pt[1] + n[1] * 500)
    ub = unit_at(pt[0] - n[0] * 500, pt[1] - n[1] * 500)
    if ua and not ub:
        return [-n[0], -n[1]]
    if ub and not ua:
        return [n[0], n[1]]
    return None


# ---------------------------------------------------------------- walls
def wall_type_for(layer, W, mat):
    mat = (mat or "").upper()
    W = int(round(W))
    wt = CFG["wall_types"]
    if "GLASS" in mat or (layer or "").startswith("3 Handrails"):
        return wt["glass"].format(W=W)
    # in-situ concrete only: "CONCRETE BLOCK" is blockwork, which has no WT5x equivalent -> generic
    if "CONCRETE" in mat and "BLOCK" not in mat and str(W) in wt["concrete"]:
        return wt["concrete"][str(W)]
    return wt["generic"].format(W=W)


def plan_wall(r, seg=None, k=0):
    """seg: one segment (p0, p1) of a polyline axis - planned as its own wall '<ifc id>-s<k>'."""
    W = r["Width"]
    a = axis_of(r)
    if a is None or not W:
        return None
    pts = outline_points(r)
    xpts = extent_points(r)
    b = r["bb"]
    rec = {"ifc_id": r["id"] if seg is None else "%d-s%d" % (r["id"], k), "guid": r["IfcGUID"], "layer": r["IfcPresentationLayer"], "name": r["IfcName"],
           "mat": r["IfcMaterial"], "W": W, "base_off": round(b[2] - LZ, 1), "height": round(b[5] - b[2], 1),
           "storey": r["IfcSpatialContainer"], "notes": []}
    rec["type"] = wall_type_for(rec["layer"], W, rec["mat"])
    if "arc" in a:
        C, R, p0, p1, pm = a["arc"]
        c = 0.0
        if pts:
            rr = [math.hypot(p[0] - C[0], p[1] - C[1]) - R for p in pts]
            lo, hi = min(rr), max(rr)
            if abs((hi - lo) - W) <= 30:
                c = (lo + hi) / 2.0
            else:
                c = math.copysign(W / 2.0, sum(rr) / len(rr))
                rec["notes"].append("arc outline %.0f deep vs W %.0f" % (hi - lo, W))
        else:
            rec["notes"].append("arc without outline: axis used as centreline")
        Rc = R + c
        am = math.atan2(pm[1] - C[1], pm[0] - C[0])

        def rel(ang):
            d = ang - am
            while d > math.pi:
                d -= 2 * math.pi
            while d < -math.pi:
                d += 2 * math.pi
            return d
        angs = [rel(math.atan2(p[1] - C[1], p[0] - C[0])) for p in (xpts or [p0, p1])]
        lo_a, hi_a = min(angs), max(angs)
        P = lambda t: [C[0] + Rc * math.cos(am + t), C[1] + Rc * math.sin(am + t)]
        rec.update({"kind": "arc", "p0": P(lo_a), "p1": P(hi_a), "pm": P((lo_a + hi_a) / 2.0), "center": C[:2], "radius": Rc})
        return rec
    p0, p1 = seg or (a["pts"][0], a["pts"][-1])
    d, L = unit_dir(p0, p1)
    n = (-d[1], d[0])
    if seg is not None:
        # keep the outline near this segment: within 1.5 W of its line and W past its ends; for the side test
        # prefer the middle part, away from the legs of the neighbouring segments
        near = lambda q, m: (-m <= (q[0] - p0[0]) * d[0] + (q[1] - p0[1]) * d[1] <= L + m
                             and abs((q[0] - p0[0]) * n[0] + (q[1] - p0[1]) * n[1]) <= 1.5 * W)
        xpts = [q for q in xpts if near(q, W)]
        pts = [q for q in pts if near(q, -W)] or [q for q in pts if near(q, W)]
        rec["notes"].append("segment %d of a polyline axis" % k)
    if pts:
        s = [(p[0] - p0[0]) * n[0] + (p[1] - p0[1]) * n[1] for p in pts]
        t = [(p[0] - p0[0]) * d[0] + (p[1] - p0[1]) * d[1] for p in xpts]
        lo, hi = min(s), max(s)
        if abs((hi - lo) - W) <= 30:
            c = (lo + hi) / 2.0
        else:
            c = (1.0 if sum(s) / len(s) > 0 else -1.0) * W / 2.0
            rec["notes"].append("wedge: outline %.0f deep vs W %.0f, modelled W on axis face" % (hi - lo, W))
            rec["wedge"] = round(hi - lo)
        t0, t1 = min(t), max(t)
    else:
        bc = ((b[0] + b[3]) / 2.0, (b[1] + b[4]) / 2.0)
        sc = (bc[0] - p0[0]) * n[0] + (bc[1] - p0[1]) * n[1]
        c = 0.0 if abs(sc) < W / 4.0 else math.copysign(W / 2.0, sc)
        t0, t1 = 0.0, L
        rec["notes"].append("no outline: side from bounding box")
    if t1 - t0 < 20:
        return None
    rec.update({"kind": "line",
                "p0": [p0[0] + d[0] * t0 + n[0] * c, p0[1] + d[1] * t0 + n[1] * c],
                "p1": [p0[0] + d[0] * t1 + n[0] * c, p0[1] + d[1] * t1 + n[1] * c]})
    return rec


def snap_orthogonal(w, max_deg):
    """Rotate a nearly orthogonal line wall about its midpoint so it is exactly orthogonal."""
    (dx, dy), L = unit_dir(w["p0"], w["p1"])
    ang = math.degrees(math.atan2(dy, dx))
    target = round(ang / 90.0) * 90.0
    dev = ang - target
    if 1e-9 < abs(dev) <= max_deg:
        m = [(w["p0"][0] + w["p1"][0]) / 2.0, (w["p0"][1] + w["p1"][1]) / 2.0]
        tr = math.radians(target)
        u = (math.cos(tr) * L / 2.0, math.sin(tr) * L / 2.0)
        w["p0"], w["p1"] = [m[0] - u[0], m[1] - u[1]], [m[0] + u[0], m[1] + u[1]]
        w["notes"].append("snapped %.4f deg to orthogonal" % dev)
        return True
    return False


def rank(w):
    t = w["type"].upper()
    kind = 0 if "GLASS" in t else (1 if "GENERIC" in t else 2)
    return (kind, w["W"], math.hypot(w["p1"][0] - w["p0"][0], w["p1"][1] - w["p0"][1]))


def resolve_overlaps(walls):
    """Trim/drop the lower-ranked of two collinear line walls whose bodies overlap (avoids 'walls overlap')."""
    moved = {}                       # loser ifc_id -> winner ifc_id (for re-hosting openings)
    extra = []
    sin1 = math.sin(math.radians(1.0))
    lines = [w for w in walls if w["kind"] == "line"]
    for i in range(len(lines)):
        for j in range(i + 1, len(lines)):
            a, b = lines[i], lines[j]
            if a.get("dropped") or b.get("dropped"):
                continue
            da, La = unit_dir(a["p0"], a["p1"])
            db, Lb = unit_dir(b["p0"], b["p1"])
            if abs(da[0] * db[1] - da[1] * db[0]) > sin1:
                continue
            na = (-da[1], da[0])
            off = abs((b["p0"][0] - a["p0"][0]) * na[0] + (b["p0"][1] - a["p0"][1]) * na[1])
            if off > (a["W"] + b["W"]) / 2.0 - 5:
                continue
            tb = sorted([(q[0] - a["p0"][0]) * da[0] + (q[1] - a["p0"][1]) * da[1] for q in (b["p0"], b["p1"])])
            ov = min(La, tb[1]) - max(0.0, tb[0])
            if ov <= 10:
                continue
            if abs(a["base_off"] - b["base_off"]) > 1 and (a["base_off"] + a["height"] <= b["base_off"] or b["base_off"] + b["height"] <= a["base_off"]):
                continue            # stacked vertically, not overlapping
            loser, winner = (a, b) if rank(a) < rank(b) else (b, a)
            dl, Ll = unit_dir(loser["p0"], loser["p1"])
            tw = sorted([(q[0] - loser["p0"][0]) * dl[0] + (q[1] - loser["p0"][1]) * dl[1] for q in (winner["p0"], winner["p1"])])
            pieces = [(0.0, min(Ll, tw[0])), (max(0.0, tw[1]), Ll)]
            pieces = [(s, e) for s, e in pieces if e - s > 100]
            P = lambda t, w=loser, d=dl: [w["p0"][0] + d[0] * t, w["p0"][1] + d[1] * t]
            moved[loser["ifc_id"]] = winner["ifc_id"]
            if not pieces:
                loser["dropped"] = True
                loser["notes"].append("dropped: covered by %s" % winner["ifc_id"])
                continue
            s0, e0 = pieces[0]
            if len(pieces) == 2:
                twin = json.loads(json.dumps(loser))
                twin["ifc_id"] = "%s-b" % loser["ifc_id"]
                twin["p0"], twin["p1"] = P(pieces[1][0]), P(pieces[1][1])
                twin["notes"].append("split around %s" % winner["ifc_id"])
                extra.append(twin)
            loser["p0"], loser["p1"] = P(s0), P(e0)
            loser["notes"].append("trimmed against %s (overlap %.0f)" % (winner["ifc_id"], ov))
    walls.extend(extra)
    return moved


# ---------------------------------------------------------------- openings (doors, windows)
def free_line(r, height, base_off=0.0):
    """Host-wall line for an opening with no IFC host.

    Direction: hinge -> closed-leaf end of a swing arc (the open leaf is drawn as a straight line from the hinge
    to the arc's other end, perpendicular to the wall, so the longest segment is NOT the wall direction for swing
    doors); without arcs, the longest plan segment. Centre across the wall from the frame lines only.
    """
    plan = r.get("plan", [])
    segs = []
    for rec in plan:
        ps = rec.get("pts", [])
        for i in range(len(ps) - 1):
            segs.append((math.hypot(ps[i + 1][0] - ps[i][0], ps[i + 1][1] - ps[i][1]), ps[i], ps[i + 1]))
    if not segs:
        return None
    near = lambda p, q: math.hypot(p[0] - q[0], p[1] - q[1]) < 5
    leaf = set()
    dd = None
    for rec in plan:
        if "arc" not in rec:
            continue
        c, rad, e0, e1, pm = rec["arc"]
        for k, (L, a, b2) in enumerate(segs):
            for e in (e0, e1):
                if (near(a, c) and near(b2, e)) or (near(b2, c) and near(a, e)):
                    leaf.add(k)
                    closed = e1 if e is e0 else e0
                    if dd is None:
                        dd = unit_dir(c, closed)[0]
    if dd is None:
        L, a, b2 = max(segs, key=lambda s: s[0])
        dd = ((b2[0] - a[0]) / L, (b2[1] - a[1]) / L)
    nn = (-dd[1], dd[0])
    o = segs[0][1]
    frame = [p for k, (L, a, b2) in enumerate(segs) if k not in leaf for p in (a, b2)]
    allp = frame + [rec["arc"][i] for rec in plan if "arc" in rec for i in (2, 3)]
    ts = [(p[0] - o[0]) * dd[0] + (p[1] - o[1]) * dd[1] for p in allp]
    ss = [(p[0] - o[0]) * nn[0] + (p[1] - o[1]) * nn[1] for p in frame]
    t0, t1, sm = min(ts) - 50, max(ts) + 50, (min(ss) + max(ss)) / 2.0
    a = o
    P = lambda t: [a[0] + dd[0] * t + nn[0] * sm, a[1] + dd[1] * t + nn[1] * sm]
    return {"p0": P(t0), "p1": P(t1), "pm": P((t0 + t1) / 2.0), "W": int(round((r.get("Depth") or 200) / 10.0) * 10),
            "height": height, "base_off": base_off}


def place_on_host(r, host):
    """Insertion point on the host centreline (midpoint of the plan-symbol extents), plus local frame."""
    pts, arcs = [], []
    for rec in r.get("plan", []):
        if "arc" in rec:
            arcs.append(rec["arc"])
            pts += [rec["arc"][2][:2], rec["arc"][3][:2]]
        else:
            pts += [p[:2] for p in rec["pts"]]
    if not pts:
        bb = r["bb"]
        pts = [[bb[0], bb[1]], [bb[3], bb[4]], [bb[0], bb[4]], [bb[3], bb[1]]]
    out = {"arcs": arcs}
    if host["kind"] == "line":
        d, L = unit_dir(host["p0"], host["p1"])
        n = (-d[1], d[0])
        a = host["p0"]
        ts = [(p[0] - a[0]) * d[0] + (p[1] - a[1]) * d[1] for p in pts]
        tm = (min(ts) + max(ts)) / 2.0
        out.update({"pt": [a[0] + d[0] * tm, a[1] + d[1] * tm], "d": d, "n": n, "extent": round(max(ts) - min(ts), 1)})
    else:
        C = host["center"]
        mx = sum(p[0] for p in pts) / len(pts)
        my = sum(p[1] for p in pts) / len(pts)
        ang = math.atan2(my - C[1], mx - C[0])
        n = (math.cos(ang), math.sin(ang))
        out.update({"pt": [C[0] + host["radius"] * n[0], C[1] + host["radius"] * n[1]], "d": (-n[1], n[0]), "n": n})
    return out


def plan_door(r, host):
    W, H = r["Width"], r["Height"]
    op = r.get("IfcOperationType") or r.get("Operation") or ""
    dp = {"ifc_id": r["id"], "guid": r["IfcGUID"], "layer": r["IfcPresentationLayer"], "op": op, "W": W, "H": H,
          "sill": round(r["bb"][2] - LZ, 1), "host_ifc": host["ifc_id"] if host else None, "notes": []}
    if host is None:
        free = free_line(r, height=H + 100)
        if free is None:
            dp["notes"].append("no host and no plan symbol")
            dp["pt"] = [(r["bb"][0] + r["bb"][3]) / 2.0, (r["bb"][1] + r["bb"][4]) / 2.0]
            return dp
        dp["free"] = free
        dp["notes"].append("no IFC host: nearest wall or own host wall at build time")
        host = {"kind": "line", "p0": free["p0"], "p1": free["p1"]}
    loc = place_on_host(r, host)
    dp["pt"], n, d = loc["pt"], loc["n"], loc["d"]
    if loc["arcs"]:
        sw = hg = 0.0
        for c, rad, p0, p1, pm in loc["arcs"]:
            for p in (p0, p1):
                s = (p[0] - dp["pt"][0]) * n[0] + (p[1] - dp["pt"][1]) * n[1]
                if abs(s) > rad * 0.5:
                    sw += s
            hg += (c[0] - dp["pt"][0]) * d[0] + (c[1] - dp["pt"][1]) * d[1]
        dp["swing"] = [n[0] * (1 if sw > 0 else -1), n[1] * (1 if sw > 0 else -1)]
        if len(loc["arcs"]) == 1:
            dp["hinge"] = [d[0] * (1 if hg > 0 else -1), d[1] * (1 if hg > 0 else -1)]
        dp["leaf"] = round(sum(a[1] for a in loc["arcs"]), 1)
    ext = exterior_side(dp["pt"], n)
    if ext:
        dp["exterior"] = ext
    # family + type size, following the measured family conventions (SKILL.md, Doors)
    F = CFG["door_families"]
    lw = dp.get("leaf") or (W - 100)
    if op.startswith("Single_Swing") or op.startswith("Double_Swing"):
        dp.update({"family": F["single"], "tw": int(round(lw)), "th": int(round(H - F["head_lining"]))})
    elif op == "Double_Door_Single_Swing":
        dp.update({"family": F["double"], "tw": int(round(lw)), "th": int(round(H - F["head_lining"]))})
    elif op == "Double_Door_Sliding":
        dp.update({"family": F["lift"], "tw": int(round(W)), "th": int(round(H))})
    else:
        fam = F["slider_small"] if W < F["slider_split_width"] else F["slider_large"]
        dp.update({"family": fam, "tw": int(round(W)), "th": int(round(H))})
    return dp


def plan_window(r, host, free_height):
    op = r.get("IfcOperationType") or r.get("OperationType") or r.get("Operation") or ""
    wp = {"ifc_id": r["id"], "guid": r["IfcGUID"], "op": op, "W": r["Width"], "H": r["Height"],
          "sill": round(r["bb"][2] - LZ, 1), "host_ifc": host["ifc_id"] if host else None, "notes": []}
    if not r["Width"] or not r["Height"]:
        # tower C had one FIXEDCASEMENT per floor with no BaseQuantities: nothing to size a type from
        bb = r["bb"]
        wp.update({"pt": [(bb[0] + bb[3]) / 2.0, (bb[1] + bb[4]) / 2.0], "special": "no width/height in the IFC",
                   "family": None})
        return wp
    if host is None:
        free = free_line(r, height=free_height)
        if free is None:
            bb = r["bb"]
            wp["pt"] = [(bb[0] + bb[3]) / 2.0, (bb[1] + bb[4]) / 2.0]
            wp["notes"].append("no host and no plan symbol")
            return wp
        wp["free"] = free
        wp["notes"].append("no IFC host: nearest wall or own host wall at build time")
        host = {"kind": "line", "p0": free["p0"], "p1": free["p1"]}
    loc = place_on_host(r, host)
    wp["pt"] = loc["pt"]
    ext = exterior_side(wp["pt"], loc["n"])
    if ext:
        wp["exterior"] = ext
    wp["tw"], wp["th"] = int(round(r["Width"])), int(round(r["Height"]))
    if host["kind"] != "line":
        # special case (user rule): windows in curved walls are left as-is for now - listed, not built
        wp["special"] = "curved host wall"
        wp["family"] = None
        return wp
    fams = CFG["window_families"]
    wp["family"] = fams.get(op, fams.get("default"))
    return wp


# ---------------------------------------------------------------- main
walls, screens, skipped = [], [], []
tightened = [r["id"] for r in ROWS if r["cat"] == "Walls" and tighten_wall_bb(r)]
for r in ROWS:
    if r["cat"] != "Walls" or not on_level(r):
        continue
    if axis_of(r) is None:
        screens.append({"ifc_id": r["id"], "layer": r["IfcPresentationLayer"], "W": r["Width"], "H": r["Height"],
                        "nsol": r["nsol"], "bb": r["bb"]})
        continue
    a = axis_of(r)
    apts = a.get("pts") or []
    if len(apts) > 2:
        # L/U-shaped IFC walls (tower C window surrounds): one Revit wall per axis segment
        for k in range(len(apts) - 1):
            if math.hypot(apts[k + 1][0] - apts[k][0], apts[k + 1][1] - apts[k][1]) < 20:
                continue
            w = plan_wall(r, (apts[k], apts[k + 1]), k)
            (skipped if w is None else walls).append("%d-s%d" % (r["id"], k) if w is None else w)
        continue
    w = plan_wall(r)
    (skipped if w is None else walls).append(r["id"] if w is None else w)

snapped = sum(1 for w in walls if w["kind"] == "line" and snap_orthogonal(w, CFG["snap_degrees"]))
moved = resolve_overlaps(walls)
walls_kept = [w for w in walls if not w.get("dropped")]
byguid = dict((w["guid"], w) for w in walls_kept)
byid = dict((w["ifc_id"], w) for w in walls_kept)


def host_for(r):
    h = byguid.get(r["IfcContainedInHostGUID"])
    if h is None and r["IfcContainedInHostGUID"]:
        # host was dropped/trimmed away by overlap resolution: use the wall that covered it
        lost = [w for w in walls if w["guid"] == r["IfcContainedInHostGUID"]]
        if lost and lost[0]["ifc_id"] in moved:
            h = byid.get(moved[lost[0]["ifc_id"]])
    return h


ext_heights = Counter(round(w["height"]) for w in walls_kept if (w["layer"] or "").startswith("3 Walls - Ext"))
free_height = ext_heights.most_common(1)[0][0] if ext_heights else 3000
doors = [plan_door(r, host_for(r)) for r in ROWS if r["cat"] == "Doors" and on_level(r, -100, 200)]
windows = [plan_window(r, host_for(r), free_height) for r in ROWS if r["cat"] == "Windows" and on_level(r, -100, 1500)]

def cover_openings(openings):
    """Stretch a straight host wall so it covers every opening it hosts (+50 mm each side)."""
    n = 0
    for o in openings:
        h = byid.get(o.get("host_ifc"))
        if not h or h["kind"] != "line" or not o.get("pt"):
            continue
        d, L = unit_dir(h["p0"], h["p1"])
        t = (o["pt"][0] - h["p0"][0]) * d[0] + (o["pt"][1] - h["p0"][1]) * d[1]
        half = (o.get("W") or o.get("tw") or 0) / 2.0 + 50
        lo, hi = min(0.0, t - half), max(L, t + half)
        if lo < 0 or hi > L:
            p0 = list(h["p0"])
            h["p0"] = [p0[0] + d[0] * lo, p0[1] + d[1] * lo]
            h["p1"] = [p0[0] + d[0] * hi, p0[1] + d[1] * hi]
            h["notes"].append("extended %.0f..%.0f to cover opening %s" % (lo, hi - L, o["ifc_id"]))
            n += 1
    return n


covered = cover_openings(doors + windows)
json.dump({"meta": D["meta"], "walls": walls_kept, "dropped": [w for w in walls if w.get("dropped")], "doors": doors,
           "windows": windows, "screens": screens, "skipped": skipped, "config": CFG},
          open("build_%s.json" % LV, "w"), indent=1)
print("%s: walls %d (dropped %d, snapped %d, trimmed/split %d) | screens %d | skipped %s | doors %d | windows %d (%d with family)"
      % (LV, len(walls_kept), len(walls) - len(walls_kept), snapped, len(moved), len(screens), skipped, len(doors),
         len(windows), sum(1 for w in windows if w.get("family"))))
print(" host walls stretched to cover their openings:", covered)
print(" walls with inflated Revit bbox, box taken from their geometry:", tightened)
print(" wall types:", dict(Counter(w["type"] for w in walls_kept)))
print(" door types:", dict(Counter("%s | %d x %d" % (d.get("family"), d.get("tw", 0), d.get("th", 0)) for d in doors)))
print(" window sizes:", dict(Counter("%s %d x %d" % (w["op"], w["tw"], w["th"]) for w in windows if "tw" in w)))
print(" windows left as-is (special):", [(w["ifc_id"], w["special"]) for w in windows if w.get("special")])
print(" wedges:", [w["ifc_id"] for w in walls if w.get("wedge")], " openings without IFC host:",
      [o["ifc_id"] for o in doors + windows if o.get("host_ifc") is None])
