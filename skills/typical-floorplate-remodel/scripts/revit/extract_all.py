# -*- coding: utf-8 -*-
"""Dump every wall, door and window of the linked IFC (all storeys) to JSON, for typical-band detection.

Runs INSIDE Revit (IronPython 2.7) through execute_revit_code:

    OUT = r"C:\\...\\work\\ifc_all.json"
    # optional: LINK_INSTANCE_ID = 950744
    execfile(r"<repo>\\skills\\typical-floorplate-remodel\\scripts\\revit\\extract_all.py")

Rows: [id, category, IfcPresentationLayer, width mm, height mm, IfcSpatialContainer, bbox mm (host coords)].
Also writes the link's levels and project information. Feed the file to scripts/local/typical_bands.py.
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
    st = p.StorageType
    if st == DB.StorageType.String:
        return p.AsString()
    if st == DB.StorageType.Double:
        return round(p.AsDouble() * 304.8, 1)
    return None


links = list(DB.FilteredElementCollector(doc).OfClass(DB.RevitLinkInstance))
li = doc.GetElement(DB.ElementId(LINK_INSTANCE_ID)) if 'LINK_INSTANCE_ID' in globals() else links[0]
ld = li.GetLinkDocument()
tr = li.GetTotalTransform()
rows = []
for bic in [DB.BuiltInCategory.OST_Walls, DB.BuiltInCategory.OST_Doors, DB.BuiltInCategory.OST_Windows]:
    for e in DB.FilteredElementCollector(ld).OfCategory(bic).WhereElementIsNotElementType():
        bb = e.get_BoundingBox(None)
        if bb is None:
            continue
        cs = [bb.Min, bb.Max]
        pts = [tr.OfPoint(DB.XYZ(x.X, y.Y, z.Z)) for x in cs for y in cs for z in cs]
        b = [round(min(p.X for p in pts) * 304.8), round(min(p.Y for p in pts) * 304.8), round(min(p.Z for p in pts) * 304.8),
             round(max(p.X for p in pts) * 304.8), round(max(p.Y for p in pts) * 304.8), round(max(p.Z for p in pts) * 304.8)]
        rows.append([eid(e), e.Category.Name, pval(e, "IfcPresentationLayer"), pval(e, "BaseQuantities.Width"),
                     pval(e, "BaseQuantities.Height"), pval(e, "IfcSpatialContainer"), b])
levels = [[l.Name, round(tr.OfPoint(DB.XYZ(0, 0, l.Elevation)).Z * 304.8)] for l in DB.FilteredElementCollector(ld).OfClass(DB.Level)]
pi = ld.ProjectInformation
info = {"BuildingName": pi.BuildingName, "Name": pi.Name, "Number": pi.Number}
f = open(OUT, "w")
f.write(json.dumps({"rows": rows, "levels": levels, "info": info}))
f.close()
print("rows {} | levels {} | info {}".format(len(rows), len(levels), info))
