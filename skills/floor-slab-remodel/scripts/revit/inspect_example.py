# -*- coding: utf-8 -*-
"""Dump the floors (and what sits on them) of an example .rvt - typically a saved group such as
"A1-FLOOR SLAB_LEVEL 5.rvt" - to JSON (mm), without disturbing the user's open model.

Runs INSIDE Revit (IronPython 2.7) through execute_revit_code. Set the inputs, then execfile this file:

    FILES = {"L5": r"<project>\\group_examples\\A1-FLOOR SLAB_LEVEL 5.rvt"}
    OUT = r"<work>\\example_slabs.json"
    execfile(r"<repo>\\skills\\floor-slab-remodel\\scripts\\revit\\inspect_example.py")

Each file is opened in the background (Application.OpenDocumentFile), read and closed without saving.
Per floor it records the type and its layers, level, offsets, parameters, and every sketch loop as a list of
curves (lines/arcs in the file's own coordinates). Also records generic models (e.g. setdown voids),
openings, slab edges and the element-count summary per category/family/type.
"""
import io
import json
from collections import Counter


def eid(e):
    i = e.Id if hasattr(e, 'Id') else e
    v = getattr(i, "Value", None)
    return int(v) if v is not None else i.IntegerValue


def nm(e):
    try:
        return e.Name
    except Exception:
        return DB.Element.Name.__get__(e)


def P(p):
    return [round(p.X * 304.8, 1), round(p.Y * 304.8, 1), round(p.Z * 304.8, 1)]


def curve_rec(c):
    if isinstance(c, DB.Arc):
        return {"arc": [P(c.Center), round(c.Radius * 304.8, 1), P(c.GetEndPoint(0)), P(c.GetEndPoint(1)), P(c.Evaluate(0.5, True))]}
    if isinstance(c, DB.Line):
        return {"line": [P(c.GetEndPoint(0)), P(c.GetEndPoint(1))]}
    return {"pts": [P(q) for q in c.Tessellate()], "kind": c.GetType().Name}


def params(e, names):
    ps = {}
    for pn in names:
        p = e.LookupParameter(pn)
        if p is None or not p.HasValue:
            continue
        ps[pn] = p.AsValueString() if p.StorageType != DB.StorageType.String else p.AsString()
    return ps


SKIP = ("<Sketch>", "Materials", "Legend Components", "Pipe Segments", "Sun Path", "Material Assets",
        "Survey Point", "Project Base Point", "Internal Origin", "Cameras", "Primary Contours", "HVAC Zones",
        "Project Information")
FLOOR_PARAMS = ["Height Offset From Level", "Structural", "Thickness", "Area", "Perimeter", "Room Bounding",
                "Slope", "Comments", "Mark", "Elevation at Top", "Elevation at Bottom", "Core Thickness"]

app = doc.Application
res = {}
for key in sorted(FILES):
    d = app.OpenDocumentFile(FILES[key])
    r = {"file": FILES[key]}
    try:
        els = [e for e in DB.FilteredElementCollector(d).WhereElementIsNotElementType()
               if e.Category is not None and e.Category.CategoryType == DB.CategoryType.Model and e.Category.Name not in SKIP]
        c = Counter()
        for e in els:
            t = d.GetElement(e.GetTypeId())
            c[(e.Category.Name, getattr(t, "FamilyName", "") if t else "", nm(t) if t else "")] += 1
        r["counts"] = [list(k) + [v] for k, v in c.most_common()]
        r["levels"] = [(nm(l), round(l.Elevation * 304.8, 1)) for l in DB.FilteredElementCollector(d).OfClass(DB.Level)]
        bp = [e for e in DB.FilteredElementCollector(d).OfCategory(DB.BuiltInCategory.OST_ProjectBasePoint)]
        sp = [e for e in DB.FilteredElementCollector(d).OfCategory(DB.BuiltInCategory.OST_SharedBasePoint)]
        r["base_point"] = P(bp[0].Position) if bp else None
        r["survey_point"] = P(sp[0].Position) if sp else None
        fls = []
        for f in DB.FilteredElementCollector(d).OfClass(DB.Floor):
            t = d.GetElement(f.GetTypeId())
            cs = t.GetCompoundStructure()
            layers = [(str(l.Function), round(l.Width * 304.8, 1),
                       nm(d.GetElement(l.MaterialId)) if eid(l.MaterialId) > 0 else None) for l in cs.GetLayers()] if cs else []
            loops = []
            sk = d.GetElement(f.SketchId) if hasattr(f, "SketchId") else None
            if sk is not None:
                for ca in sk.Profile:
                    loops.append([curve_rec(cv) for cv in ca])
            bb = f.get_BoundingBox(None)
            fls.append({"id": eid(f), "type": nm(t), "family": getattr(t, "FamilyName", ""),
                        "level": nm(d.GetElement(f.LevelId)), "layers": layers,
                        "params": params(f, FLOOR_PARAMS), "type_params": params(t, ["Function", "Structural Material", "Default Thickness"]),
                        "loops": loops,
                        "bb": P(bb.Min) + P(bb.Max) if bb else None})
        r["floors"] = fls
        gms = []
        for e in els:
            if e.Category.Name != "Generic Models" or not isinstance(e, DB.FamilyInstance):
                continue
            t = d.GetElement(e.GetTypeId())
            loc = e.Location
            bb = e.get_BoundingBox(None)
            gms.append({"id": eid(e), "family": t.FamilyName, "type": nm(t),
                        "pt": P(loc.Point) if isinstance(loc, DB.LocationPoint) else None,
                        "host": eid(e.Host) if e.Host else None,
                        "bb": P(bb.Min) + P(bb.Max) if bb else None})
        r["generic_models"] = gms
        ops = []
        for o in DB.FilteredElementCollector(d).OfClass(DB.Opening):
            rec = {"id": eid(o), "cat": o.Category.Name if o.Category else None, "host": eid(o.Host) if o.Host else None}
            if o.IsRectBoundary:
                rec["rect"] = [P(q) for q in o.BoundaryRect]
            else:
                rec["curves"] = [curve_rec(cv) for cv in o.BoundaryCurves]
            ops.append(rec)
        r["openings"] = ops
        r["slab_edges"] = [(eid(s), nm(d.GetElement(s.GetTypeId()))) for s in DB.FilteredElementCollector(d).OfClass(DB.HostedSweep)
                           if s.Category and s.Category.Id.IntegerValue == int(DB.BuiltInCategory.OST_EdgeSlab)]
    finally:
        d.Close(False)
    res[key] = r

f = io.open(OUT, "w", encoding="utf-8")
f.write(unicode(json.dumps(res, ensure_ascii=False)))
f.close()
summary = {}
for k, r in res.items():
    summary[k] = {"levels": r["levels"], "floors": [(x["type"], x["level"], x["params"].get("Height Offset From Level"),
                                                     x["params"].get("Structural"), x["params"].get("Area"), len(x["loops"]),
                                                     [len(lp) for lp in x["loops"]]) for x in r["floors"]],
                  "generic_models": len(r["generic_models"]), "openings": len(r["openings"]), "slab_edges": len(r["slab_edges"]),
                  "counts": r["counts"]}
print(json.dumps(summary, ensure_ascii=False))
