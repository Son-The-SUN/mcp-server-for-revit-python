# -*- coding: utf-8 -*-
"""Dump IFC space (IfcSpace) footprints from the linked IFC to JSON, per level.

Runs INSIDE Revit (IronPython 2.7) through execute_revit_code:

    LEVELS = [["L5", 30450], ["L6", 33700]]     # key, level elevation in mm (host coordinates)
    OUT = r"C:\\...\\work\\ifc_spaces.json"      # merged into the file if it already exists
    # optional: LINK_INSTANCE_ID = 950744
    execfile(r"<repo>\\skills\\typical-floorplate-remodel\\scripts\\revit\\extract_spaces.py")

Revit's IFC link brings IfcSpace in as Generic Model DirectShapes whose "Export to IFC As" is IfcSpaceType and
whose IfcName is the space name (unit numbers such as A1-05.01, balconies, common areas).

- The footprint is the space's horizontal section 1000 mm above the level. Spaces come as Solids or as triangle
  Meshes (on the first job A1-06.08 was a mesh); solids are cut with CutWithHalfSpace, meshes by intersecting
  every triangle with the plane and chaining the segments into loops.
- A space belongs to a level when its GEOMETRY spans the section height. Don't use the element bounding box: for
  mesh DirectShapes it can be wildly wrong (A1-06.08 reported 26 x 26 m and z 31250..39200 for a one-storey unit).
- Filter units by their level code (e.g. --unit-prefix A1-05. / A1-06.) in the local scripts.
"""
import json


def eid(e):
    i = e.Id if hasattr(e, 'Id') else e
    v = getattr(i, "Value", None)
    return int(v) if v is not None else i.IntegerValue


def pval(e, name):
    p = e.LookupParameter(name)
    if p is None:
        return None
    return p.AsString() if p.StorageType == DB.StorageType.String else None


links = list(DB.FilteredElementCollector(doc).OfClass(DB.RevitLinkInstance))
li = doc.GetElement(DB.ElementId(LINK_INSTANCE_ID)) if 'LINK_INSTANCE_ID' in globals() else links[0]
ld = li.GetLinkDocument()
tr = li.GetTotalTransform()


def P(p):
    q = tr.OfPoint(p)
    return [round(q.X * 304.8, 1), round(q.Y * 304.8, 1)]


def flatten(geo, acc):
    for o in geo:
        if isinstance(o, DB.GeometryInstance):
            flatten(o.GetInstanceGeometry(), acc)
        else:
            acc.append(o)
    return acc


def solid_section(solid, zlink):
    down = DB.Plane.CreateByNormalAndOrigin(DB.XYZ(0, 0, -1), DB.XYZ(0, 0, zlink))
    part = DB.BooleanOperationsUtils.CutWithHalfSpace(solid, down)
    loops = []
    if part is None:
        return loops
    for f in part.Faces:
        if isinstance(f, DB.PlanarFace) and f.FaceNormal.Z > 0.99 and abs(f.Origin.Z - zlink) < 0.01:
            for cl in f.GetEdgesAsCurveLoops():
                pts = []
                for c in cl:
                    tp = [P(p) for p in c.Tessellate()]
                    if pts:
                        tp = tp[1:]
                    pts.extend(tp)
                loops.append(pts)
    return loops


def mesh_section(mesh, zlink):
    """Intersect every triangle with z = zlink and chain the segments into closed loops (host mm)."""
    segs = []
    for i in range(mesh.NumTriangles):
        tri = mesh.get_Triangle(i)
        v = [tri.get_Vertex(k) for k in range(3)]
        pts = []
        for a, b in ((0, 1), (1, 2), (2, 0)):
            da, db = v[a].Z - zlink, v[b].Z - zlink
            if (da < 0) != (db < 0):
                t = da / (da - db)
                pts.append(DB.XYZ(v[a].X + (v[b].X - v[a].X) * t, v[a].Y + (v[b].Y - v[a].Y) * t, zlink))
        if len(pts) == 2:
            segs.append((P(pts[0]), P(pts[1])))
    key = lambda p: (int(round(p[0])), int(round(p[1])))
    adj = {}
    for s in segs:
        for p, q in ((s[0], s[1]), (s[1], s[0])):
            adj.setdefault(key(p), []).append(q)
    used = set()
    loops = []
    for s in segs:
        k0 = key(s[0])
        if (k0, key(s[1])) in used:
            continue
        loop = [s[0]]
        cur, prev = s[1], s[0]
        used.add((k0, key(s[1])))
        used.add((key(s[1]), k0))
        for _ in range(len(segs) + 1):
            loop.append(cur)
            if key(cur) == k0:
                break
            nxt = None
            for q in adj.get(key(cur), []):
                if (key(cur), key(q)) not in used:
                    nxt = q
                    break
            if nxt is None:
                break
            used.add((key(cur), key(nxt)))
            used.add((key(nxt), key(cur)))
            prev, cur = cur, nxt
        if len(loop) > 3:
            loops.append(loop)
    return loops


def zrange(objs):
    zs = []
    for o in objs:
        if isinstance(o, DB.Solid) and o.Volume > 0:
            bb = o.GetBoundingBox()
            zs += [bb.Transform.OfPoint(bb.Min).Z, bb.Transform.OfPoint(bb.Max).Z]
        elif isinstance(o, DB.Mesh):
            zs += [o.Vertices[i].Z for i in range(o.Vertices.Count)]
    return (min(zs), max(zs)) if zs else None


try:
    res = json.loads(open(OUT).read())
except Exception:
    res = {}
spaces = []
for e in DB.FilteredElementCollector(ld).OfCategory(DB.BuiltInCategory.OST_GenericModel).WhereElementIsNotElementType():
    if pval(e, "Export to IFC As") != "IfcSpaceType":
        continue
    objs = flatten(e.get_Geometry(DB.Options()) or [], [])
    zr = zrange(objs)
    if zr:
        spaces.append((e, objs, zr))
report = []
for key, zmm in LEVELS:
    zsec = tr.Inverse.OfPoint(DB.XYZ(0, 0, (zmm + 1000) / 304.8)).Z
    sp = []
    for e, objs, (z0, z1) in spaces:
        if not (z0 <= zsec <= z1):
            continue
        loops = []
        for o in objs:
            try:
                if isinstance(o, DB.Solid) and o.Volume > 0:
                    loops.extend(solid_section(o, zsec))
                elif isinstance(o, DB.Mesh):
                    loops.extend(mesh_section(o, zsec))
            except Exception:
                pass
        sp.append({"id": eid(e), "name": pval(e, "IfcName"), "zmin": round(tr.OfPoint(DB.XYZ(0, 0, z0)).Z * 304.8),
                   "loops": loops, "mesh": any(isinstance(o, DB.Mesh) for o in objs)})
    res[key] = sp
    report.append("{}: {} spaces ({})".format(key, len(sp), ", ".join(
        sorted("%s%s" % (s["name"] or "-", "" if s["loops"] else "[no outline]") for s in sp))))
f = open(OUT, "w")
f.write(json.dumps(res))
f.close()
print("\n".join(report))
