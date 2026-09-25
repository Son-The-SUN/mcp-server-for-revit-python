---
name: revit-group-strategy
description: GroupGSA Revit Execution Plan (REP) rules for Revit model groups on repetitive floor plates - which elements belong in the Facade, Intertenancy + Corridor, Unit and Core groups, how to name group types (<Building>-<Group>_L05-L09), which workset each group goes on, and when to group (only at the very end of a remodel, after the user has approved the ungrouped model). Use this skill whenever you are about to create, name, copy or re-workset Revit groups, when modelling or remodelling a typical floor, tower floor plate or repeated levels, or when the user mentions groups, group naming, typical floors, worksets for groups or the Revit Execution Plan - even if they never say "group".
---

# Revit group strategy (GroupGSA Revit Execution Plan, section 4)

Source: the project's *Revit Execution Plan* (REP), section 4 "Revit group strategy" and section 3 "Worksets". A copy of the template lives next to the `revit` skill (`skills/revit/Revit Execution Plan.docx`). If the project has its own filled-in REP, its values win over the defaults below.

Use this together with the `revit` skill, which covers the MCP tools, units (feet), IronPython 2.7 and transactions.

## Rule 1: group last

Create groups only at the very end of a modelling or remodelling job, after the user has seen the ungrouped result and said they are happy with it.

Why: once elements are in a group, every change has to go through Revit's group edit mode and propagates to every instance. The Revit API has no group edit mode, so an agent can only change a group by ungrouping it (which the REP forbids) or by deleting and rebuilding it. A mistake that is a one-line fix on ungrouped walls becomes a rebuild once they are grouped. So the order is always:

1. Model every element ungrouped, keeping a record of which group each element is meant for (see "Keep a manifest while modelling").
2. Show the user the result (plan images, counts, a list of assumptions) and fix what they point out.
3. Only after an explicit go-ahead: create the groups, name them, repeat them on other levels and put them on worksets.

If the user asks for a change after grouping, don't ungroup. Offer two options: they make the edit in Revit's group edit mode, or you rebuild that one group type (delete its instances and type, fix the loose elements you recreate, regroup). Rebuilding is destructive, so confirm first.

## Rule 2: decide whether to group at all

- **Repetitive floor plates** (residential towers, typical floors copied level to level): use groups. They keep the file performant, make edits propagate predictably, and let the model be split into separate files later.
- **Non-repetitive projects**: don't group unless it's strictly necessary. Groups slow the model and complicate daily work.
- **Only group something that will be placed two or more times** - on several levels, or as repeated identical units on one level. A group used once only adds overhead. When the model does not yet contain all the levels of a typical band (for example only LEVEL 5 exists, but the layout repeats up to LEVEL 9), the group still counts as repeated; say so in your summary.

## The four group categories (REP 4.1)

Each floor plate is split into these categories. Each category is modelled and grouped on its own, per level.

| # | Group | What goes in it | Behaviour across levels | Workset |
|---|---|---|---|---|
| 1 | **Facade** | Facade/external walls, the doors and windows hosted in them, balconies, balustrades, and any other exterior-facing element | Consistent through the levels. Sets the outer envelope every other wall must respect | `11_EXTERIOR` |
| 2 | **Intertenancy + Corridor** | Party walls between units, corridor walls, and the corridor-side door leaves (unit entry doors) | May vary level to level, but always aligns to the facade module | `12_INTERIOR` |
| 3 | **Unit** (internal unit walls) | Partitions inside one unit, their doors, floor finishes, joinery, FF&E of that unit | Follows the unit layout; one group type per distinct unit layout | `12_INTERIOR` |
| 4 | **Core** | Lift, fire stair and services riser/shaft walls, and the doors that belong to the core | Unchanged between levels; structural priority | `12_STRUCTURE` |

The REP text says "five categories" but its table lists four (the fifth row is blank). Anything that fits none of them - stairs, structural columns, slabs, ceilings - stays ungrouped unless the user decides otherwise. Mention what you left out.

How to decide which group an element belongs to:

- **Hosted elements go with their host.** A door or window belongs to the group of the wall it is cut into. A group must contain the host of every hosted element in it, otherwise the element loses its host when the group is copied.
- **Perimeter wall** → Facade. **Wall between two units** → Intertenancy. **Wall between a unit and the corridor** → Corridor (same group as intertenancy). **Wall around lifts, fire stairs, risers** → Core; the core wins where it touches a unit or the corridor.
- **Wall entirely inside one unit** → that unit's group.
- When one straight wall runs past several of these conditions, split it at the boundary so each piece belongs to exactly one group.

## Group naming (REP 4.2)

```
<Building>-<Group Name>_<Start Level>-<End Level>
```

REP examples: `G-Unit_L05-L18` (typical residential band), `G-Facade_L05-L18`, `G-Core_L01-Roof`, `G-Intertenancy_L05-L10` (when intertenancy changes mid-tower).

- **Building**: the building code. Look for it in the project information, the unit numbers (`A1-05.01` means building A1, level 05, unit 01), or the source model. If there is no clear code, ask the user rather than inventing one.
- **Group Name**: `Facade`, `Intertenancy`, `Core`, or `Unit-<id>` for units, where `<id>` is the unit's stack number or unit type (`Unit-01`, `Unit-2B`). Mirrored units get their own types: `Unit-2B-LH` / `Unit-2B-RH`.
- **Level range**: the first and last level of the band this group type is used on, as `L` plus two digits (`L05`); use `GF`, `Roof` and similar for named levels. It describes the typical band the layout repeats over, which can be larger than the levels that exist in the model today - take it from the source drawings or model and state where it came from.
- If the same category differs between two bands (for example the Level 5 and Level 10 typical floors), each band gets its own type with its own range: `A1-Intertenancy_L05-L09`, `A1-Intertenancy_L10-L28`. If it is identical across both, use one type spanning both bands and place it on every level.
- Group type names must be unique in the model; check existing group types before naming.

## Good group practice (REP 4.5)

- **One level per group.** Every element in a group belongs to the same level (walls' base constraint, doors' and windows' level). Mixed-level groups break floor-to-floor copying and schedules.
- **Never ungroup**, including `Group.UngroupMembers()` through the API. If Revit offers "Fix Group" or "Create New Group Type", accept the new type and then update every instance that was meant to change; leaving some floors on the old type is the most common source of drift.
- **No wall joins across groups.** A unit wall must not run through an intertenancy wall, and an intertenancy wall must not run through a core wall. Model internal walls to the face of the boundary wall, not its centreline, and disallow joins at wall ends that touch another group's walls. If joins appear across groups, the group boundaries are wrong: fix the geometry rather than forcing the join.
- **No nested groups.**
- **Avoid attaching detail groups** to model groups.
- **Mirroring**: create LH/RH group types instead of mirroring instances; hatch patterns can display wrongly in mirrored groups.
- **Model, don't fake**: modelled elements are preferred over filled regions, which can hide unresolved design.

## Worksets (REP 3.0 and 4.3)

Put each group instance **and its member elements** on the group's workset (table above), so visibility and selective loading behave the same whichever you pick.

The REP workset list:

`10_MASSING`, `11_EXTERIOR`, `12_INTERIOR`, `12_STRUCTURE`, `13_PARKING`, `15_SERVICES`, `16_FURNITURE & EQUIPMENT`, `17_STORAGE`, `30_LANDSCAPE`, `31_PLANTING`, `50_CONTROL_MODEL`, `51_LINKS_AR`, `51_LINKS_EL`, `51_LINKS_HY`, `51_LINKS_ID`, `51_LINKS_ME`, `51_LINKS_STR`, `52_CAD CONTAINERS`, `80_SHARED LEVELS AND GRIDS`, `81_SURVEY`, `82_TOPO`, `83_HEIGHT BLANKET`, `84_SITE BOUNDARY`, `89_CONTEXT`, `90_ENSCAPE - RENDERING`

Links go on the `51_LINKS_*` workset for their discipline, levels and grids on `80_SHARED LEVELS AND GRIDS`.

If the model is **not workshared** (`doc.IsWorkshared` is False), enabling worksharing is a one-way change: the file becomes a central model when it is next saved. Ask the user before enabling it, and don't save or sync on their behalf.

Versioning: before a major typical-floor revision the user can save a group out as its own file (Edit Group → Save Group) to keep an archived copy. There is no API for this; suggest it rather than doing it.

## File separation (REP 4.4)

Clean group boundaries let a large model be split later: save each group as a standalone file and link it back on the same workset structure. Facade, Core and Corridor are the usual first candidates because they are the most stable and the most referenced by other disciplines. Keep this in mind when deciding group boundaries.

## Doing it through the API

Everything below runs in `execute_revit_code` (IronPython 2.7). Use one named transaction per group so the user can undo each separately.

### Keep a manifest while modelling

Grouping happens at the end, possibly in a later session, so record what you create as you go: for each level and group category, the element IDs (and for units, the unit id). A JSON file in the scratchpad works; so does a text parameter such as `Comments` if the user is fine with that. Re-read the model before grouping - the user may have edited it in the meantime - and drop IDs that no longer exist.

A tested implementation lives in the `typical-floorplate-remodel` skill. `scripts/local/group_plan.py` turns a per-element category map plus level bands into names and sharing. `scripts/revit/group_revit.py` does `check` → `joins` → `group` → `place`. Reuse them rather than rewriting.

### Pre-group checks

For every intended group, before creating it:

- all element IDs exist, are not already in a group (`el.GroupId` is invalid), and are on the same level;
- every door and window in the set has its host wall in the set (`inst.Host.Id`);
- no wall end in the set is joined to a wall of another group. Find the joins with `wall.Location.get_ElementsAtJoin(end)` and remove them with `DB.WallUtils.DisallowWallJoinAtEnd(wall, end)`. **Repeat until a pass changes nothing:** unjoining one wall's end lets Revit re-join the neighbour at *its* end (first job: 71, then 21, then 1, then 0 ends);
- the type name you are about to use is unique.

Show the user the plan (names, member counts, what gets shared or replaced) and get a go-ahead before the first `NewGroup`.

### Create, name and workset one group

```python
from System.Collections.Generic import List

def set_workset(el, ws_id):
    p = el.get_Parameter(DB.BuiltInParameter.ELEM_PARTITION_PARAM)
    if p and not p.IsReadOnly:
        p.Set(ws_id.IntegerValue)

ws = [w for w in DB.FilteredWorksetCollector(doc).OfKind(DB.WorksetKind.UserWorkset)
      if w.Name == "12_STRUCTURE"][0]
ids = [DB.ElementId(i) for i in core_ids_level5]

t = DB.Transaction(doc, "MCP: group A1-Core_L05-L28 on LEVEL 5")
t.Start()
try:
    for i in ids:                         # members first, then the instance
        set_workset(doc.GetElement(i), ws.Id)
    grp = doc.Create.NewGroup(List[DB.ElementId](ids))
    grp.GroupType.Name = "A1-Core_L05-L28"
    set_workset(grp, ws.Id)
    t.Commit()
except Exception:
    t.RollBack()
    raise
```

### Repeat a group type on another level

If a category is identical on another level, don't model it twice and group it twice: place another instance of the same type. If identical loose copies already exist on that level (for example because they were modelled for review), delete them in the same transaction and tell the user.

Copy the instance by the level-to-level height. The copy still references the source level with an offset, so set its reference level to the target level and the offset to 0. Tested on Revit 2025: the group stays in place and its member walls then report the target level as their base constraint, so this is not a mixed-level group.

```python
dz = level10.Elevation - level5.Elevation
new = doc.GetElement(list(DB.ElementTransformUtils.CopyElement(doc, grp.Id, DB.XYZ(0, 0, dz)))[0])
new.get_Parameter(DB.BuiltInParameter.GROUP_LEVEL).Set(level10.Id)
new.get_Parameter(DB.BuiltInParameter.GROUP_OFFSET_FROM_LEVEL).Set(0.0)
```

### Naming a one-level group

A type used on a single level (a one-off floor the user still wants grouped) is named with that level alone: `A1-Facade_L05`.

### When the model is not workshared

Ask before enabling worksharing. If the user declines, group and name as usual, skip the workset step, and list in the report which workset each group type should go on later.

### Verify

After grouping, report every group type with its instance count, the levels its instances sit on and its workset. Also confirm no remodelled wall/door/window was left ungrouped (`el.GroupId` invalid), and check `doc.GetWarnings()` for new warnings (duplicate instances, joins across groups, hosted elements without hosts).
