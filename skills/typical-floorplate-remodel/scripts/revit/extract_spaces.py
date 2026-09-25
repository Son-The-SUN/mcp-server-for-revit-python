# -*- coding: utf-8 -*-
"""Dump IFC space (IfcSpace) footprints from the linked IFC to JSON, per level.

Runs INSIDE Revit (IronPython 2.7) through execute_revit_code:

    LEVELS = [["L5", 30450], ["L6", 33700]]     # key, level elevation in mm (host coordinates)
    OUT = r"C:\\...\\work\\ifc_spaces.json"      # merged into the file if it already exists
    # optional: LINK_INSTANCE_ID = 950744
    execfile(r"<repo>\\skills\\typical-floorplate-remodel\\scripts\\revit\\extract_spaces.py")

Revit's IFC link brings IfcSpace in as Generic Model DirectShapes whose "Export to IFC As" is IfcSpaceType and
whose IfcName is the space name (unit numbers such as A1-05.01, balconies, common areas). A space belongs to a
level when its bottom is within 1000 mm below to 2600 mm above the level (some spaces start below the slab).
The footprint is the space's horizontal section 1000 mm above the level.
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


def section_loops(solid, zlink):
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


try:
    res = json.loads(open(OUT).read())
except Exception:
    res = {}
spaces = [e for e in DB.FilteredElementCollector(ld).OfCategory(DB.BuiltInCategory.OST_GenericModel).WhereElementIsNotElementType()
          if pval(e, "Export to IFC As") == "IfcSpaceType"]
report = []
for key, zmm in LEVELS:
    zhost = zmm / 304.8
    zlink = tr.Inverse.OfPoint(DB.XYZ(0, 0, zhost + 1000 / 304.8)).Z
    sp = []
    for e in spaces:
        bb = e.get_BoundingBox(None)
        if bb is None:
            continue
        zmin = tr.OfPoint(bb.Min).Z
        if not (zhost - 1000 / 304.8 <= zmin <= zhost + 2600 / 304.8) or tr.OfPoint(bb.Max).Z < zhost + 1100 / 304.8:
            continue
        loops = []
        for o in flatten(e.get_Geometry(DB.Options()) or [], []):
            if isinstance(o, DB.Solid) and o.Volume > 0:
                try:
                    loops.extend(section_loops(o, zlink))
                except Exception:
                    pass
        sp.append({"id": eid(e), "name": pval(e, "IfcName"), "zmin": round(zmin * 304.8), "loops": loops})
    res[key] = sp
    report.append("{}: {} spaces ({})".format(key, len(sp), ", ".join(sorted(set(s["name"] or "-" for s in sp)))))
f = open(OUT, "w")
f.write(json.dumps(res))
f.close()
print("\n".join(report))
