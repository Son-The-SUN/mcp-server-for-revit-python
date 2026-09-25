---
name: typical-floorplate-remodel
description: Remodel a typical floor plate from another architect's IFC (linked into Revit) as native Revit walls, doors and windows using the families already in the model, then group it per the GroupGSA Revit Execution Plan. Use this skill whenever the user wants to rebuild, remodel, trace, convert or "redraw in our model" floors from a linked IFC or consultant model - typical floors, tower floor plates, residential levels, "use the LEVEL 5 - TYPICAL FLOOR view to model" - even if they only say "remodel this IFC" or "model these floors from the link".
---

# Typical-Floorplate-Remodel

Rebuilds the floor plates shown in one or more host plan views from a linked IFC, as native Revit elements, then groups them following the `revit-group-strategy` skill. Use together with the `revit` skill (MCP tools, feet vs mm, IronPython 2.7, transactions).

The first job this was developed on: KSCW tower (IFC `AR-KSCW-SSDA-FP-01.ifc`, building A1), views `LEVEL 5 - TYPICAL FLOOR` and `LEVEL 10 - TYPICAL FLOOR`, Revit 2025. Numbers quoted below come from that job.

## The user's rules

These come straight from the user and apply to every remodel:

1. **Use the families and types already in the model.** Don't load or invent families unless the user asks.
2. **Walls:** use an existing type when one matches the IFC thickness and material. **Every wall type you create is a Generic type.** The template already has generic types: `GROUPGSA - GENERIC-100mm`, `-200mm`, `-300mm`, `GROUPGSA - GENERIC - 10mm GLASS`, `- 10mm MIRROR`. Use one of those if it fits. Otherwise duplicate the matching template generic and set its single layer to the IFC thickness: `GROUPGSA - GENERIC-<W>mm` (from `GENERIC-100mm`), or `GROUPGSA - GENERIC - <W>mm GLASS` (from `GENERIC - 10mm GLASS`). Never create new WT-numbered or other named constructions.
3. **Doors and windows:** if the width or height doesn't match an existing type, create a new type in the matching family, named `<W> x <H>` (mm).
4. **Group last.** Model everything ungrouped, show the user, then group, name and workset per the `revit-group-strategy` skill. Groups are hard to edit once made.
5. **Scripts live in this skill's `scripts/` folder**, data in the session scratchpad. Anything new you write for the job goes into `scripts/` too.
6. **The model is shared with the user in real time.** When they say they are working in the model (loading families, editing), stop all Revit calls until they say go.

Ask the user before anything outside these rules, e.g. enabling worksharing, or when there are no families at all for a category (on the first job there were no window families; the user chose to load theirs).

## Workflow

Scripts are in `scripts/revit/` (IronPython 2.7, run *inside* Revit) and `scripts/local/` (CPython 3, run on the workstation). Revit scripts are run through `execute_revit_code` by defining their inputs and calling `execfile`:

```python
VIEW_ID = 950844
LINK_VIEW_NAME = "LEVEL 5"
OUT = r"<scratchpad>\ifc_L5.json"
execfile(r"<this skill's folder>\scripts\revit\extract_view.py")
```

Local scripts read and write the JSON files in the current directory: run them with the scratchpad as the working directory, `python "<skill>/scripts/local/<script>.py" ...`.

1. **Survey.** `get_revit_status`; list levels, plan views (and their view templates), the link instance and its transform, wall/door/window types, and whether the model is workshared. Check the host levels match the IFC storey elevations.
2. **Extract** (Revit): `extract_view.py` once per view → `ifc_<LV>.json` (everything visible in that view: IFC properties, bbox, wall axis, plan section at the cut plane, door/window plan symbols). `extract_spaces.py` → `ifc_spaces.json` (unit/balcony/common-area outlines). `extract_all.py` → `ifc_all.json` (every wall/door/window of the whole IFC, for band detection).
3. **Understand** (local): `render_layers.py` draws the plan coloured by IFC layer, `render_spaces.py` overlays unit spaces, `typical_bands.py` shows which levels share each floor's layout per layer (this drives group level ranges), `diff_levels.py` lists what differs between two floors, `wall_stats.py` / `axis_stats.py` summarise wall kinds.
4. **Plan** (local): `plan_build.py <LV> --spaces <space level>` → `build_<LV>.json` (walls, doors, windows, screens, notes). Always render it with `render_plan.py` over the IFC outlines and look before building.
5. **Build** (Revit): `build_revit.py` with `STEP = "walls"`, then `"doors"`, then `"windows"`. Each step is one named transaction ("MCP: remodel LEVEL 5 walls from IFC") and records IFC id → Revit id in `manifest_<LV>.json`. `STEP = "remove"` deletes what a manifest created, for a clean rebuild.
6. **Verify:** `get_revit_view` on the view (the link shows through, so compare), count warnings, and re-render.
7. **Review with the user**, fix, then **group** (see `revit-group-strategy`).

## Walls - the method that works

The user confirmed the walls built this way were correct.

- **What to take:** walls visible in the view (Revit 2024+ `FilteredElementCollector(doc, viewId, linkInstanceId)`) whose base is within -800…+200 mm of the level and whose top is more than 300 mm above it. Don't trust `IfcSpatialContainer` for the level: elements are often contained in spaces (`A1-05.01`, `A1_2`) rather than storeys.
- **The IFC axis is the reference line, usually on one face.** Wall DirectShapes carry the IFC `Axis` representation as a PolyLine or Arc with graphics style `Axis`. For this (ArchiCAD-style) export it lay on one face of the wall for ~90% of walls and on the centreline for the rest. So never use it directly as the Revit centreline.
- **Centreline from the section outline:** project the wall's plan section (cut at the view's cut plane; top faces for walls below it) onto the axis normal to get `lo..hi`. If `hi - lo` equals `BaseQuantities.Width` (±30 mm), the centreline is the axis offset by `(lo + hi) / 2`. Otherwise keep the IFC width on the axis face, on the side where the outline lies, and note it.
- **Length from the outline**, not the axis: the min/max projection of the outline onto the axis direction is the visible extent, and Revit's automatic joins then clean the corners and tees.
- **Arcs:** same logic radially around the axis arc centre; angular extent from the outline.
- **Thickness** = `BaseQuantities.Width`. **Base offset and unconnected height** from the element's bounding box relative to the level (3250 floor-to-floor concrete, 3030 to the slab soffit, ~1200 balustrades, some starting -600 below the slab).
- **Type mapping** (by IFC material and width): concrete 150/200/300 → the existing `WT50/WT51/WT52 - CONCRETE`; glass or a handrail layer → `GROUPGSA - GENERIC - <W>mm GLASS`; anything else → `GROUPGSA - GENERIC-<W>mm`. The template's generic types are reused when the width matches (100/200/300, 10 mm glass). New ones are always generics, made by duplicating the template generic and `CompoundStructure.SetLayerWidth(0, W)`. On the first job that added `GENERIC-250/400/450/500mm` and `GENERIC - 50mm GLASS`. All of these are single-layer, so wall orientation doesn't matter.
- **Snap near-orthogonal walls.** After the link transform, "orthogonal" walls were 0.001°-0.09° off axis, which triggers "Wall is slightly off axis" warnings. Rotate lines within 0.2° of 0/90° to exact about their midpoint (ends move < 3 mm).
- **Resolve collinear overlaps before building.** Where two parallel walls overlap in thickness and length, trim or drop the lower-priority one (glass < generic < concrete, then thinner, then shorter). Otherwise Revit reports "Highlighted walls overlap".
- **Create:** `DB.Wall.Create(doc, curve, typeId, levelId, height, baseOffset, False, False)`; the API default location line is the wall centreline.
- **Failure handling:** use a failures preprocessor that records every message, resolves errors with their default resolution (`ResolveFailure`, e.g. "Can't keep elements joined" → unjoin) and deletes failing elements only when there is no resolution. The first run deleted on every error and silently lost 4 walls.

### IFC "walls" that are not walls

- **Screens:** IFC walls with no axis made of many small solids (6×10 mm rods, 50-100 mm posts, 1150-3030 high). They are open rod/post screens; building them as solid walls closes up the facade. Leave them out of the wall build and list them; offer the template's screen curtain wall types (`SCREEN - HILITE`, `SCREEN AERO VERTICAL`, ...) if the user wants them.
- **Wedge piers:** outlines much deeper than the IFC width (trapezoids where an orthogonal interior meets an angled facade). A native wall can't take that shape, and a 200 mm stand-in lands on top of the facade wall. Report them; they need a family if the user wants them.

## Doors (implemented, awaiting the user's review)

- **Host:** the Revit wall built from the door's `IfcContainedInHostGUID`. Doors with no host GUID: nearest built wall within half its thickness + 100 mm; otherwise a short host wall of the door's depth is created (noted in the manifest).
- **Position:** project the door's plan symbol curves onto the host centreline; the midpoint of their extent is the door centre (it matches the IFC width exactly).
- **Swing and hinge** from the IFC plan arcs: the open-leaf end of the arc gives the swing side, the arc centre the hinge end. After placing, read the Revit door's own plan arcs (`Options.View` = the plan view) and `flipFacing()` / `flipHand()` until they agree. This avoids relying on each family's facing convention: here `DOOR - SINGLE - STD` swings away from its facing side while `DOOR - DOUBLE - STD` swings toward it.
- **Family by IFC operation:** `Single_Swing_*` → `DOOR - SINGLE - STD`; `Double_Door_Single_Swing` → `DOOR - DOUBLE - STD`; `Double_Door_Sliding` (lift doors) → `DOOR - SLIDER 2P`; sliding facade doors (`UserDefined`) → `DOOR - SLIDER 2P` below 2900 wide, `3P` above.
- **Sizes:** swing doors use the leaf size (arc radius, IFC height − 50 mm head lining), because these families' `Width` is the leaf (opening = Width + 60 single / + 80 double). Sliders use the IFC overall size (opening = Width). Sliders face the side that is not inside a unit.
- Measure a family's conventions with a rolled-back test placement before trusting them.

## Windows

Pending: on the first job the model had no window families; the user loaded theirs. The planner records each IFC window's host, centre, sill, width, height and operation; the family/type mapping is set once the families are known.

## Grouping

Follow `revit-group-strategy`. Preparing it is local work and can run while the user reviews the model:

1. `classify_groups.py <LV> --spaces <key> [--overrides overrides_<LV>.json]`. This assigns every planned wall, door and window to Facade / Intertenancy (incl. corridor) / Core / Unit-nn and writes `groups_<LV>.json` plus a coloured `groups_<LV>.png` to check:
   - Facade comes from the IFC layer.
   - Walls not on facade layers: the unit spaces sampled on both sides decide (two units = Intertenancy; unit + nothing = corridor, i.e. Intertenancy; nothing on either side = Core; clusters of those walls form "core zones").
   - Openings follow their host wall.
   Fix the few misfits with an overrides file (`{"<ifc_id>": "Intertenancy"}`). On the first job these were a 200 mm concrete nib and the last corridor segment at the south end.
2. Unit outlines must be complete. A unit whose IFC space came back without an outline makes its corridor walls look like core. `extract_spaces.py` cuts a section 1 m above the level for exactly this reason.
3. `group_bands.py <LV> --ref-level "LEVEL 10"` gives, per category, the levels where that category repeats unchanged (every element present and nothing extra). Screens and duplicates left out of the build are ignored, and doors/windows get a looser position tolerance because their 3D leaves are exported at varying open angles. The band sets the `_Lxx-Lyy` part of each group name.
4. Categories that are identical between two modelled floors share one group type. Place it on both levels instead of grouping each floor's copy separately.

Job-specific points:

- Derive the level range of each group type from `group_bands.py`, not from the view names. On the first job "LEVEL 5 - TYPICAL FLOOR" turned out to be a one-off level, while Facade and Intertenancy were identical on L6-L16 and L18-L24 (L17 a variant).
- That SSDA-stage IFC had no internal unit partitions, so there were no Unit groups.
- The building code for names comes from the unit numbers in the IFC spaces (`A1-05.01` → `A1`).
- Ask whether one-off floors should be grouped anyway (the REP says to group only what's used 2+ times). The user chose to group Level 5 anyway.
