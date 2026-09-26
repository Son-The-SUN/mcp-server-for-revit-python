# -*- coding: utf-8 -*-
"""Create native Revit walls / doors / windows from a build plan (scripts/local/plan_build.py output).

Runs INSIDE Revit (IronPython 2.7) through execute_revit_code. Set the inputs, then execfile this file:

    BUILD = r"<work>\\build_L5.json"        # from plan_build.py
    MANIFEST = r"<work>\\manifest_L5.json"  # IFC id -> Revit id record, created/updated by every step
    LEVEL_ID = 950753                      # host level
    VIEW_ID = 950844                       # host plan view on that level (door plan symbols are read in it)
    STEP = "walls"                         # "walls" | "doors" | "windows" | "remove"
    # optional: LIMIT = 15                 # create at most this many new elements per call (the tool times out
    #                                        at 60 s; the manifest records progress, so just call again)
    execfile(r"<repo>\\skills\\typical-floorplate-remodel\\scripts\\revit\\build_revit.py")

Each step is one transaction named "MCP: remodel <level> <step> from IFC", so the user can undo it in one go.
Elements already in the manifest are skipped, so a step can be re-run after a partial failure.

Rules baked in (see SKILL.md):
- wall types this script creates are always Generic: duplicated from the template generic
  (GENERIC_WALL_SOURCE / GENERIC_GLASS_SOURCE) with the single layer set to the IFC thickness;
- door/window types are "<W> x <H>" duplicates inside the planned family;
- Revit errors are resolved with their default resolution (e.g. unjoin); failing elements are deleted only
  when there is no resolution. Every failure message is kept in the manifest.
"""
import json
from System.Collections.Generic import List

MM = 1 / 304.8
GENERIC_PREFIX = globals().get("GENERIC_PREFIX", "GROUPGSA - GENERIC")
GENERIC_WALL_SOURCE = globals().get("GENERIC_WALL_SOURCE", "GROUPGSA - GENERIC-100mm")
GENERIC_GLASS_SOURCE = globals().get("GENERIC_GLASS_SOURCE", "GROUPGSA - GENERIC - 10mm GLASS")


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
    """Record every failure; resolve errors (default resolution first, delete only as a last resort)."""

    def __init__(self):
        self.msgs = []

    def PreprocessFailures(self, fa):
        handled = False
        for f in fa.GetFailureMessages():
            sev = f.GetSeverity()
            ids = [eid(i) for i in f.GetFailingElementIds()]
            self.msgs.append([str(sev), f.GetDescriptionText(), ids])
            if sev == DB.FailureSeverity.Error:
                if f.HasResolutions():
                    fa.ResolveFailure(f)
                    handled = True
                elif f.GetFailingElementIds().Count > 0:
                    fa.DeleteElements(List[DB.ElementId](f.GetFailingElementIds()))
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


level = doc.GetElement(DB.ElementId(LEVEL_ID))
view = doc.GetElement(DB.ElementId(VIEW_ID))
Z = level.Elevation
try:
    MAN = json.loads(open(MANIFEST).read())
except Exception:
    MAN = {"level": nm(level), "walls": {}, "doors": {}, "windows": {}, "types_created": [], "failures": [], "notes": []}
for k in ("walls", "doors", "windows"):
    MAN.setdefault(k, {})
MAN.setdefault("notes", [])
B = json.loads(open(BUILD).read()) if STEP != "remove" else None
LIMIT = globals().get("LIMIT", 100000)


def XYZ(p):
    return DB.XYZ(p[0] * MM, p[1] * MM, Z)


def wall_type(name):
    for wt in DB.FilteredElementCollector(doc).OfClass(DB.WallType):
        if nm(wt) == name:
            return wt
    return None


def ensure_wall_type(name, W):
    wt = wall_type(name)
    if wt:
        return wt
    if not name.startswith(GENERIC_PREFIX):
        raise Exception("refusing to create non-generic wall type '%s'" % name)
    src = wall_type(GENERIC_GLASS_SOURCE if "GLASS" in name.upper() else GENERIC_WALL_SOURCE)
    wt = src.Duplicate(name)
    cs = wt.GetCompoundStructure()
    cs.SetLayerWidth(0, W * MM)
    wt.SetCompoundStructure(cs)
    MAN["types_created"].append("Wall: " + name)
    return wt


def apply_type_params(s, fam, w):
    """Width-dependent type parameters from the plan's config, e.g. {"type_params": {"WNDW - 1Swing_1Fixed_2Panels":
    {"Door Panel Width": 0.5}}} = half the type width. Tower C: that family's swing panel stayed 1000 wide from the
    base type, and a 1050 window then failed ("Base sketch for extrusion is invalid")."""
    for pname, frac in ((B or {}).get("config", {}).get("type_params", {}).get(fam, {}) or {}).items():
        p = s.LookupParameter(pname)
        if p is not None and not p.IsReadOnly and abs(p.AsDouble() - w * frac * MM) > 1e-6:
            p.Set(w * frac * MM)


def sized_symbol(bic, fam, w, h):
    """Type '<w> x <h>' in family `fam`, duplicated from the family's first type when missing."""
    name = "%d x %d" % (w, h)
    base = None
    for s in DB.FilteredElementCollector(doc).OfClass(DB.FamilySymbol).OfCategory(bic):
        if s.FamilyName != fam:
            continue
        if nm(s) == name:
            apply_type_params(s, fam, w)
            return s
        base = base or s
    if base is None:
        raise Exception("family not loaded: %s" % fam)
    s = base.Duplicate(name)
    wb = DB.BuiltInParameter.DOOR_WIDTH if bic == DB.BuiltInCategory.OST_Doors else DB.BuiltInParameter.WINDOW_WIDTH
    hb = DB.BuiltInParameter.DOOR_HEIGHT if bic == DB.BuiltInCategory.OST_Doors else DB.BuiltInParameter.WINDOW_HEIGHT
    pw = s.get_Parameter(wb) or s.LookupParameter("Width")
    if pw is not None and not pw.IsReadOnly:
        pw.Set(w * MM)
    else:
        pp = s.LookupParameter("Panel Width")      # double doors whose Width is a formula of the panel width
        if pp is None or pp.IsReadOnly:
            raise Exception("cannot set width on %s" % fam)
        pp.Set(w * MM / 2.0)
    ph = s.get_Parameter(hb) or s.LookupParameter("Height")
    if ph is None or ph.IsReadOnly:
        raise Exception("cannot set height on %s" % fam)
    ph.Set(h * MM)
    apply_type_params(s, fam, w)
    MAN["types_created"].append("%s: %s : %s" % ("Door" if bic == DB.BuiltInCategory.OST_Doors else "Window", fam, name))
    return s


def flatten(geo, acc):
    for o in geo:
        if isinstance(o, DB.GeometryInstance):
            flatten(o.GetInstanceGeometry(), acc)
        else:
            acc.append(o)
    return acc


def plan_arcs(inst):
    opt = DB.Options()
    opt.View = view
    return [o for o in flatten(inst.get_Geometry(opt) or [], []) if isinstance(o, DB.Arc)]


def find_host(item, walls_by_ifc):
    """Planned host, else nearest straight wall, else a new host wall from the item's 'free' line."""
    host = walls_by_ifc.get(str(item.get("host_ifc")))
    pt = XYZ(item["pt"])
    if host is not None:
        return host, pt
    best = None
    for k, wl in walls_by_ifc.items():
        lc = wl.Location.Curve
        if not isinstance(lc, DB.Line):
            continue
        pr = lc.Project(pt)
        if pr is not None and pr.Distance < wl.Width / 2 + 100 * MM and (best is None or pr.Distance < best[0]):
            best = (pr.Distance, wl, pr.XYZPoint)
    if best is not None:
        MAN["notes"].append("%d hosted in nearest wall %d" % (item["ifc_id"], eid(best[1])))
        return best[1], DB.XYZ(best[2].X, best[2].Y, Z)
    fr = item.get("free")
    if fr:
        wt = ensure_wall_type("%s-%dmm" % (GENERIC_PREFIX, fr["W"]), fr["W"])
        wl = DB.Wall.Create(doc, DB.Line.CreateBound(XYZ(fr["p0"]), XYZ(fr["p1"])), wt.Id, level.Id,
                            fr["height"] * MM, fr.get("base_off", 0) * MM, False, False)
        doc.Regenerate()
        MAN["walls"]["host-for-%d" % item["ifc_id"]] = eid(wl)
        MAN["notes"].append("%d: created host wall %d (IFC element has no host wall)" % (item["ifc_id"], eid(wl)))
        return wl, XYZ(fr["pm"])
    return None, pt


def free_ends_if_filled(host, width_mm):
    """An opening that (nearly) fills its host wall - e.g. a slider spanning the whole gap between two cross walls -
    is refused ("Can't cut instance out of Wall") once Revit trims the host at its end joins. Disallow those joins
    first so the host keeps its full length."""
    lc = host.Location.Curve if isinstance(host.Location, DB.LocationCurve) else None
    if lc is None or width_mm * MM + 300 * MM < lc.Length:
        return False
    for end in (0, 1):
        if DB.WallUtils.IsWallJoinAllowedAtEnd(host, end):
            DB.WallUtils.DisallowWallJoinAtEnd(host, end)
    doc.Regenerate()
    MAN["notes"].append("host %d: end joins disallowed so a %d mm opening fits" % (eid(host), width_mm))
    return True


def drop_missing(key):
    for k in list(MAN[key].keys()):
        if doc.GetElement(DB.ElementId(MAN[key][k])) is None:
            MAN["failures"].append(["%s deleted by Revit" % key[:-1], k])
            del MAN[key][k]


out = []
if STEP == "walls":
    t, col = start("MCP: remodel %s walls from IFC" % nm(level))
    made = 0
    try:
        for w in B["walls"]:
            if str(w["ifc_id"]) in MAN["walls"]:
                continue
            if made >= LIMIT:
                break
            wt = ensure_wall_type(w["type"], w["W"])
            if w["kind"] == "line":
                crv = DB.Line.CreateBound(XYZ(w["p0"]), XYZ(w["p1"]))
            else:
                crv = DB.Arc.Create(XYZ(w["p0"]), XYZ(w["p1"]), XYZ(w["pm"]))
            wall = DB.Wall.Create(doc, crv, wt.Id, level.Id, w["height"] * MM, w["base_off"] * MM, False, False)
            MAN["walls"][str(w["ifc_id"])] = eid(wall)
            made += 1
        t.Commit()
    except Exception:
        t.RollBack()
        raise
    drop_missing("walls")
    MAN["failures"] += col.msgs
    out.append("walls created {} | in manifest {} | failure msgs {}".format(made, len(MAN["walls"]), len(col.msgs)))

elif STEP == "doors":
    walls_by_ifc = dict((k, doc.GetElement(DB.ElementId(v))) for k, v in MAN["walls"].items() if not k.startswith("host-for"))
    t, col = start("MCP: remodel %s doors from IFC" % nm(level))
    made = flips = 0
    try:
        for d in B["doors"]:
            if str(d["ifc_id"]) in MAN["doors"]:
                continue
            if made >= LIMIT:
                break
            if not d.get("family"):
                MAN["failures"].append(["door skipped: not planned (no host, no plan symbol)", d["ifc_id"]])
                continue
            host, pt = find_host(d, walls_by_ifc)
            if host is None:
                MAN["failures"].append(["door skipped: no host wall", d["ifc_id"]])
                continue
            free_ends_if_filled(host, max(d["tw"], d.get("W") or 0))
            sym = sized_symbol(DB.BuiltInCategory.OST_Doors, d["family"], d["tw"], d["th"])
            if not sym.IsActive:
                sym.Activate()
                doc.Regenerate()
            inst = doc.Create.NewFamilyInstance(pt, sym, host, level, DB.Structure.StructuralType.NonStructural)
            if abs(d.get("sill") or 0) > 5:
                p = inst.get_Parameter(DB.BuiltInParameter.INSTANCE_SILL_HEIGHT_PARAM)
                if p and not p.IsReadOnly:
                    p.Set(d["sill"] * MM)
            doc.Regenerate()
            if d.get("swing"):
                # compare the family's own plan swing arcs with the IFC swing side / hinge end
                arcs = plan_arcs(inst)
                if arcs:
                    sw = DB.XYZ(d["swing"][0], d["swing"][1], 0)
                    score = 0.0
                    for a in arcs:
                        far = max([a.GetEndPoint(0), a.GetEndPoint(1)], key=lambda q: abs((q - pt).DotProduct(sw)))
                        score += (far - pt).DotProduct(sw)
                    if score < 0:
                        inst.flipFacing()
                        flips += 1
                        doc.Regenerate()
                    if d.get("hinge") and len(arcs) == 1:
                        a = plan_arcs(inst)[0]
                        if (a.Center - pt).DotProduct(DB.XYZ(d["hinge"][0], d["hinge"][1], 0)) < 0:
                            inst.flipHand()
                            flips += 1
                            doc.Regenerate()
            elif d.get("exterior"):
                if inst.FacingOrientation.DotProduct(DB.XYZ(d["exterior"][0], d["exterior"][1], 0)) < 0:
                    inst.flipFacing()
                    flips += 1
            MAN["doors"][str(d["ifc_id"])] = eid(inst)
            made += 1
        t.Commit()
    except Exception:
        t.RollBack()
        raise
    drop_missing("doors")
    MAN["failures"] += col.msgs
    out.append("doors created {} | in manifest {} | flips {} | failure msgs {}".format(made, len(MAN["doors"]), flips, len(col.msgs)))

elif STEP == "windows":
    walls_by_ifc = dict((k, doc.GetElement(DB.ElementId(v))) for k, v in MAN["walls"].items() if not k.startswith("host-for"))
    t, col = start("MCP: remodel %s windows from IFC" % nm(level))
    made = flips = 0
    try:
        for w in B.get("windows", []):
            if str(w["ifc_id"]) in MAN["windows"] or not w.get("family"):
                continue
            if made >= LIMIT:
                break
            host, pt = find_host(w, walls_by_ifc)
            if host is None:
                MAN["failures"].append(["window skipped: no host wall", w["ifc_id"]])
                continue
            free_ends_if_filled(host, w["tw"])
            sym = sized_symbol(DB.BuiltInCategory.OST_Windows, w["family"], w["tw"], w["th"])
            if not sym.IsActive:
                sym.Activate()
                doc.Regenerate()
            inst = doc.Create.NewFamilyInstance(pt, sym, host, level, DB.Structure.StructuralType.NonStructural)
            p = inst.get_Parameter(DB.BuiltInParameter.INSTANCE_SILL_HEIGHT_PARAM)
            if p and not p.IsReadOnly:
                p.Set((w.get("sill") or 0) * MM)
            doc.Regenerate()
            if w.get("exterior"):
                sign = globals().get("WINDOW_FACING_IS_EXTERIOR", True)
                d = inst.FacingOrientation.DotProduct(DB.XYZ(w["exterior"][0], w["exterior"][1], 0))
                if (d < 0) == sign:
                    inst.flipFacing()
                    flips += 1
            MAN["windows"][str(w["ifc_id"])] = eid(inst)
            made += 1
        t.Commit()
    except Exception:
        t.RollBack()
        raise
    drop_missing("windows")
    MAN["failures"] += col.msgs
    out.append("windows created {} | in manifest {} | flips {} | failure msgs {}".format(made, len(MAN["windows"]), flips, len(col.msgs)))

elif STEP == "remove":
    ids = [DB.ElementId(v) for key in ("windows", "doors", "walls") for v in MAN[key].values()
           if doc.GetElement(DB.ElementId(v)) is not None]
    t, col = start("MCP: remove %s remodel elements (rebuild)" % nm(level))
    try:
        deleted = doc.Delete(List[DB.ElementId](ids)) if ids else []
        t.Commit()
    except Exception:
        t.RollBack()
        raise
    out.append("removed {} manifest elements ({} ids deleted incl. dependents)".format(len(ids), len(list(deleted))))
    MAN = {"level": nm(level), "walls": {}, "doors": {}, "windows": {}, "types_created": MAN.get("types_created", []),
           "failures": [], "notes": []}

if STEP in ("walls", "doors", "windows"):
    todo = [x for x in B[STEP] if str(x["ifc_id"]) not in MAN[STEP] and (STEP != "windows" or x.get("family"))]
    out.append("remaining to create in this step: {}".format(len(todo)))
f = open(MANIFEST, "w")
f.write(json.dumps(MAN, indent=1))
f.close()
from collections import Counter
c = Counter(m[1][:70] for m in MAN["failures"] if isinstance(m, list) and len(m) == 3)
out.append("failure summary: " + ("; ".join("{} x{}".format(k, v) for k, v in c.most_common(8)) or "none"))
if MAN.get("types_created"):
    out.append("types created so far: {}".format(", ".join(MAN["types_created"][-40:])))
print("\n".join(out))
