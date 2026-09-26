# -*- coding: utf-8 -*-
"""Move the template's NORTH / SOUTH / EAST / WEST elevation markers so they stand around the new building.

Templates keep their markers around the origin; a building imported origin-to-origin often lands on one of them
(the elevation then cuts through the building). Runs INSIDE Revit through execute_revit_code:

    # optional: BOX_MM = (x0, y0, x1, y1)   # building extents in mm; default: box of all walls, floors and roofs
    # optional: GAP_MM = 4000               # clearance from the box
    # optional: PLAN = "GROUND FLOOR"       # plan view used to find the markers
    execfile(r"<repo>\\skills\\dwg-to-rvt-converter\\scripts\\revit\\place_elevation_markers.py")

Markers are matched by the names of the elevation views they host. One transaction "MCP: move elevation markers".
"""
MM = 304.8
GAP_MM = globals().get("GAP_MM", 4000)
PLAN = globals().get("PLAN", None)


def nm(e):
    try:
        return e.Name
    except Exception:
        return DB.Element.Name.__get__(e)


plans = [v for v in DB.FilteredElementCollector(doc).OfClass(DB.ViewPlan) if not v.IsTemplate and v.ViewType == DB.ViewType.FloorPlan]
plan = [v for v in plans if nm(v) == PLAN][0] if PLAN else plans[0]
box = globals().get("BOX_MM")
if not box:
    xs, ys = [], []
    for bic in (DB.BuiltInCategory.OST_Walls, DB.BuiltInCategory.OST_Floors, DB.BuiltInCategory.OST_Roofs):
        for e in DB.FilteredElementCollector(doc).OfCategory(bic).WhereElementIsNotElementType():
            bb = e.get_BoundingBox(None)
            if bb:
                xs += [bb.Min.X * MM, bb.Max.X * MM]
                ys += [bb.Min.Y * MM, bb.Max.Y * MM]
    box = (min(xs), min(ys), max(xs), max(ys))
bx0, by0, bx1, by1 = box
cx, cy = (bx0 + bx1) / 2.0, (by0 + by1) / 2.0
targets = {"NORTH": (cx, by1 + GAP_MM), "SOUTH": (cx, by0 - GAP_MM), "EAST": (bx1 + GAP_MM, cy), "WEST": (bx0 - GAP_MM, cy)}
out = []
t = DB.Transaction(doc, "MCP: move elevation markers")
t.Start()
try:
    for m in DB.FilteredElementCollector(doc).OfClass(DB.ElevationMarker):
        names = [nm(doc.GetElement(m.GetViewId(i))) for i in range(4) if m.GetViewId(i) != DB.ElementId.InvalidElementId]
        tgt = [targets[n.upper()] for n in names if n.upper() in targets]
        bb = m.get_BoundingBox(plan)
        if not tgt or bb is None:
            continue
        c = (bb.Min + bb.Max) / 2.0
        if m.Pinned:
            m.Pinned = False
        DB.ElementTransformUtils.MoveElement(doc, m.Id, DB.XYZ(tgt[0][0] / MM - c.X, tgt[0][1] / MM - c.Y, 0))
        out.append("{}: ({:.0f}, {:.0f}) -> ({:.0f}, {:.0f}) mm".format(",".join(names), float(c.X * MM), float(c.Y * MM),
                                                                      float(tgt[0][0]), float(tgt[0][1])))
    t.Commit()
except Exception:
    t.RollBack()
    raise
print("building box {:.0f},{:.0f} - {:.0f},{:.0f} mm".format(float(bx0), float(by0), float(bx1), float(by1)))
print("\n".join(out))
