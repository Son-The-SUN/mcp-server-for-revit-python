# -*- coding: utf-8 -*-
"""Import and align CAD, step 2: import a 1:1 DXF/DWG (from extract_view.py) into a Revit view and align it.

Runs INSIDE Revit (IronPython 2.7) through execute_revit_code:

    FILE = r"<scratchpad>\\views\\GF.dxf"
    VIEW_NAME = "GROUND FLOOR"          # target view (floor plan, elevation, section, drafting...)
    # optional:
    # VIEW_TYPE = "FloorPlan"           # disambiguate a name used by several views
    # THIS_VIEW_ONLY = True             # plans: current view only (default True)
    # COLOR = "black"                   # "black" (black & white) | "preserve" | "invert"
    # MOVE_MM = (0, 0, 0)               # move after import (model x, y, z in mm)
    # ALIGN = {"dwg": (x, y), "model": (X, Y, Z)}    # move so DWG point (mm, in the file) lands on a model point (mm)
    # ROTATE_DEG = 0                    # about the DWG origin (plans only), after the move
    # REPLACE = True                    # delete this file's earlier imports in that view first
    # PIN = True
    # HIDE_LAYERS = ["DEFPOINTS", "KHUNG GIẤY"]      # layer names switched off in the view (import subcategories)
    execfile(r"<repo>\\skills\\dwg-to-rvt-converter\\scripts\\revit\\import_cad.py")

Placement is origin-to-origin in millimetres, so every plan cut with the same extract_view.py origin lands on
the same spot. In an elevation or section the file's X runs along the view (left to right) and Y up; the DWG
origin (±0.000 datum at the left of the drawing) is then moved with ALIGN / MOVE_MM.
Prints the import id and the model-space box of the import (mm). One transaction "MCP: import CAD <file> -> <view>".
"""
import os
import clr

THIS_VIEW_ONLY = globals().get("THIS_VIEW_ONLY", True)
COLOR = globals().get("COLOR", "black")
MOVE_MM = globals().get("MOVE_MM", None)
ALIGN = globals().get("ALIGN", None)
ROTATE_DEG = globals().get("ROTATE_DEG", 0)
REPLACE = globals().get("REPLACE", True)
PIN = globals().get("PIN", True)
HIDE_LAYERS = globals().get("HIDE_LAYERS", [])
VIEW_TYPE = globals().get("VIEW_TYPE", None)
MM = 304.8


def nm(e):
    try:
        return e.Name
    except Exception:
        return DB.Element.Name.__get__(e)


def idv(eid):
    return getattr(eid, "Value", None) or eid.IntegerValue


def xyz_mm(p):
    return "({:.0f}, {:.0f}, {:.0f})".format(p.X * MM, p.Y * MM, p.Z * MM)


cands = [v for v in DB.FilteredElementCollector(doc).OfClass(DB.View) if not v.IsTemplate and nm(v) == VIEW_NAME]
if VIEW_TYPE:
    cands = [v for v in cands if str(v.ViewType) == VIEW_TYPE]
if not cands:
    raise Exception("view '{}' not found".format(VIEW_NAME))
if len(cands) > 1:
    cands.sort(key=lambda v: 0 if v.ViewType == DB.ViewType.FloorPlan else 1)
view = cands[0]
fname = os.path.basename(FILE)

opts = DB.DWGImportOptions()
opts.Unit = DB.ImportUnit.Millimeter
opts.Placement = DB.ImportPlacement.Origin
opts.ThisViewOnly = bool(THIS_VIEW_ONLY) or view.ViewType not in (DB.ViewType.FloorPlan, DB.ViewType.CeilingPlan, DB.ViewType.ThreeD)
opts.ColorMode = {"black": DB.ImportColorMode.BlackAndWhite, "preserve": DB.ImportColorMode.Preserved,
                  "invert": DB.ImportColorMode.Inverted}[COLOR]
opts.VisibleLayersOnly = False
try:
    opts.AutoCorrectAlmostVHLines = True
except Exception:
    pass

t = DB.Transaction(doc, "MCP: import CAD {} -> {}".format(fname, VIEW_NAME))
t.Start()
try:
    removed = 0
    if REPLACE:
        for ii in DB.FilteredElementCollector(doc).OfClass(DB.ImportInstance):
            typ = doc.GetElement(ii.GetTypeId())
            if typ is not None and nm(typ).lower() == fname.lower() and (not ii.ViewSpecific or ii.OwnerViewId == view.Id):
                if ii.Pinned:
                    ii.Pinned = False
                doc.Delete(ii.Id)
                removed += 1
    ref = clr.Reference[DB.ElementId]()
    ok = doc.Import(FILE, opts, view, ref)
    if not ok:
        # elevations / sections / drafting views refuse origin-to-origin: place centred, ALIGN moves it after
        opts.Placement = DB.ImportPlacement.Centered
        ok = doc.Import(FILE, opts, view, ref)
    if not ok:
        raise Exception("Import failed for " + FILE)
    inst = doc.GetElement(ref.Value)
    if inst.Pinned:
        inst.Pinned = False            # Revit pins origin-placed imports; unpin to move, PIN re-pins below
    tr0 = inst.GetTotalTransform()
    move = DB.XYZ(0, 0, 0)
    if ALIGN:
        # DWG point (file mm) -> model point: through the instance transform
        dx, dy = ALIGN["dwg"][0] / MM, ALIGN["dwg"][1] / MM
        src = tr0.OfPoint(DB.XYZ(dx, dy, 0))
        X, Y, Z = [None if c is None else c / MM for c in ALIGN["model"]]   # None: keep that coordinate
        tgt = DB.XYZ(X if X is not None else src.X, Y if Y is not None else src.Y, Z if Z is not None else src.Z)
        move = tgt - src
    if MOVE_MM:
        move = move + DB.XYZ(MOVE_MM[0] / MM, MOVE_MM[1] / MM, MOVE_MM[2] / MM)
    if not move.IsZeroLength():
        DB.ElementTransformUtils.MoveElement(doc, inst.Id, move)
    if ROTATE_DEG:
        o = inst.GetTotalTransform().Origin
        axis = DB.Line.CreateBound(o, o + DB.XYZ.BasisZ)
        import math
        DB.ElementTransformUtils.RotateElement(doc, inst.Id, axis, math.radians(ROTATE_DEG))
    hidden = []
    if HIDE_LAYERS:
        cat = inst.Category
        for sub in cat.SubCategories:
            if sub.Name in HIDE_LAYERS and view.CanCategoryBeHidden(sub.Id):
                view.SetCategoryHidden(sub.Id, True)
                hidden.append(sub.Name)
    if PIN:
        inst.Pinned = True
    t.Commit()
except Exception:
    t.RollBack()
    raise

tr = inst.GetTotalTransform()
bb = inst.get_BoundingBox(view if inst.ViewSpecific else None)
layers = sorted(s.Name for s in inst.Category.SubCategories)
print("import id={} in '{}' ({}) view-specific={} replaced={}".format(idv(inst.Id), VIEW_NAME, view.ViewType, inst.ViewSpecific, removed))
print("DWG origin at {} mm, X axis ({:.3f}, {:.3f}, {:.3f})".format(xyz_mm(tr.Origin), tr.BasisX.X, tr.BasisX.Y, tr.BasisX.Z))
if bb:
    print("box {} - {} mm".format(xyz_mm(bb.Min), xyz_mm(bb.Max)))
print("{} layers{}".format(len(layers), (", hidden: " + ", ".join(hidden)) if hidden else ""))
