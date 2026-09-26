# -*- coding: utf-8 -*-
"""Export a 3D picture of the remodel: the IFC link hidden, optionally coloured by REP group - without changing the model.

Runs INSIDE Revit (IronPython 2.7) through execute_revit_code:

    VIEW_NAME = "{3D}"                     # an existing 3D view
    HIDE_IDS = [950744]                    # element ids to hide, e.g. the IFC link instance
    OUT = r"<scratchpad>\\remodel_3d"      # file path prefix; Revit appends " - 3D View - <view>.png"
    # optional: COLOR_BY_GROUP = True      # colour group members: Facade blue, Intertenancy orange, Core red, Unit green
    # optional: PIXELS = 2400
    execfile(r"<repo>\\skills\\typical-floorplate-remodel\\scripts\\revit\\view_3d.py")

The hiding and colour overrides are made inside a TransactionGroup that is rolled back after the export, so the view
and the document are left exactly as they were (a saved model stays unmodified).
"""
import os
import glob
from System.Collections.Generic import List

PIXELS = globals().get("PIXELS", 2400)
COLOR_BY_GROUP = globals().get("COLOR_BY_GROUP", False)
COLORS = [("-Facade", (70, 130, 200)), ("-Intertenancy", (240, 165, 50)), ("-Core", (205, 70, 70)),
          ("-Unit", (90, 170, 90))]


def nm(e):
    try:
        return e.Name
    except Exception:
        return DB.Element.Name.__get__(e)


view = [v for v in DB.FilteredElementCollector(doc).OfClass(DB.View3D) if not v.IsTemplate and nm(v) == VIEW_NAME][0]
was_modified = doc.IsModified
folder = os.path.dirname(OUT)
before = set(glob.glob(os.path.join(folder, "*.png")))

tg = DB.TransactionGroup(doc, "MCP: 3D picture (rolled back)")
tg.Start()
try:
    t = DB.Transaction(doc, "MCP: prepare 3D picture")
    t.Start()
    hide = [DB.ElementId(i) for i in HIDE_IDS if doc.GetElement(DB.ElementId(i)) is not None]
    hide = [i for i in hide if not doc.GetElement(i).IsHidden(view)]
    if hide:
        view.HideElements(List[DB.ElementId](hide))
    counts = {}
    if COLOR_BY_GROUP:
        solid = [f for f in DB.FilteredElementCollector(doc).OfClass(DB.FillPatternElement)
                 if f.GetFillPattern().IsSolidFill][0]
        for g in DB.FilteredElementCollector(doc).OfClass(DB.Group):
            tname = nm(g.GroupType)
            rgb = next((c for key, c in COLORS if key in tname), None)
            if rgb is None:
                continue
            ogs = DB.OverrideGraphicSettings()
            color = DB.Color(*rgb)
            ogs.SetSurfaceForegroundPatternId(solid.Id)
            ogs.SetSurfaceForegroundPatternColor(color)
            ogs.SetCutForegroundPatternId(solid.Id)
            ogs.SetCutForegroundPatternColor(color)
            for m in g.GetMemberIds():
                view.SetElementOverrides(m, ogs)
            counts[tname] = counts.get(tname, 0) + g.GetMemberIds().Count
    t.Commit()

    opts = DB.ImageExportOptions()
    opts.ExportRange = DB.ExportRange.SetOfViews
    opts.SetViewsAndSheets(List[DB.ElementId]([view.Id]))
    opts.FilePath = OUT
    opts.ZoomType = DB.ZoomFitType.FitToPage
    opts.FitDirection = DB.FitDirectionType.Horizontal
    opts.PixelSize = PIXELS
    opts.ImageResolution = DB.ImageResolution.DPI_150
    opts.HLRandWFViewsFileType = DB.ImageFileType.PNG
    opts.ShadowViewsFileType = DB.ImageFileType.PNG
    doc.ExportImage(opts)
finally:
    tg.RollBack()

new = sorted(set(glob.glob(os.path.join(folder, "*.png"))) - before, key=os.path.getmtime)
print("exported: {}".format(new[-1] if new else "nothing new (file overwritten?)"))
if counts:
    print("coloured members: {}".format(counts))
print("document modified: before {} / after {}".format(was_modified, doc.IsModified))
