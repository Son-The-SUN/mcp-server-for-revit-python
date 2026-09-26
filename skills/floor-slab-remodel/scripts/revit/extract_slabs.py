# -*- coding: utf-8 -*-
"""Find the linked IFC's floor slabs at each host level and merge them into one slab outline per level.

Runs INSIDE Revit (IronPython 2.7) through execute_revit_code. Read-only (no transaction). Set the inputs,
then execfile this file:

    LEVELS = {"L5": 950753, "L10": 950770}     # key -> host level id
    OUT = r"<work>\\ifc_slabs.json"
    # optional:
    # LINK_INSTANCE_ID = 950744                # defaults to the first Revit link instance
    # CATEGORIES = ["Floors", "Roofs", "Generic Models"]   # link categories scanned for candidates
    # ZWIN = [-700, 150]                       # mm: an up-facing face counts when level+ZWIN[0] <= z <= level+ZWIN[1]
    # MIN_AREA = 0.5                           # m2: smaller up-faces are ignored
    # SLAB_RULE = {"cats": ["Floors"], "dz": [-100, 50], "min_thk": 150, "material": "CONCRETE"}
    # INCLUDE = {"L10": [123]}                 # link element ids forced into / out of the merged slab
    # EXCLUDE = {"L10": [456]}
    # FILL = "holes"                           # "holes" | "union" | "none" - see merge()
    # FILL_CATS = ["Walls", "Structural Columns", "Columns"]
    # STAIR_CATS = ["Stairs"]; STAIR_REACH = 3500   # stairs within this many mm of the level
    # EMBED = 0.6                              # add fills that are this much surrounded by slab (0 = off)
    execfile(r"<repo>\\skills\\floor-slab-remodel\\scripts\\revit\\extract_slabs.py")

Per level it records every candidate element with an up-facing face in the window (IFC properties, the top
faces as loops in host mm, the faces' z, the thickness down to the next down-facing face, bbox). Solids give
planar faces with true arcs; IFC meshes (common for typical floors) give triangles, whose boundary edges are
chained into loops (arcs arrive as short segments; plan_slab.py fits them back).

Candidates that pass SLAB_RULE (or are in INCLUDE, and not in EXCLUDE) are merged into the level's slab.
ArchiCAD-style IFCs cut every wall and column that passes through a slab out of it, so the slab's holes are
mostly wall footprints: the walls/columns are cut at mid-slab ("fills") and the holes they cover are filled
back, leaving the real voids (shafts, stairs, penetrations). Booleans run on thin extrusions of the loops,
projected onto the level. Result: "outline" (outer and inner loops, host mm, lines/arcs), "slab_only"
(outer loops only), "fills", and "stairs" (plan convex hull of every IFC stair near the level) for
plan_slab.py's stair-void rule.
"""
import io
import json
import math
import time

t0 = time.time()
FT = 304.8
MM = 1 / FT

CATEGORIES = globals().get("CATEGORIES", ["Floors", "Roofs", "Generic Models"])
ZWIN = globals().get("ZWIN", [-700, 150])
MIN_AREA = globals().get("MIN_AREA", 0.5)
SLAB_RULE = globals().get("SLAB_RULE", {"cats": ["Floors"], "dz": [-100, 50], "min_thk": 150, "material": "CONCRETE"})
INCLUDE = globals().get("INCLUDE", {})
EXCLUDE = globals().get("EXCLUDE", {})
FILL = globals().get("FILL", "holes")
FILL_CATS = globals().get("FILL_CATS", ["Walls", "Structural Columns", "Columns"])
STAIR_CATS = globals().get("STAIR_CATS", ["Stairs"])
STAIR_REACH = globals().get("STAIR_REACH", 3500)
EMBED = globals().get("EMBED", 0.6)
PARAMS = ["IfcGUID", "IfcPresentationLayer", "IfcSpatialContainer", "IfcName", "IfcMaterial", "IfcDescription",
          "IfcObjectType", "Mark"]


def eid(e):
    i = e.Id if hasattr(e, 'Id') else e
    v = getattr(i, "Value", None)
    return int(v) if v is not None else i.IntegerValue


def nm(e):
    try:
        return e.Name
    except Exception:
        return DB.Element.Name.__get__(e)


def pval(e, name):
    p = e.LookupParameter(name)
    if p is None or not p.HasValue:
        return None
    st = p.StorageType
    if st == DB.StorageType.String:
        return p.AsString()
    if st == DB.StorageType.Double:
        return round(p.AsDouble() * FT, 1)
    if st == DB.StorageType.Integer:
        return p.AsInteger()
    return None


links = list(DB.FilteredElementCollector(doc).OfClass(DB.RevitLinkInstance))
li = doc.GetElement(DB.ElementId(LINK_INSTANCE_ID)) if 'LINK_INSTANCE_ID' in globals() else links[0]
ld = li.GetLinkDocument()
tr = li.GetTotalTransform()


def P(p):
    return [round(p.X * FT, 1), round(p.Y * FT, 1), round(p.Z * FT, 1)]


def curve_rec(c):
    """Host-coordinate curve -> JSON (mm)."""
    if isinstance(c, DB.Arc):
        return {"arc": [P(c.Center), round(c.Radius * FT, 1), P(c.GetEndPoint(0)), P(c.GetEndPoint(1)), P(c.Evaluate(0.5, True))]}
    if isinstance(c, DB.Line):
        return {"line": [P(c.GetEndPoint(0)), P(c.GetEndPoint(1))]}
    pts = [P(q) for q in c.Tessellate()]
    return {"poly": pts}


def flatten(geo, acc):
    for o in geo:
        if isinstance(o, DB.GeometryInstance):
            flatten(o.GetInstanceGeometry(), acc)
        else:
            acc.append(o)
    return acc


def host_bbox(bb):
    cs = [bb.Min, bb.Max]
    pts = [tr.OfPoint(DB.XYZ(x.X, y.Y, z.Z)) for x in cs for y in cs for z in cs]
    return [min(p.X for p in pts) * FT, min(p.Y for p in pts) * FT, min(p.Z for p in pts) * FT,
            max(p.X for p in pts) * FT, max(p.Y for p in pts) * FT, max(p.Z for p in pts) * FT]


def mesh_up_loops(mesh, zlo, zhi):
    """Up-facing triangles of a mesh (host coords) grouped by z -> directed boundary edges chained into loops.
    Returns [(z_ft, area_ft2, [[XYZ, ...], ...])]. Triangle winding follows the up normal, so outer loops come
    out counter-clockwise and holes clockwise."""
    groups = {}
    for k in range(mesh.NumTriangles):
        t = mesh.get_Triangle(k)
        a, b, c = [tr.OfPoint(t.get_Vertex(j)) for j in range(3)]
        n = (b - a).CrossProduct(c - a)
        ar = n.GetLength() / 2
        if ar < 1e-8:
            continue
        if n.Z / (2 * ar) < 0.99:
            continue
        z = (a.Z + b.Z + c.Z) / 3
        if not (zlo <= z <= zhi):
            continue
        g = groups.setdefault(int(round(z * FT)), {"area": 0.0, "tris": []})
        g["area"] += ar
        g["tris"].append((a, b, c))
    res = []
    for zk, g in groups.items():
        key = lambda p: (int(round(p.X * FT * 2)), int(round(p.Y * FT * 2)))      # 0.5 mm grid
        pts = {}
        edges = {}
        for tri in g["tris"]:
            ks = [key(p) for p in tri]
            for p, kk in zip(tri, ks):
                pts.setdefault(kk, p)
            for i in range(3):
                u, v = ks[i], ks[(i + 1) % 3]
                if u == v:
                    continue
                if (v, u) in edges:            # shared with a neighbour -> interior edge
                    edges[(v, u)] -= 1
                    if edges[(v, u)] == 0:
                        del edges[(v, u)]
                else:
                    edges[(u, v)] = edges.get((u, v), 0) + 1
        nxt = {}
        for (u, v) in edges:
            nxt.setdefault(u, []).append(v)
        loops = []
        used = set()
        for (u, v) in list(edges):
            if (u, v) in used:
                continue
            loop = [u]
            cur = u
            nx = v
            used.add((u, v))
            guard = 0
            while nx != u and guard < 100000:
                loop.append(nx)
                cands = [w for w in nxt.get(nx, []) if (nx, w) not in used]
                if not cands:
                    break
                used.add((nx, cands[0]))
                cur, nx = nx, cands[0]
                guard += 1
            if nx == u and len(loop) >= 3:
                loops.append([pts[k] for k in loop])
        res.append((zk * MM, g["area"], loops))
    return res


def rec_loops(loops):
    return [[curve_rec(c) for c in cl] for cl in loops]


def face_rec(face):
    """[outer, holes...] of CurveLoops or point lists -> JSON loops."""
    return [[curve_rec(c) for c in lp] if isinstance(lp, DB.CurveLoop) else
            [{"line": [P(lp[i]), P(lp[(i + 1) % len(lp)])]} for i in range(len(lp))] for lp in face]


def pts_loops_rec(loops):
    return [[{"line": [P(lp[i]), P(lp[(i + 1) % len(lp)])]} for i in range(len(lp))] for lp in loops]


def candidates(level_z):
    zlo, zhi = level_z + ZWIN[0] * MM, level_z + ZWIN[1] * MM
    inv = tr.Inverse
    out = []
    for cn in CATEGORIES:
        cat = ld.Settings.Categories.get_Item(cn) if cn in [c.Name for c in ld.Settings.Categories] else None
        if cat is None:
            continue
        for e in DB.FilteredElementCollector(ld).OfCategoryId(cat.Id).WhereElementIsNotElementType():
            bb = e.get_BoundingBox(None)
            if bb is None:
                continue
            hb = host_bbox(bb)
            if hb[5] * MM < zlo or hb[2] * MM > zhi:
                continue
            tops = []          # (z_ft, area_ft2, "solid"/"mesh", loops as CurveLoop list or point lists)
            downs = []
            for o in flatten(e.get_Geometry(DB.Options()) or [], []):
                if isinstance(o, DB.Solid) and o.Volume > 1e-9:
                    for f in o.Faces:
                        if not isinstance(f, DB.PlanarFace):
                            continue
                        nz = tr.OfVector(f.FaceNormal).Z
                        z = tr.OfPoint(f.Origin).Z
                        if nz > 0.99 and zlo <= z <= zhi and f.Area * 0.092903 >= MIN_AREA:
                            cls = [DB.CurveLoop.CreateViaTransform(cl, tr) for cl in f.GetEdgesAsCurveLoops()]
                            tops.append((z, f.Area, "solid", cls))
                        elif nz < -0.99:
                            downs.append((z, f.Area))
                elif isinstance(o, DB.Mesh):
                    for z, ar, lps in mesh_up_loops(o, zlo, zhi):
                        if ar * 0.092903 >= MIN_AREA:
                            tops.append((z, ar, "mesh", lps))
                    for k in range(o.NumTriangles):
                        t = o.get_Triangle(k)
                        a, b, c = [tr.OfPoint(t.get_Vertex(j)) for j in range(3)]
                        n = (b - a).CrossProduct(c - a)
                        if n.GetLength() > 1e-8 and n.Normalize().Z < -0.99:
                            downs.append(((a.Z + b.Z + c.Z) / 3, n.GetLength() / 2))
            if not tops:
                continue
            out.append((e, cn, hb, tops, downs))
    return out


def thickness(z, downs):
    below = [d for d, a in downs if d < z - 1e-4]
    return round((z - max(below)) * FT, 1) if below else None


def flat_curve(c, z):
    def f(p):
        return DB.XYZ(p.X, p.Y, z)
    if isinstance(c, DB.Arc):
        return DB.Arc.Create(f(c.GetEndPoint(0)), f(c.GetEndPoint(1)), f(c.Evaluate(0.5, True)))
    return DB.Line.CreateBound(f(c.GetEndPoint(0)), f(c.GetEndPoint(1)))


def to_curveloop(loop, z, tol):
    """CurveLoop (solid face) or list of XYZ (mesh) -> CurveLoop at height z; tiny edges are dropped."""
    cl = DB.CurveLoop()
    if isinstance(loop, DB.CurveLoop):
        for c in loop:
            cl.Append(flat_curve(c, z))
        return cl
    pts = []
    for p in loop:
        q = DB.XYZ(p.X, p.Y, z)
        if not pts or q.DistanceTo(pts[-1]) > tol:
            pts.append(q)
    if pts[0].DistanceTo(pts[-1]) <= tol:
        pts.pop()
    for i in range(len(pts)):
        cl.Append(DB.Line.CreateBound(pts[i], pts[(i + 1) % len(pts)]))
    return cl


def poly_area(pts):
    return sum(pts[i].X * pts[(i + 1) % len(pts)].Y - pts[(i + 1) % len(pts)].X * pts[i].Y for i in range(len(pts))) / 2.0


def inside(p, pts):
    c = False
    for i in range(len(pts)):
        a, b = pts[i], pts[(i + 1) % len(pts)]
        if (a.Y > p.Y) != (b.Y > p.Y) and p.X < a.X + (p.Y - a.Y) * (b.X - a.X) / (b.Y - a.Y):
            c = not c
    return c


def nest(loops):
    """Point loops (any orientation) -> faces [outer CCW, holes CW...] by containment depth."""
    info = sorted([(abs(poly_area(lp)), lp) for lp in loops if len(lp) >= 3], key=lambda x: -x[0])
    faces = []
    for i, (ar, lp) in enumerate(info):
        parents = [j for j in range(i) if inside(lp[0], info[j][1])]
        if len(parents) % 2 == 0:
            faces.append([lp if poly_area(lp) > 0 else lp[::-1]])
            info[i] = (ar, lp, len(faces) - 1)
        else:
            par = info[parents[-1]]
            if len(par) == 3:
                faces[par[2]].append(lp if poly_area(lp) < 0 else lp[::-1])
    return faces


def mesh_section(mesh, zc):
    """Section of a (host-transformed) mesh at height zc: crossing segments chained into point loops."""
    key = lambda p: (int(round(p.X * FT * 2)), int(round(p.Y * FT * 2)))
    adj, pos = {}, {}
    for k in range(mesh.NumTriangles):
        t = mesh.get_Triangle(k)
        ps = [tr.OfPoint(t.get_Vertex(j)) for j in range(3)]
        cut = []
        for i in range(3):
            a, b = ps[i], ps[(i + 1) % 3]
            da, db = a.Z - zc, b.Z - zc
            if (da > 0) != (db > 0):
                s = da / (da - db)
                cut.append(DB.XYZ(a.X + (b.X - a.X) * s, a.Y + (b.Y - a.Y) * s, zc))
        if len(cut) != 2:
            continue
        ka, kb = key(cut[0]), key(cut[1])
        if ka == kb:
            continue
        pos[ka], pos[kb] = cut[0], cut[1]
        adj.setdefault(ka, []).append(kb)
        adj.setdefault(kb, []).append(ka)
    used, loops = set(), []
    for s0 in list(adj):
        for n0 in adj[s0]:
            if (s0, n0) in used:
                continue
            loop, prev, cur = [s0], s0, n0
            used.add((s0, n0))
            used.add((n0, s0))
            while cur != s0 and len(loop) < 100000:
                loop.append(cur)
                nxt = [w for w in adj[cur] if (cur, w) not in used]
                if not nxt:
                    break
                used.add((cur, nxt[0]))
                used.add((nxt[0], cur))
                prev, cur = cur, nxt[0]
            if cur == s0 and len(loop) >= 3:
                loops.append([pos[q] for q in loop])
    return loops


def section_faces(e, zc):
    """Plan section of a link element at host height zc -> faces, each [outer, holes...] as CurveLoops (solids)
    or point lists (meshes), host coordinates."""
    zl = tr.Inverse.OfPoint(DB.XYZ(0, 0, zc)).Z
    down = DB.Plane.CreateByNormalAndOrigin(DB.XYZ(0, 0, -1), DB.XYZ(0, 0, zl))
    faces, mesh_loops = [], []
    for o in flatten(e.get_Geometry(DB.Options()) or [], []):
        if isinstance(o, DB.Mesh):
            mesh_loops.extend(mesh_section(o, zc))
            continue
        if not isinstance(o, DB.Solid) or o.Volume < 1e-9:
            continue
        try:
            part = DB.BooleanOperationsUtils.CutWithHalfSpace(o, down)
        except Exception:
            continue
        if part is None or part.Volume < 1e-9:
            continue
        for f in part.Faces:
            if isinstance(f, DB.PlanarFace) and f.FaceNormal.Z > 0.99 and abs(f.Origin.Z - zl) < 0.01:
                faces.append([DB.CurveLoop.CreateViaTransform(cl, tr) for cl in f.GetEdgesAsCurveLoops()])
    faces.extend(nest(mesh_loops))
    return faces


def hull2d(pts):
    """Convex hull (monotone chain) of [(x, y)] -> counter-clockwise list."""
    pts = sorted(set(pts))
    if len(pts) < 3:
        return pts

    def cross(o, a, b):
        return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])
    lo, hi = [], []
    for p in pts:
        while len(lo) >= 2 and cross(lo[-2], lo[-1], p) <= 0:
            lo.pop()
        lo.append(p)
    for p in reversed(pts):
        while len(hi) >= 2 and cross(hi[-2], hi[-1], p) <= 0:
            hi.pop()
        hi.append(p)
    return lo[:-1] + hi[:-1]


def stair_footprints(level_z, box):
    """IFC stairs reaching within STAIR_REACH of the level: plan convex hull of their geometry (host mm)."""
    out = []
    names = [c.Name for c in ld.Settings.Categories]
    for cn in STAIR_CATS:
        if cn not in names:
            continue
        cat = ld.Settings.Categories.get_Item(cn)
        for e in DB.FilteredElementCollector(ld).OfCategoryId(cat.Id).WhereElementIsNotElementType():
            bb = e.get_BoundingBox(None)
            if bb is None:
                continue
            hb = host_bbox(bb)
            if hb[5] * MM < level_z - STAIR_REACH * MM or hb[2] * MM > level_z + STAIR_REACH * MM:
                continue
            if hb[3] < box[0] or hb[0] > box[3] or hb[4] < box[1] or hb[1] > box[4]:
                continue
            pts = []
            for o in flatten(e.get_Geometry(DB.Options()) or [], []):
                if isinstance(o, DB.Solid):
                    for ed in o.Edges:
                        for q in ed.Tessellate():
                            h = tr.OfPoint(q)
                            pts.append((round(h.X * FT, 1), round(h.Y * FT, 1)))
                elif isinstance(o, DB.Mesh):
                    for q in o.Vertices:
                        h = tr.OfPoint(q)
                        pts.append((round(h.X * FT, 1), round(h.Y * FT, 1)))
            if pts:
                out.append({"id": eid(e), "cat": cn, "IfcName": pval(e, "IfcName"),
                            "dz": [round(hb[2] - level_z * FT, 1), round(hb[5] - level_z * FT, 1)],
                            "hull": [list(p) for p in hull2d(pts)]})
    return out


def fill_parts(zc, box):
    """Walls/columns of the link cut at zc (mid-slab), within the slab's bbox (host mm)."""
    out = []
    names = [c.Name for c in ld.Settings.Categories]
    for cn in FILL_CATS:
        if cn not in names:
            continue
        cat = ld.Settings.Categories.get_Item(cn)
        for e in DB.FilteredElementCollector(ld).OfCategoryId(cat.Id).WhereElementIsNotElementType():
            bb = e.get_BoundingBox(None)
            if bb is None:
                continue
            hb = host_bbox(bb)
            if not (hb[2] * MM < zc < hb[5] * MM):
                continue
            if hb[3] < box[0] or hb[0] > box[3] or hb[4] < box[1] or hb[1] > box[4]:
                continue
            faces = section_faces(e, zc)
            if faces:
                out.append((e, faces))
    return out


def extrude(cls, z0, h, errs, tag):
    try:
        mv = DB.Transform.CreateTranslation(DB.XYZ(0, 0, z0))
        return DB.GeometryCreationUtilities.CreateExtrusionGeometry([DB.CurveLoop.CreateViaTransform(cl, mv) for cl in cls],
                                                                    DB.XYZ.BasisZ, h)
    except Exception as ex:
        errs.append([tag, "extrude: " + str(ex)[:100]])
        return None


def boolean(a, b, op, errs, tag):
    try:
        return DB.BooleanOperationsUtils.ExecuteBooleanOperation(a, b, op)
    except Exception as ex:
        errs.append([tag, str(op) + ": " + str(ex)[:100]])
        return a


def union_all(solids, errs, tag):
    solids = [s for s in solids if s is not None]
    if not solids:
        return None
    while len(solids) > 1:                      # pairwise, so each union stays small
        nxt = []
        for i in range(0, len(solids) - 1, 2):
            nxt.append(boolean(solids[i], solids[i + 1], DB.BooleanOperationsType.Union, errs, tag))
        if len(solids) % 2:
            nxt.append(solids[-1])
        solids = nxt
    return solids[0]


def top_faces(solid, ztop):
    res = []
    if solid is None:
        return res
    for f in solid.Faces:
        if isinstance(f, DB.PlanarFace) and f.FaceNormal.Z > 0.99 and abs(f.Origin.Z - ztop) < 0.01:
            res.append({"area": round(f.Area * 0.092903, 2), "loops": rec_loops(f.GetEdgesAsCurveLoops())})
    return res


def merge(level_z, parts, fills):
    """Slab region from the slab faces' loops projected onto the level: A = union of the outer loops,
    Hs = union of the holes. FILL="holes": the holes covered by walls/columns cut at mid-slab are filled back
    (R = A - (Hs - F)); FILL="union": R = (A - Hs) + F; FILL="none": R = A - Hs.
    Each loop is extruded (A 300 mm; hole and fill tools taller, so no faces are coplanar) and combined with
    Revit's solid booleans; the result's top face is the outline."""
    tol = max(doc.Application.ShortCurveTolerance, 1.0 * MM)
    errs = []
    H = 300 * MM
    outers, holes = [], []
    for e, z, kind, loops in parts:
        for lp in loops:
            cl = to_curveloop(lp, 0.0, tol)
            (outers if cl.IsCounterclockwise(DB.XYZ.BasisZ) else holes).append((eid(e), cl))
    A = union_all([extrude([cl], level_z, H, errs, i) for i, cl in outers], errs, "A")
    Hs = union_all([extrude([cl], level_z - 20 * MM, H + 40 * MM, errs, i) for i, cl in holes], errs, "H")
    F = None
    if fills and FILL != "none":
        F = union_all([extrude([to_curveloop(lp, 0.0, tol) for lp in face], level_z - 40 * MM, H + 80 * MM, errs, eid(e))
                       for e, faces in fills for face in faces], errs, "F")
    if FILL == "union":
        R = boolean(A, Hs, DB.BooleanOperationsType.Difference, errs, "A-H") if Hs is not None else A
        if F is not None:
            R = boolean(R, F, DB.BooleanOperationsType.Union, errs, "R+F")
    elif Hs is not None:
        voids = boolean(Hs, F, DB.BooleanOperationsType.Difference, errs, "H-F") if F is not None else Hs
        R = boolean(A, voids, DB.BooleanOperationsType.Difference, errs, "A-voids")
    else:
        R = A
    ztop = level_z + H
    embedded = []
    if FILL == "holes" and fills and EMBED:
        R, embedded = add_embedded(R, fills, level_z, H, ztop, tol, errs)
    return {"outline": top_faces(R, ztop), "slab_only": top_faces(A, ztop), "embedded": embedded}, errs


def add_embedded(R, fills, level_z, H, ztop, tol, errs):
    """Walls/columns cut into the slab edge (a facade wall splitting the slab from its balcony strip leaves a
    slot open at one end) are not holes, so FILL="holes" misses them. A fill face whose surroundings are mostly
    slab - EMBED of the points sampled 30 mm outside its outer loop lie on the slab - and that is not already
    covered is unioned in. Returns (solid, [ids])."""
    def top_face(s):
        fs = [f for f in s.Faces if isinstance(f, DB.PlanarFace) and f.FaceNormal.Z > 0.99 and abs(f.Origin.Z - ztop) < 0.01]
        return fs

    def on_slab(fs, x, y):
        q = DB.XYZ(x, y, ztop)
        for f in fs:
            r = f.Project(q)
            if r is not None and r.Distance < 1e-6:
                return True
        return False

    added = []
    for e, faces in fills:
        for face in faces:
            loops = [to_curveloop(lp, ztop, tol) for lp in face]
            ccw = [cl for cl in loops if cl.IsCounterclockwise(DB.XYZ.BasisZ)]
            if not ccw:
                continue
            outer = ccw[0]
            fs = top_face(R)
            out_pts, in_pts = [], []
            for c in outer:
                L = c.Length
                n = max(2, int(L / (200 * MM)))
                for k in range(n):
                    t = c.GetEndParameter(0) + (c.GetEndParameter(1) - c.GetEndParameter(0)) * (k + 0.5) / n
                    d = c.ComputeDerivatives(t, False)
                    p, tg = d.Origin, d.BasisX.Normalize()
                    nx, ny = tg.Y, -tg.X                       # right of a CCW loop = outside
                    out_pts.append((p.X + nx * 30 * MM, p.Y + ny * 30 * MM))
                    in_pts.append((p.X - nx * 30 * MM, p.Y - ny * 30 * MM))
            if not out_pts:
                continue
            frac = sum(1 for x, y in out_pts if on_slab(fs, x, y)) / float(len(out_pts))
            covered = sum(1 for x, y in in_pts if on_slab(fs, x, y)) / float(len(in_pts))
            if frac < EMBED or covered > 0.95:
                continue
            s = extrude([to_curveloop(lp, 0.0, tol) for lp in face], level_z, H, errs, eid(e))
            if s is None:
                continue
            try:
                R = DB.BooleanOperationsUtils.ExecuteBooleanOperation(R, s, DB.BooleanOperationsType.Union)
                added.append({"id": eid(e), "surrounded": round(frac, 2)})
            except Exception as ex:
                errs.append([eid(e), "embed union: " + str(ex)[:80]])
    return R, added


res = {"meta": {"link": li.Name, "zwin": ZWIN, "rule": SLAB_RULE, "categories": CATEGORIES, "fill": FILL,
                "fill_cats": FILL_CATS}, "levels": {}}
for key in sorted(LEVELS):
    lvl = doc.GetElement(DB.ElementId(LEVELS[key]))
    Z = lvl.Elevation
    rows, parts = [], []
    inc = set(INCLUDE.get(key, []))
    exc = set(EXCLUDE.get(key, []))
    for e, cn, hb, tops, downs in candidates(Z):
        r = {"id": eid(e), "cat": cn, "bb": [round(v, 1) for v in hb]}
        for n in PARAMS:
            r[n] = pval(e, n)
        t = ld.GetElement(e.GetTypeId())
        r["type"] = nm(t) if t is not None else None
        faces = []
        for z, ar, kind, loops in tops:
            fr = {"dz": round((z - Z) * FT, 1), "area": round(ar * 0.092903, 2), "kind": kind, "thk": thickness(z, downs)}
            fr["loops"] = rec_loops(loops) if kind == "solid" else pts_loops_rec(loops)
            faces.append(fr)
        r["faces"] = faces
        mat = (r.get("IfcMaterial") or "").upper()
        ok = []
        for (z, ar, kind, loops), fr in zip(tops, faces):
            good = (cn in SLAB_RULE.get("cats", [cn]) and SLAB_RULE["dz"][0] <= fr["dz"] <= SLAB_RULE["dz"][1]
                    and (fr["thk"] or 0) >= SLAB_RULE.get("min_thk", 0)
                    and SLAB_RULE.get("material", "").upper() in mat)
            if (good or r["id"] in inc) and r["id"] not in exc:
                parts.append((e, z, kind, loops))
                ok.append(True)
            else:
                ok.append(False)
        r["in_slab"] = any(ok)
        rows.append(r)
    # slab band from the largest merged face: its top and thickness; walls/columns are cut at mid-depth
    best = None
    for r in rows:
        for fr in (r["faces"] if r["in_slab"] else []):
            if best is None or fr["area"] > best["area"]:
                best = fr
    band, fills, stairs = None, [], []
    if best is not None:
        thk = best["thk"] or SLAB_RULE.get("min_thk", 200)
        band = {"top_dz": best["dz"], "thk": thk, "mid_dz": round(best["dz"] - thk / 2.0, 1)}
        bbs = [r["bb"] for r in rows if r["in_slab"]]
        box = [min(b[0] for b in bbs), min(b[1] for b in bbs), 0, max(b[3] for b in bbs), max(b[4] for b in bbs), 0]
        if FILL != "none":
            fills = fill_parts(Z + band["mid_dz"] * MM, box)
        stairs = stair_footprints(Z, box)
    merged, errs = merge(Z, parts, fills)
    res["levels"][key] = {"level": nm(lvl), "level_id": LEVELS[key], "level_z": round(Z * FT, 1), "band": band,
                          "candidates": rows, "outline": merged["outline"], "slab_only": merged["slab_only"],
                          "embedded": merged["embedded"],
                          "fill": FILL, "fills": [{"id": eid(e), "cat": e.Category.Name, "IfcName": pval(e, "IfcName"),
                                                   "IfcMaterial": pval(e, "IfcMaterial"),
                                                   "loops": [lp for face in faces for lp in face_rec(face)]}
                                                  for e, faces in fills],
                          "stairs": stairs, "errors": errs,
                          "slab_ids": sorted(set(eid(p[0]) for p in parts))}

f = io.open(OUT, "w", encoding="utf-8")
f.write(unicode(json.dumps(res, ensure_ascii=False)))
f.close()
summ = {}
for k, v in res["levels"].items():
    summ[k] = {"candidates": len(v["candidates"]), "slab_ids": v["slab_ids"], "band": v["band"],
               "fills": len(v["fills"]), "embedded": v["embedded"], "stairs": [s["id"] for s in v["stairs"]],
               "n_errors": len(v["errors"]), "errors": v["errors"][:5],
               "slab_only": [(o["area"], len(o["loops"])) for o in v["slab_only"]],
               "outline": [(o["area"], [len(lp) for lp in o["loops"]]) for o in v["outline"]]}
summ["secs"] = round(time.time() - t0, 1)
print(json.dumps(summ))
