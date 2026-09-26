# -*- coding: utf-8 -*-
"""Create or update levels (and their plan views) from the levels read off the DWG.

Runs INSIDE Revit (IronPython 2.7) through execute_revit_code:

    LEVELS = [
        {"name": "GROUND FLOOR", "elev_mm": 0,    "type": "FFL - TRIANGLE", "plan": True, "ceiling": True},
        {"name": "LEVEL 1",      "elev_mm": 3900, "type": "FFL - TRIANGLE", "plan": True, "ceiling": True},
        {"name": "ROOF",         "elev_mm": 6680, "type": "RL - TRIANGLE",  "plan": True},
        {"name": "RIDGE",        "elev_mm": 10630, "type": "RL - TRIANGLE", "plan": False},
    ]
    # optional: DRY_RUN = True     -> report only
    execfile(r"<repo>\\skills\\dwg-to-rvt-converter\\scripts\\revit\\create_levels.py")

- An existing level with the same name is moved/retyped; otherwise a new level is created.
- A level already at that elevation under another name is reported, never renamed (e.g. the template's
  AHD LEVEL): say so and let the user decide.
- Floor / ceiling plans are named like the level and use the first FloorPlan / CeilingPlan view family type.
One transaction: "MCP: levels from DWG".
"""
DRY_RUN = globals().get("DRY_RUN", False)
MM = 304.8


def nm(e):
    try:
        return e.Name
    except Exception:
        return DB.Element.Name.__get__(e)


def idv(eid):
    return getattr(eid, "Value", None) or eid.IntegerValue


levels = {nm(l): l for l in DB.FilteredElementCollector(doc).OfClass(DB.Level)}
ltypes = {nm(t): t for t in DB.FilteredElementCollector(doc).OfClass(DB.LevelType)}
vfts = list(DB.FilteredElementCollector(doc).OfClass(DB.ViewFamilyType))
vft_plan = [v for v in vfts if v.ViewFamily == DB.ViewFamily.FloorPlan]
vft_rcp = [v for v in vfts if v.ViewFamily == DB.ViewFamily.CeilingPlan]
views = [v for v in DB.FilteredElementCollector(doc).OfClass(DB.ViewPlan) if not v.IsTemplate]


def plan_for(level, kind):
    for v in views:
        if v.GenLevel and v.GenLevel.Id == level.Id and v.ViewType == kind:
            return v
    return None


report = []
t = DB.Transaction(doc, "MCP: levels from DWG")
if not DRY_RUN:
    t.Start()
try:
    for spec in LEVELS:
        name, elev = spec["name"], spec["elev_mm"] / MM
        same_height = [n for n, l in levels.items() if n != name and abs(l.Elevation - elev) < 1e-4]
        lv = levels.get(name)
        if lv is None:
            action = "create"
            if not DRY_RUN:
                lv = DB.Level.Create(doc, elev)
                lv.Name = name
                levels[name] = lv
        else:
            action = "keep" if abs(lv.Elevation - elev) < 1e-4 else "move {:.0f} -> {:.0f}".format(lv.Elevation * MM, elev * MM)
            if not DRY_RUN and action.startswith("move"):
                lv.Elevation = elev
        if lv is not None and spec.get("type") and not DRY_RUN:
            lt = ltypes.get(spec["type"])
            if lt is None:
                report.append("  level type '{}' not found - kept {}".format(spec["type"], nm(doc.GetElement(lv.GetTypeId()))))
            elif lv.GetTypeId() != lt.Id:
                lv.ChangeTypeId(lt.Id)
        made = []
        if lv is not None and not DRY_RUN:
            if spec.get("plan") and vft_plan and plan_for(lv, DB.ViewType.FloorPlan) is None:
                vp = DB.ViewPlan.Create(doc, vft_plan[0].Id, lv.Id)
                try:
                    vp.Name = name
                except Exception:
                    pass
                made.append("plan {}".format(idv(vp.Id)))
            if spec.get("ceiling") and vft_rcp and plan_for(lv, DB.ViewType.CeilingPlan) is None:
                vc = DB.ViewPlan.Create(doc, vft_rcp[0].Id, lv.Id)
                try:
                    vc.Name = name
                except Exception:
                    pass
                made.append("ceiling {}".format(idv(vc.Id)))
        report.append("{:<14} {:>7.0f} mm  {:<12} id={} {}{}".format(
            name, float(spec["elev_mm"]), action, idv(lv.Id) if lv is not None else "-", ", ".join(made),
            ("  (same height as: " + ", ".join(same_height) + ")") if same_height else ""))
    if not DRY_RUN:
        t.Commit()
except Exception:
    if not DRY_RUN:
        t.RollBack()
    raise
print("\n".join(report))
