# -*- coding: utf-8 -*-
"""Build one floor in Revit from plan_model.py's plan (walls, columns, doors, windows).

Runs INSIDE Revit (IronPython 2.7) through execute_revit_code:

    PLAN = r"<scratchpad>\\plan_GF.json"
    MANIFEST = r"<scratchpad>\\manifest_GF.json"     # plan id -> Revit id, written by every step
    LEVEL = "GROUND FLOOR"
    TOP_LEVEL = "LEVEL 1"                           # walls/columns go up to this level
    STEP = "walls"        # "walls" | "columns" | "openings" | "remove"
    RULES = {...}         # optional, see DEFAULT_RULES below (families per opening tag, wall/column types)
    execfile(r"<repo>\\skills\\dwg-to-rvt-converter\\scripts\\revit\\build_model.py")

- walls: `GROUPGSA - GENERIC-<t>mm` (made from GENERIC-100mm with the layer set to t when missing), centreline,
  base LEVEL, top constraint TOP_LEVEL. One transaction per step ("MCP: DWG <LEVEL> walls").
- columns: structural columns, `concrete` -> COLUMN - CONCRETE - SQUARE "<w> x <d>mm", `steel` -> RULES["steel_column"].
- openings: hosted in the wall built from the plan wall; type "<W> x <H>" in the tag's family (sizes from the
  schedule, else from the drawn gap); windows get their sill. Doors are flipped until their own plan swing arc
  matches the DWG (hinge point and swing side), so family conventions don't matter.
- remove: deletes what the manifest recorded (all steps, or RMSTEPS = ["openings"]).
"""
import json
import math
import os

MM = 304.8
RULES = globals().get("RULES", {})
DEFAULT_RULES = {
    "wall_type": "GROUPGSA - GENERIC-{t}mm",
    "wall_type_source": "GROUPGSA - GENERIC-100mm",
    "concrete_column": {"family": "COLUMN - CONCRETE - SQUARE", "type": "{w} x {d}mm", "params": {"b": "w"}},
    "steel_column": {"family": "COLUMN - STEEL - UNIVERSAL", "type": "{w} UC", "params": {}},
    # per tag: family + param formulas (W, H = schedule width/height in mm)
    "openings": {},
    "door_single": {"family": "DOOR - SINGLE - STD", "params": {"Width": "W-60", "Height": "H-50"}},
    "door_double": {"family": "DOOR - DOUBLE - STD", "params": {"Width": "W-80", "Panel Width": "(W-80)/2", "Height": "H-50"}},
    "window": {"family": "WNDW - Fixed_1Panel", "params": {"Width": "W", "Height": "H"}},
    "default_door_height": 2350,
}
for k, v in DEFAULT_RULES.items():
    RULES.setdefault(k, v)
RMSTEPS = globals().get("RMSTEPS", None)


def nm(e):
    try:
        return e.Name
    except Exception:
        return DB.Element.Name.__get__(e)


def idv(eid):
    return int(getattr(eid, "Value", None) or eid.IntegerValue)   # Int64 -> int, or json can't write it


def ft(mm):
    return mm / MM


def xyz(p, z=0.0):
    return DB.XYZ(ft(p[0]), ft(p[1]), z)


import io
plan = json.load(io.open(PLAN, encoding="utf-8"))
def load_manifest():
    if not os.path.isfile(MANIFEST):
        return {}
    with open(MANIFEST) as f:
        txt = f.read()
    return json.loads(txt) if txt.strip() else {}


def save_manifest():
    tmp = MANIFEST + ".tmp"
    with open(tmp, "w") as f:
        f.write(json.dumps(man, indent=1))
    if os.path.isfile(MANIFEST):
        os.remove(MANIFEST)
    os.rename(tmp, MANIFEST)


man = load_manifest()
for k in ("walls", "columns", "openings", "types"):
    man.setdefault(k, {})
levels = {nm(l): l for l in DB.FilteredElementCollector(doc).OfClass(DB.Level)}
level = levels[LEVEL]
top = levels.get(globals().get("TOP_LEVEL")) if globals().get("TOP_LEVEL") else None


class Quiet(DB.IFailuresPreprocessor):
    """Dismiss warnings; resolve errors with their default resolution; record everything."""
    def __init__(self):
        self.msgs = []

    def PreprocessFailures(self, fa):
        for f in fa.GetFailureMessages():
            self.msgs.append(f.GetDescriptionText())
            if f.GetSeverity() == DB.FailureSeverity.Warning:
                fa.DeleteWarning(f)
            elif f.HasResolutions():
                fa.ResolveFailure(f)
        return DB.FailureProcessingResult.Continue


def run(name, fn):
    t = DB.Transaction(doc, "MCP: DWG {} {}".format(LEVEL, name))
    q = Quiet()
    o = t.GetFailureHandlingOptions()
    o.SetFailuresPreprocessor(q)
    t.SetFailureHandlingOptions(o)
    t.Start()
    try:
        out = fn()
        t.Commit()
    except Exception:
        t.RollBack()
        raise
    save_manifest()
    if q.msgs:
        seen = {}
        for m in q.msgs:
            seen[m] = seen.get(m, 0) + 1
        out.append("warnings: " + "; ".join("{} x{}".format(m[:80], n) for m, n in seen.items()))
    return out


def evalf(expr, W, H):
    return float(eval(str(expr), {"__builtins__": {}}, {"W": W, "H": H, "w": W, "d": H}))


# ------------------------------------------------------------------ types

def wall_type(t):
    name = RULES["wall_type"].format(t=int(round(t)))
    for wt in DB.FilteredElementCollector(doc).OfClass(DB.WallType):
        if nm(wt) == name:
            return wt
    src = [wt for wt in DB.FilteredElementCollector(doc).OfClass(DB.WallType) if nm(wt) == RULES["wall_type_source"]][0]
    new = src.Duplicate(name)
    cs = new.GetCompoundStructure()
    cs.SetLayerWidth(0, ft(t))
    new.SetCompoundStructure(cs)
    man["types"][name] = idv(new.Id)
    return new


def symbol_type(family, type_name, params, W, H, category=None):
    syms = [s for s in DB.FilteredElementCollector(doc).OfClass(DB.FamilySymbol) if s.FamilyName == family]
    if not syms:
        raise Exception("family '{}' not in the model - load it (load_family.py)".format(family))
    for s in syms:
        if nm(s) == type_name:
            if not s.IsActive:
                s.Activate()
            return s, False
    new = syms[0].Duplicate(type_name)
    for pname, expr in params.items():
        p = new.LookupParameter(pname)
        if p is None or p.IsReadOnly:
            continue
        v = evalf(expr, W, H)
        if p.StorageType == DB.StorageType.Double:
            p.Set(ft(v))
        elif p.StorageType == DB.StorageType.Integer:
            p.Set(int(v))
    if not new.IsActive:
        new.Activate()
    man["types"]["{} : {}".format(family, type_name)] = idv(new.Id)
    return new, True


# ------------------------------------------------------------------ steps

def adopt_walls():
    """Walls built by an earlier run whose manifest was not saved: find them by their comment."""
    tag = "DWG {} ".format(os.path.basename(PLAN))
    n = 0
    for wl in DB.FilteredElementCollector(doc).OfClass(DB.Wall):
        cm = wl.get_Parameter(DB.BuiltInParameter.ALL_MODEL_INSTANCE_COMMENTS)
        s_ = cm.AsString() if cm else None
        if s_ and s_.startswith(tag) and wl.LevelId == level.Id:
            pid = s_[len(tag):]
            if pid not in man["walls"]:
                man["walls"][pid] = idv(wl.Id)
                n += 1
    return n


def do_walls():
    out, n = [], 0
    k = adopt_walls()
    if k:
        out.append("adopted {} walls from an earlier run".format(k))
    for w in plan["walls"]:
        if w["id"] in man["walls"] and doc.GetElement(DB.ElementId(man["walls"][w["id"]])) is not None:
            continue
        wt = wall_type(w["t"])
        line = DB.Line.CreateBound(xyz(w["a"], level.Elevation), xyz(w["b"], level.Elevation))
        h = (top.Elevation - level.Elevation) if top else ft(3000)
        wall = DB.Wall.Create(doc, line, wt.Id, level.Id, h, 0.0, False, False)
        if top:
            wall.get_Parameter(DB.BuiltInParameter.WALL_HEIGHT_TYPE).Set(top.Id)
        wall.get_Parameter(DB.BuiltInParameter.WALL_KEY_REF_PARAM).Set(0)   # location line: wall centreline
        cm = wall.get_Parameter(DB.BuiltInParameter.ALL_MODEL_INSTANCE_COMMENTS)
        if cm:
            cm.Set("DWG {} {}".format(os.path.basename(PLAN), w["id"]))
        man["walls"][w["id"]] = idv(wall.Id)
        n += 1
    out.append("walls created: {} (total in manifest {})".format(n, len(man["walls"])))
    return out


def do_columns():
    out, n = [], 0
    for c in plan["columns"]:
        if c["id"] in man["columns"] and doc.GetElement(DB.ElementId(man["columns"][c["id"]])) is not None:
            continue
        rule = RULES["steel_column"] if c.get("kind") == "steel" else RULES["concrete_column"]
        w, d = int(round(c["w"])), int(round(c["d"]))
        sym, _ = symbol_type(rule["family"], rule["type"].format(w=w, d=d), rule.get("params", {}), w, d)
        inst = doc.Create.NewFamilyInstance(xyz(c["c"], level.Elevation), sym, level, DB.Structure.StructuralType.Column)
        if top:
            inst.get_Parameter(DB.BuiltInParameter.FAMILY_TOP_LEVEL_PARAM).Set(top.Id)
            inst.get_Parameter(DB.BuiltInParameter.FAMILY_TOP_LEVEL_OFFSET_PARAM).Set(0.0)
        inst.get_Parameter(DB.BuiltInParameter.FAMILY_BASE_LEVEL_OFFSET_PARAM).Set(0.0)
        man["columns"][c["id"]] = idv(inst.Id)
        n += 1
    out.append("columns created: {}".format(n))
    return out


def plan_view():
    for v in DB.FilteredElementCollector(doc).OfClass(DB.ViewPlan):
        if not v.IsTemplate and v.ViewType == DB.ViewType.FloorPlan and v.GenLevel and v.GenLevel.Id == level.Id:
            return v
    return None


def swing_arcs(inst, view):
    """Plan arcs of a door instance (in model coordinates)."""
    opt = DB.Options()
    opt.View = view
    arcs = []

    def walk(ge, tr):
        for g in ge:
            if isinstance(g, DB.GeometryInstance):
                walk(g.GetInstanceGeometry(), None)
            elif isinstance(g, DB.Arc):
                arcs.append(g)
    walk(inst.get_Geometry(opt), None)
    return arcs


def orient_door(inst, op, view):
    """Flip facing / hand until the door's biggest plan arc matches the DWG hinge and swing side."""
    if not op.get("swing_normal") or not op.get("hinge"):
        return "no swing in DWG"
    hinge = DB.XYZ(ft(op["hinge"][0]), ft(op["hinge"][1]), 0)
    sn = DB.XYZ(op["swing_normal"][0], op["swing_normal"][1], 0)
    c0 = DB.XYZ(ft(op["center"][0]), ft(op["center"][1]), 0)

    def score():
        doc.Regenerate()
        arcs = swing_arcs(inst, view)
        if not arcs:
            return None
        a = max(arcs, key=lambda g: g.Radius)
        cen = DB.XYZ(a.Center.X, a.Center.Y, 0)
        mid = a.Evaluate(0.5, True)
        side = (DB.XYZ(mid.X, mid.Y, 0) - c0).DotProduct(sn)
        return cen.DistanceTo(hinge) + (0 if side > 0 else 100.0)

    best = score()
    if best is None:
        return "no arcs in the family's plan view"
    for flip in ("facing", "hand", "facing", "hand"):
        (inst.flipFacing if flip == "facing" else inst.flipHand)()
        s = score()
        if s < best - 1e-6:
            best = s
        else:
            (inst.flipFacing if flip == "facing" else inst.flipHand)()   # undo
    return "ok" if best < ft(200) else "check (off {:.0f} mm)".format(best * MM)


def do_openings():
    out, n, notes = [], 0, []
    view = plan_view()
    for w in plan["walls"]:
        host = doc.GetElement(DB.ElementId(man["walls"].get(w["id"], -1))) if w["id"] in man["walls"] else None
        for op in w.get("openings", []):
            if op["id"] in man["openings"] and doc.GetElement(DB.ElementId(man["openings"][op["id"]])) is not None:
                continue
            if host is None:
                notes.append("{}: host {} not built".format(op["id"], w["id"]))
                continue
            tag = op.get("tag")
            if tag:
                tag = tag.replace(u"Đ", u"D").replace(u"đ", u"d")    # Đ -> D: rules are keyed in ASCII
            rule = RULES["openings"].get(tag) if tag else None
            if rule is None:
                if op["kind"] == "door":
                    rule = RULES["door_double"] if op.get("leaves") == 2 else RULES["door_single"]
                elif op["kind"] == "window" and tag:
                    rule = RULES["window"]
                else:
                    notes.append("{}: untagged {} {} mm left out".format(op["id"], op["kind"], op["gap"]))
                    continue
            if rule.get("skip"):
                notes.append("{}: {} skipped by rule".format(op["id"], tag))
                continue
            W = op.get("width") or op["gap"]
            H = op.get("height") or rule.get("height") or RULES["default_door_height"]
            tname = rule.get("type", "{W} x {H}").format(W=int(W), H=int(H))
            sym, made = symbol_type(rule["family"], tname, rule.get("params", {}), W, H)
            inst = doc.Create.NewFamilyInstance(xyz(op["center"], level.Elevation), sym, host, level,
                                                DB.Structure.StructuralType.NonStructural)
            sill = op.get("sill", rule.get("sill"))
            if sill is not None:
                p = inst.get_Parameter(DB.BuiltInParameter.INSTANCE_SILL_HEIGHT_PARAM)
                if p and not p.IsReadOnly:
                    p.Set(ft(sill))
            state = ""
            if inst.Category.Id.IntegerValue == int(DB.BuiltInCategory.OST_Doors) and view is not None:
                state = orient_door(inst, op, view)
            tm = sym.get_Parameter(DB.BuiltInParameter.ALL_MODEL_TYPE_MARK)     # the DWG tag is a type mark
            if tm and tag and not tm.IsReadOnly and tm.AsString() != tag:
                tm.Set(tag)
            man["openings"][op["id"]] = idv(inst.Id)
            n += 1
            if state and state != "ok":
                notes.append("{} {}: swing {}".format(op["id"], tag, state))
    out.append("openings placed: {}".format(n))
    out += notes
    return out


def do_remove():
    ids = []
    for k in (RMSTEPS or ["openings", "columns", "walls"]):
        for pid, rid in list(man[k].items()):
            if doc.GetElement(DB.ElementId(rid)) is not None:
                ids.append(DB.ElementId(rid))
            del man[k][pid]
    from System.Collections.Generic import List
    if ids:
        doc.Delete(List[DB.ElementId](ids))
    return ["removed {} elements".format(len(ids))]


fn = {"walls": do_walls, "columns": do_columns, "openings": do_openings, "remove": do_remove}[STEP]
print("\n".join(run(STEP, fn)))
