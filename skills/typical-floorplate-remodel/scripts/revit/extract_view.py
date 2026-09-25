# -*- coding: utf-8 -*-
"""Dump every linked-IFC element visible in a host plan view to JSON (host coordinates, mm).

Runs INSIDE Revit (IronPython 2.7) through execute_revit_code. Set the inputs, then execfile this file:

    VIEW_ID = 950844                 # host plan view to read (e.g. "LEVEL 5 - TYPICAL FLOOR")
    LINK_VIEW_NAME = "LEVEL 5"       # plan view inside the link used for door/window plan symbols
    OUT = r"C:\\...\\work\\ifc_L5.json"
    # optional: LINK_INSTANCE_ID = 950744   (defaults to the first Revit link instance)
    execfile(r"<repo>\\skills\\typical-floorplate-remodel\\scripts\\revit\\extract_view.py")

Per element it records the IFC properties (GUID, layer, storey, name, material, host GUID, BaseQuantities
width/height/length/depth), the bounding box, the IFC wall axis (the "Axis" polyline or arc), the plan
section at the view's cut plane (or the top faces for elements below it) and, for doors and windows, the
plan symbol curves from the link's own plan view (swing arcs).
"""
import json
import time

t0 = time.time()


def eid(e):
    i = e.Id if hasattr(e, 'Id') else e
    v = getattr(i, "Value", None)
    return int(v) if v is not None else i.IntegerValue


def pval(e, name):
    p = e.LookupParameter(name)
    if p is None:
        return None
    st = p.StorageType
    if st == DB.StorageType.String:
        return p.AsString()
    if st == DB.StorageType.Double:
        return p.AsDouble()
    if st == DB.StorageType.Integer:
        return p.AsInteger()
    return None


def mmv(v):
    return None if v is None else round(v * 304.8, 1)


links = list(DB.FilteredElementCollector(doc).OfClass(DB.RevitLinkInstance))
if 'LINK_INSTANCE_ID' in globals():
    li = doc.GetElement(DB.ElementId(LINK_INSTANCE_ID))
else:
    li = links[0]
ld = li.GetLinkDocument()
tr = li.GetTotalTransform()
view = doc.GetElement(DB.ElementId(VIEW_ID))
lvl = view.GenLevel
vr = view.GetViewRange()
zcut_host = lvl.Elevation + vr.GetOffset(DB.PlanViewPlane.CutPlane)
zcut = tr.Inverse.OfPoint(DB.XYZ(0, 0, zcut_host)).Z          # cut plane in link coordinates
lview = [v for v in DB.FilteredElementCollector(ld).OfClass(DB.ViewPlan) if not v.IsTemplate and v.Name == LINK_VIEW_NAME][0]


def P(p):
    q = tr.OfPoint(p)
    return [round(q.X * 304.8, 1), round(q.Y * 304.8, 1), round(q.Z * 304.8, 1)]


def flatten(geo, acc):
    for o in geo:
        if isinstance(o, DB.GeometryInstance):
            flatten(o.GetInstanceGeometry(), acc)
        else:
            acc.append(o)
    return acc


def curve_rec(c):
    if isinstance(c, DB.Arc):
        return {"arc": [P(c.Center), round(c.Radius * 304.8, 1), P(c.GetEndPoint(0)), P(c.GetEndPoint(1)), P(c.Evaluate(0.5, True))]}
    return {"pts": [P(p) for p in c.Tessellate()]}


def loops_of_face(f):
    res = []
    for cl in f.GetEdgesAsCurveLoops():
        res.append([curve_rec(c) for c in cl])
    return res


opt = DB.Options()
opt.IncludeNonVisibleObjects = True
optv = DB.Options()
optv.View = lview
down = DB.Plane.CreateByNormalAndOrigin(DB.XYZ(0, 0, -1), DB.XYZ(0, 0, zcut))

PARAMS = ["IfcGUID", "IfcPresentationLayer", "IfcSpatialContainer", "IfcName", "IfcMaterial",
          "IfcContainedInHost", "IfcContainedInHostGUID", "Mark", "ClassificationCode"]
DIMS = ["BaseQuantities.Width", "BaseQuantities.Height", "BaseQuantities.Length", "BaseQuantities.Depth"]
SECTION_CATS = ("Walls", "Doors", "Windows", "Structural Columns", "Columns", "Floors", "Stairs", "Railings",
                "Curtain Panels", "Generic Models")

els = list(DB.FilteredElementCollector(doc, view.Id, li.Id).WhereElementIsNotElementType())   # Revit 2024+
rows = []
errs = 0
for e in els:
    if e.Category is None:
        continue
    cat = e.Category.Name
    r = {"id": eid(e), "cat": cat, "cls": e.GetType().Name}
    for n in PARAMS:
        r[n] = pval(e, n)
    for n in DIMS:
        r[n.split(".")[1]] = mmv(pval(e, n))
    t = ld.GetElement(e.GetTypeId())
    if t is not None:
        r["type"] = DB.Element.Name.__get__(t)
        for n in ["IfcOperationType", "Operation", "OperationType", "OperationType 2", "PanelPosition", "PanelPosition 2"]:
            v = pval(t, n)
            if v:
                r[n] = v
    bb = e.get_BoundingBox(None)
    if bb:
        cs = [bb.Min, bb.Max]
        pts = [P(DB.XYZ(x.X, y.Y, z.Z)) for x in cs for y in cs for z in cs]
        r["bb"] = [min(p[0] for p in pts), min(p[1] for p in pts), min(p[2] for p in pts),
                   max(p[0] for p in pts), max(p[1] for p in pts), max(p[2] for p in pts)]
    try:
        objs = flatten(e.get_Geometry(opt) or [], [])
    except Exception:
        objs = []
    axis, cut, top = [], [], []
    nsol = 0
    for o in objs:
        if isinstance(o, DB.PolyLine) or isinstance(o, DB.Curve):
            gs = ld.GetElement(o.GraphicsStyleId) if o.GraphicsStyleId.IntegerValue > 0 else None
            rec = {"pts": [P(p) for p in o.GetCoordinates()]} if isinstance(o, DB.PolyLine) else curve_rec(o)
            rec["style"] = gs.Name if gs else None
            axis.append(rec)
        elif isinstance(o, DB.Solid) and o.Volume > 1e-6:
            nsol += 1
            try:
                got = False
                if cat in SECTION_CATS:
                    part = DB.BooleanOperationsUtils.CutWithHalfSpace(o, down)
                    if part is not None and part.Volume > 1e-9:
                        for f in part.Faces:
                            if isinstance(f, DB.PlanarFace) and f.FaceNormal.Z > 0.99 and abs(f.Origin.Z - zcut) < 0.01:
                                cut.extend(loops_of_face(f))
                                got = True
                # top faces: the projection for elements below the cut plane; for walls always, because a wall
                # with a tall opening is cut only at its jambs while its top face runs over the lintel
                if not got or cat == "Walls":
                    for f in o.Faces:
                        if isinstance(f, DB.PlanarFace) and f.FaceNormal.Z > 0.99:
                            top.extend(loops_of_face(f))
            except Exception:
                errs += 1
    r["axis"], r["cut"], r["top"], r["nsol"] = axis, cut, top, nsol
    if cat in ("Doors", "Windows"):
        plan = []
        try:
            for o in flatten(e.get_Geometry(optv) or [], []):
                if isinstance(o, DB.PolyLine):
                    plan.append({"pts": [P(p) for p in o.GetCoordinates()]})
                elif isinstance(o, DB.Curve):
                    plan.append(curve_rec(o))
        except Exception:
            pass
        r["plan"] = plan
    rows.append(r)

meta = {"view": view.Name, "view_id": VIEW_ID, "level": lvl.Name, "level_id": eid(lvl), "level_z": round(lvl.Elevation * 304.8, 1),
        "zcut": round(zcut_host * 304.8, 1), "link": li.Name, "n": len(rows), "errs": errs, "secs": round(time.time() - t0, 1)}
f = open(OUT, "w")
f.write(json.dumps({"meta": meta, "rows": rows}))
f.close()
print(json.dumps(meta))
