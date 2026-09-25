# -*- coding: utf-8 -*-
"""Create the REP model groups from the remodel manifests (see skills/revit-group-strategy).

Runs INSIDE Revit (IronPython 2.7) through execute_revit_code. Only after the user approved the ungrouped model.

    PLAN = r"<work>\\group_plan.json"      # written by scripts/local/group_plan.py
    STEP = "check"                         # "check" | "joins" | "group" | "place"
    # optional: ONLY = "A1-Core_L05-L24"   # restrict "group" / "place" to one group type
    execfile(r"<repo>\\skills\\typical-floorplate-remodel\\scripts\\revit\\group_revit.py")

group_plan.json:
  {"levels": {"L5": {"level_id": .., "manifest": "..", "groups": ".."}, "L10": {...}},
   "groups": [{"name": "A1-Core_L05-L24", "source": "L5", "category": "Core", "also_on": ["L10"]}, ...]}

Steps (each its own named transaction, safe to re-run):
  check  - dry run: member counts per group, level consistency, hosts inside the set, cross-group joins
  joins  - disallow wall joins at every wall end that touches a wall of another group category (REP 4.5)
  group  - doc.Create.NewGroup(members) per planned group, named; skips names that already exist
  place  - for groups with "also_on": delete that level's loose copies of the category and place an instance of
           the type there (copy by the level-to-level height, reference level set to the target level)
"""
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


P = json.loads(open(PLAN).read())
ONLY = globals().get("ONLY")
LV = {}
for key, cfg in P["levels"].items():
    man = json.loads(open(cfg["manifest"]).read())
    grp = json.loads(open(cfg["groups"]).read())
    cat_of = {}
    for kind in ("walls", "doors", "windows"):
        for ifc, rid in man[kind].items():
            c = grp[kind].get(ifc) or grp["walls"].get(ifc)
            if c:
                cat_of[rid] = c
    LV[key] = {"level": doc.GetElement(DB.ElementId(cfg["level_id"])), "cat_of": cat_of}


def members(key, category):
    ids = [DB.ElementId(r) for r, c in LV[key]["cat_of"].items() if c == category]
    return [i for i in ids if doc.GetElement(i) is not None]


def group_type(name):
    for gt in DB.FilteredElementCollector(doc).OfClass(DB.GroupType):
        if nm(gt) == name:
            return gt
    return None


out = []
if STEP == "check":
    for g in P["groups"]:
        ids = members(g["source"], g["category"])
        els = [doc.GetElement(i) for i in ids]
        lvls = set(eid(e.LevelId) for e in els if e.LevelId is not None and eid(e.LevelId) > 0)
        grouped = [eid(e) for e in els if eid(e.GroupId) > 0]
        idset = set(eid(i) for i in ids)
        orphans = [eid(e) for e in els if isinstance(e, DB.FamilyInstance) and e.Host is not None and eid(e.Host) not in idset]
        cross = 0
        for e in els:
            if isinstance(e, DB.Wall) and isinstance(e.Location, DB.LocationCurve):
                for end in (0, 1):
                    for j in e.Location.get_ElementsAtJoin(end):
                        if isinstance(j, DB.Wall) and eid(j) != eid(e) and LV[g["source"]]["cat_of"].get(eid(j), "?") != g["category"]:
                            cross += 1
        out.append("{:<28} {:>3} members | levels {} | already grouped {} | hosts outside {} | cross-group joins {} | type exists {}".format(
            g["name"], len(ids), sorted(lvls), len(grouped), orphans[:5], cross, group_type(g["name"]) is not None))

elif STEP == "joins":
    # Unjoining one wall's end lets Revit re-join the neighbour at ITS end, so repeat until a pass changes nothing
    # (first job: 71, then 21, then 1, then 0).
    t = DB.Transaction(doc, "MCP: disallow wall joins across REP groups")
    t.Start()
    passes = []
    try:
        for _ in range(6):
            n = 0
            for key in LV:
                cat_of = LV[key]["cat_of"]
                for rid, c in cat_of.items():
                    w = doc.GetElement(DB.ElementId(rid))
                    if not isinstance(w, DB.Wall) or not isinstance(w.Location, DB.LocationCurve):
                        continue
                    for end in (0, 1):
                        others = [j for j in w.Location.get_ElementsAtJoin(end) if isinstance(j, DB.Wall) and eid(j) != rid]
                        if any(cat_of.get(eid(j), "?") != c for j in others) and DB.WallUtils.IsWallJoinAllowedAtEnd(w, end):
                            DB.WallUtils.DisallowWallJoinAtEnd(w, end)
                            n += 1
            doc.Regenerate()
            passes.append(n)
            if n == 0:
                break
        t.Commit()
    except Exception:
        t.RollBack()
        raise
    out.append("wall ends unjoined across groups, per pass: {}".format(passes))

elif STEP == "group":
    for g in P["groups"]:
        if ONLY and g["name"] != ONLY:
            continue
        if group_type(g["name"]) is not None:
            out.append("{}: type exists, skipped".format(g["name"]))
            continue
        ids = members(g["source"], g["category"])
        if not ids:
            out.append("{}: no members, skipped".format(g["name"]))
            continue
        t = DB.Transaction(doc, "MCP: group {} on {}".format(g["name"], nm(LV[g["source"]]["level"])))
        t.Start()
        try:
            grp = doc.Create.NewGroup(List[DB.ElementId](ids))
            grp.GroupType.Name = g["name"]
            t.Commit()
            out.append("{}: group {} with {} members".format(g["name"], eid(grp), len(ids)))
        except Exception as ex:
            t.RollBack()
            out.append("{}: FAILED {}".format(g["name"], ex))

elif STEP == "place":
    for g in P["groups"]:
        if ONLY and g["name"] != ONLY:
            continue
        gt = group_type(g["name"])
        if gt is None or not g.get("also_on"):
            continue
        src_level = LV[g["source"]]["level"]
        inst = [x for x in gt.Groups if eid(x.LevelId) == eid(src_level)]
        if not inst:
            out.append("{}: no instance on {}".format(g["name"], nm(src_level)))
            continue
        for key in g["also_on"]:
            tgt = LV[key]["level"]
            if [x for x in gt.Groups if eid(x.LevelId) == eid(tgt)]:
                out.append("{}: already on {}".format(g["name"], nm(tgt)))
                continue
            loose = members(key, g["category"])
            t = DB.Transaction(doc, "MCP: place {} on {} (replaces loose copies)".format(g["name"], nm(tgt)))
            t.Start()
            try:
                if loose:
                    doc.Delete(List[DB.ElementId](loose))
                new_ids = DB.ElementTransformUtils.CopyElement(doc, inst[0].Id, DB.XYZ(0, 0, tgt.Elevation - src_level.Elevation))
                ng = doc.GetElement(list(new_ids)[0])
                pl = ng.get_Parameter(DB.BuiltInParameter.GROUP_LEVEL)
                po = ng.get_Parameter(DB.BuiltInParameter.GROUP_OFFSET_FROM_LEVEL)
                if pl is not None and not pl.IsReadOnly and eid(pl.AsElementId()) != eid(tgt):
                    pl.Set(tgt.Id)
                    if po is not None and not po.IsReadOnly:
                        po.Set(0.0)
                t.Commit()
                bb = ng.get_BoundingBox(None)
                out.append("{}: placed on {} (group {}, removed {} loose, level {}, bottom z {:.0f} mm)".format(
                    g["name"], nm(tgt), eid(ng), len(loose), nm(doc.GetElement(ng.LevelId)) if eid(ng.LevelId) > 0 else "-",
                    bb.Min.Z * 304.8 if bb else 0))
            except Exception as ex:
                t.RollBack()
                out.append("{}: FAILED on {}: {}".format(g["name"], nm(tgt), ex))
print("\n".join(out))
