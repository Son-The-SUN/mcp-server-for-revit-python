# -*- coding: utf-8 -*-
"""Floors and roofs from outlines in plan mm (the extracted views share the model origin).

Runs INSIDE Revit (IronPython 2.7) through execute_revit_code:

    FLOORS = [
        {"id": "GF-SLAB", "level": "GROUND FLOOR", "type": "CONCRETE 150MM", "offset_mm": 0,
         "outline": [[1406, 3], [14006, 3], [14006, 15173], [1406, 15173]]},
    ]
    ROOFS = [
        # gable: two opposite edges define the slope; the slope comes from slope_deg, or from ridge_mm
        {"id": "ROOF-MAIN", "level": "ROOF", "type": "Generic - 125mm", "outline": [[596, -737], ...],
         "slope_edges": "x-min,x-max", "ridge_mm": 10630},
        # lean-to: one edge slopes
        {"id": "ROOF-EAST", "level": "ROOF", "type": "Generic - 125mm", "outline": [...], "slope_edges": "x-max", "slope_deg": 29},
    ]
    MANIFEST = r"<scratchpad>\\build_envelope.json"
    # optional: STEP = "remove"
    execfile(r"<repo>\\skills\\dwg-to-rvt-converter\\scripts\\revit\\build_envelope.py")

slope_edges names the outline edges that slope, by the side of the outline they lie on:
"x-min" (west), "x-max" (east), "y-min" (south), "y-max" (north). One transaction "MCP: DWG floors and roofs".
"""
import json
import math
import os
import clr

MM = 304.8
FLOORS = globals().get("FLOORS", [])
ROOFS = globals().get("ROOFS", [])
STEP = globals().get("STEP", "build")


def nm(e):
    try:
        return e.Name
    except Exception:
        return DB.Element.Name.__get__(e)


def idv(eid):
    return int(getattr(eid, "Value", None) or eid.IntegerValue)


def ft(v):
    return v / MM


man = {}
if os.path.isfile(MANIFEST):
    with open(MANIFEST) as f:
        txt = f.read()
    man = json.loads(txt) if txt.strip() else {}
levels = {nm(l): l for l in DB.FilteredElementCollector(doc).OfClass(DB.Level)}


def loop(outline, z):
    pts = [DB.XYZ(ft(p[0]), ft(p[1]), z) for p in outline]
    ca = DB.CurveArray()
    cl = DB.CurveLoop()
    for i in range(len(pts)):
        ln = DB.Line.CreateBound(pts[i], pts[(i + 1) % len(pts)])
        ca.Append(ln)
        cl.Append(ln)
    return ca, cl


def edge_side(p0, p1, outline):
    xs = [p[0] for p in outline]
    ys = [p[1] for p in outline]
    sides = []
    if abs(p0.X - p1.X) < 1e-6:
        x = p0.X * MM
        if abs(x - min(xs)) < 5:
            sides.append("x-min")
        if abs(x - max(xs)) < 5:
            sides.append("x-max")
    if abs(p0.Y - p1.Y) < 1e-6:
        y = p0.Y * MM
        if abs(y - min(ys)) < 5:
            sides.append("y-min")
        if abs(y - max(ys)) < 5:
            sides.append("y-max")
    return sides


out = []
t = DB.Transaction(doc, "MCP: DWG floors and roofs" if STEP != "remove" else "MCP: DWG remove floors and roofs")
t.Start()
try:
    if STEP == "remove":
        from System.Collections.Generic import List
        ids = [DB.ElementId(v) for v in man.values() if doc.GetElement(DB.ElementId(v)) is not None]
        if ids:
            doc.Delete(List[DB.ElementId](ids))
        out.append("removed {}".format(len(ids)))
        man = {}
    else:
        ftypes = {nm(x): x for x in DB.FilteredElementCollector(doc).OfClass(DB.FloorType)}
        rtypes = {nm(x): x for x in DB.FilteredElementCollector(doc).OfClass(DB.RoofType)}
        for f in FLOORS:
            if f["id"] in man and doc.GetElement(DB.ElementId(man[f["id"]])) is not None:
                continue
            lv = levels[f["level"]]
            ca, cl = loop(f["outline"], lv.Elevation)
            from System.Collections.Generic import List
            fl = DB.Floor.Create(doc, List[DB.CurveLoop]([cl]), ftypes[f["type"]].Id, lv.Id)
            fl.get_Parameter(DB.BuiltInParameter.FLOOR_HEIGHTABOVELEVEL_PARAM).Set(ft(f.get("offset_mm", 0)))
            if f.get("structural") is not None:
                fl.get_Parameter(DB.BuiltInParameter.FLOOR_PARAM_IS_STRUCTURAL).Set(1 if f["structural"] else 0)
            man[f["id"]] = idv(fl.Id)
            area = fl.get_Parameter(DB.BuiltInParameter.HOST_AREA_COMPUTED).AsDouble() * 0.09290304
            out.append("floor {} id={} {} on {} ({:.1f} m2)".format(f["id"], idv(fl.Id), f["type"], f["level"], area))
        for r in ROOFS:
            if r["id"] in man and doc.GetElement(DB.ElementId(man[r["id"]])) is not None:
                continue
            lv = levels[r["level"]]
            ca, cl = loop(r["outline"], lv.Elevation)
            mc = clr.Reference[DB.ModelCurveArray](DB.ModelCurveArray())   # a null StrongBox is refused
            roof = doc.Create.NewFootPrintRoof(ca, lv, rtypes[r["type"]], mc)
            want = [s.strip() for s in r.get("slope_edges", "").split(",") if s.strip()]
            if not want:
                tan = 0.0                                  # flat roof
            elif r.get("slope_deg") is not None:
                tan = math.tan(math.radians(r["slope_deg"]))
            else:
                xs = [p[0] for p in r["outline"]]
                ys = [p[1] for p in r["outline"]]
                span = (max(xs) - min(xs)) if any(w.startswith("x") for w in want) else (max(ys) - min(ys))
                half = span / 2.0 if len(want) == 2 else span
                rise = r["ridge_mm"] - lv.Elevation * MM - r.get("offset_mm", 0)
                tan = rise / half
            n_s = 0
            for c in mc.Value:
                crv = c.GeometryCurve
                sides = edge_side(crv.GetEndPoint(0), crv.GetEndPoint(1), r["outline"])
                slope = any(s in want for s in sides)
                roof.set_DefinesSlope(c, slope)
                if slope:
                    roof.set_SlopeAngle(c, tan)
                    n_s += 1
            if r.get("offset_mm"):
                roof.get_Parameter(DB.BuiltInParameter.ROOF_LEVEL_OFFSET_PARAM).Set(ft(r["offset_mm"]))
            man[r["id"]] = idv(roof.Id)
            out.append("roof {} id={} {} on {}: {} sloped edges at {:.1f} deg".format(r["id"], idv(roof.Id), r["type"], r["level"],
                                                                                   n_s, math.degrees(math.atan(tan))))
    t.Commit()
except Exception:
    t.RollBack()
    raise
tmp = MANIFEST + ".tmp"
with open(tmp, "w") as f:
    f.write(json.dumps(man, indent=1))
if os.path.isfile(MANIFEST):
    os.remove(MANIFEST)
os.rename(tmp, MANIFEST)
print("\n".join(out))
