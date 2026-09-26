# -*- coding: utf-8 -*-
"""Create the floor slab of one level from a slab plan (scripts/local/plan_slab.py output).

Runs INSIDE Revit (IronPython 2.7) through execute_revit_code. Set the inputs, then execfile this file:

    PLAN = r"<work>\\slab_L10.json"          # from plan_slab.py
    MANIFEST = r"<work>\\slab_manifest_L10.json"
    STEP = "build"                          # "build" | "rebuild" (delete the recorded floor first) | "remove" | "check"
    execfile(r"<repo>\\skills\\floor-slab-remodel\\scripts\\revit\\build_slab.py")

- The floor type is the plan's floor_type ("CONCRETE 220MM"). If the model has no such type, it is duplicated
  from type_source ("CONCRETE 200MM") with its structure layer set to the plan thickness - same materials,
  function and structure, only the thickness changes. Recorded in the manifest.
- The sketch is the plan's loops (outer edge + voids) at the level; lines and true arcs.
- "Height Offset From Level" = the plan offset (the example: -20, top of slab 20 below FFL);
  structural as in the plan.
- One transaction "MCP: floor slab <level> from IFC"; failures are recorded, errors resolved with Revit's
  default resolution. "check" only validates the loops (BoundaryValidation) without creating anything.
"""
import io
import json
from System.Collections.Generic import List

MM = 1 / 304.8


def eid(e):
    i = e.Id if hasattr(e, 'Id') else e
    v = getattr(i, "Value", None)
    return int(v) if v is not None else i.IntegerValue


def nm(e):
    try:
        return e.Name
    except Exception:
        return DB.Element.Name.__get__(e)


class Collector(DB.IFailuresPreprocessor):
    def __init__(self):
        self.msgs = []

    def PreprocessFailures(self, fa):
        handled = False
        for f in fa.GetFailureMessages():
            sev = f.GetSeverity()
            self.msgs.append([str(sev), f.GetDescriptionText(), [eid(i) for i in f.GetFailingElementIds()]])
            if sev == DB.FailureSeverity.Warning:
                fa.DeleteWarning(f)
            elif sev == DB.FailureSeverity.Error and f.HasResolutions():
                fa.ResolveFailure(f)
                handled = True
        return DB.FailureProcessingResult.ProceedWithCommit if handled else DB.FailureProcessingResult.Continue


def start(name):
    t = DB.Transaction(doc, name)
    col = Collector()
    opts = t.GetFailureHandlingOptions()
    opts.SetFailuresPreprocessor(col)
    opts.SetClearAfterRollback(True)
    t.SetFailureHandlingOptions(opts)
    t.Start()
    return t, col


def save(man):
    f = io.open(MANIFEST, "w", encoding="utf-8")
    f.write(unicode(json.dumps(man, indent=1, ensure_ascii=False)))
    f.close()


P = json.loads(io.open(PLAN, encoding="utf-8").read())
level = doc.GetElement(DB.ElementId(P["level_id"]))
Z = level.Elevation
try:
    MAN = json.loads(io.open(MANIFEST, encoding="utf-8").read())
except Exception:
    MAN = {"level": nm(level), "level_id": P["level_id"], "floors": [], "types_created": [], "failures": []}


def XY(p):
    return DB.XYZ(p[0] * MM, p[1] * MM, Z)


def curve_loops():
    loops = []
    for lp in P["loops"]:
        cl = DB.CurveLoop()
        for c in lp:
            if "line" in c:
                cl.Append(DB.Line.CreateBound(XY(c["line"][0]), XY(c["line"][1])))
            else:
                cc, r, p0, p1, pm = c["arc"]
                cl.Append(DB.Arc.Create(XY(p0), XY(p1), XY(pm)))
        loops.append(cl)
    return loops


def floor_type():
    types = dict((nm(t), t) for t in DB.FilteredElementCollector(doc).OfClass(DB.FloorType))
    if P["floor_type"] in types:
        return types[P["floor_type"]], False
    src = types.get(P["type_source"])
    if src is None:
        raise Exception("floor type '%s' missing and no source type '%s' to duplicate" % (P["floor_type"], P["type_source"]))
    ft = src.Duplicate(P["floor_type"])
    cs = ft.GetCompoundStructure()
    k = cs.StructuralMaterialIndex if cs.StructuralMaterialIndex >= 0 else cs.GetFirstCoreLayerIndex()
    others = sum(cs.GetLayerWidth(i) for i in range(cs.LayerCount) if i != k)
    cs.SetLayerWidth(k, P["thickness"] * MM - others)
    ft.SetCompoundStructure(cs)
    return ft, True


def existing():
    out = []
    for r in MAN["floors"]:
        e = doc.GetElement(DB.ElementId(r["id"]))
        if e is not None:
            out.append(e)
    return out


loops = curve_loops()
valid = DB.BoundaryValidation.IsValidHorizontalBoundary(List[DB.CurveLoop](loops))
res = {"level": nm(level), "step": STEP, "loops": len(loops), "curves": sum(lp.NumberOfCurves() for lp in loops), "valid": valid}

if STEP == "check":
    print(json.dumps(res))
elif STEP == "remove" or STEP == "rebuild" or STEP == "build":
    old = existing()
    if STEP == "build" and old:
        res["skipped"] = "floor already built: %s (use STEP='rebuild')" % [eid(e) for e in old]
        print(json.dumps(res))
    else:
        t, col = start("MCP: floor slab %s from IFC" % nm(level) if STEP != "remove" else "MCP: remove floor slab %s" % nm(level))
        try:
            if old:
                doc.Delete(List[DB.ElementId]([e.Id for e in old]))
                res["deleted"] = [eid(e) for e in old]
                MAN["floors"] = []
            if STEP != "remove":
                if not valid:
                    raise Exception("the plan's loops are not a valid horizontal boundary")
                ft, made = floor_type()
                if made:
                    MAN["types_created"].append("Floor: " + P["floor_type"])
                fl = DB.Floor.Create(doc, List[DB.CurveLoop](loops), ft.Id, level.Id, bool(P["structural"]), None, 0.0)
                fl.get_Parameter(DB.BuiltInParameter.FLOOR_HEIGHTABOVELEVEL_PARAM).Set(P["offset"] * MM)
                doc.Regenerate()
                MAN["floors"].append({"id": eid(fl), "unique_id": fl.UniqueId, "type": P["floor_type"],
                                      "offset": P["offset"], "structural": bool(P["structural"]),
                                      "area_m2": round(fl.get_Parameter(DB.BuiltInParameter.HOST_AREA_COMPUTED).AsDouble() * 0.09290304, 2),
                                      "ifc_slab_ids": P["ifc_slab_ids"], "plan": PLAN})
                res["floor"] = MAN["floors"][-1]
            t.Commit()
        except Exception as ex:
            if t.HasStarted() and not t.HasEnded():
                t.RollBack()
            res["error"] = str(ex)
        MAN["failures"].extend(col.msgs)
        res["failures"] = col.msgs[:10]
        save(MAN)
        print(json.dumps(res))
else:
    raise Exception("STEP must be build, rebuild, remove or check")
