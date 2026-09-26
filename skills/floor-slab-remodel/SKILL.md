---
name: floor-slab-remodel
description: Model the floor slab (floor plate) of a level as a native Revit floor from another architect's IFC linked into Revit, following the user's own example slab (type, thickness, offset below FFL, which voids go in the slab). Use this skill whenever the user wants to model, remodel, trace or rebuild floor slabs, floor plates, structural slabs or "the floor" of typical levels from a linked IFC or consultant model - even if they only say "model the slab from the link" or "look at the example and do the floor plate" - and together with typical-floorplate-remodel when a whole floor is being rebuilt.
---

# Floor-slab-remodel

Builds one native floor per level from the slab in a linked IFC, the way the user models slabs in their own example
files. Use together with the `revit` skill (MCP tools, feet vs mm, IronPython 2.7, transactions) and, for the walls,
doors and windows of the same floors, `typical-floorplate-remodel`. Group the slabs last, per `revit-group-strategy`.

First job: KSCW tower (IFC `AR-KSCW-SSDA-FP-01.ifc`, building A1), LEVEL 5 and LEVEL 10, Revit 2025. The numbers
below come from that job. Records: `<model folder>\remodel_records\floor_slab\`.

## The user's rules

1. **Follow the user's example slab.** The example files are the user's own saved groups
   (`group_examples\A1-FLOOR SLAB_LEVEL 5.rvt`, `A1-FLOOR SLAB_LEVEL 10-LEVEL20.rvt`). Read them first
   (`inspect_example.py`). On the first job they held one floor per level: `CONCRETE 220MM`, structural,
   `Height Offset From Level` -20 (top of slab 20 below FFL), filleted outer edge, and a single void for the stair
   flights. No lift, riser or wall holes.
2. **Leave the setdown families out while the units are not set out.** The examples also carry
   `Building A Setdown Void*` / `Setdown_Void_Shower*` generic models (20/40 mm, hosted on the slab). Don't place
   them until the user says the internal units are set out.
3. **Use the types in the model.** If the example's floor type is missing, duplicate the model's matching type
   (`CONCRETE 200MM`) and change only the structure thickness (`CONCRETE 220MM`). Say which material it kept.
4. Scripts live in this skill's `scripts/`, data in the session scratchpad, records next to the model
   (see `revit` skill and the remodel skill's record section). Group last; ask before grouping.
5. **The slab is its own REP group category, "Floor Slab"** (REP 4.1 row 6, added 2026-09-26; see
   `revit-group-strategy`). Its group holds the concrete floor plate plus everything that cuts a void or setdown
   into it: 3D setdowns, cutouts, downpipe penetration holes. Finish floors stay out. Name:
   `<Building>-Floor Slab_<Lxx>-<Lyy>` (REP example `G-Floor Slab-L10-L15`; see the separator note in
   `revit-group-strategy`).

## Workflow

Revit scripts (`scripts/revit/`, IronPython) are run through `execute_revit_code` by setting their inputs and calling
`execfile`; local scripts (`scripts/local/`, CPython, no third-party packages) run in the scratchpad.

1. **Survey:** `get_revit_status`, levels, floor types (`CONCRETE ...` layers and materials), existing floors, the
   IFC link instance, plan views.
2. **Example** (Revit): `inspect_example.py` with `FILES = {"L5": r"...\A1-FLOOR SLAB_LEVEL 5.rvt", ...}` →
   `example_slabs.json`. Opens each file in the background (`Application.OpenDocumentFile`) and closes it
   without saving; the user's model is untouched. Records floors (type, layers, offset, structural, sketch loops),
   generic models (setdowns), openings, slab edges.
3. **Extract** (Revit, read-only): `extract_slabs.py` with `LEVELS = {"L5": <level id>, "L10": <level id>}` →
   `ifc_slabs.json`. See "How the IFC slab is read".
4. **Look and compare** (local):
   - `render_slabs.py L10 slabs_L10.png [--ids]` draws the IFC up-faces, the merged slab, the fills and the holes.
   - `compare_example.py L10 L10 compare_L10.png --outline slab_only` fits the example onto the IFC slab (8 right
     angle rotations/mirrors, bbox-centred, refined shift, scored by raster XOR). On the first job it fitted at
     270° with a shift equal to the link's origin and 15 m² mismatch: the example is the same building's slab, so
     it is a reliable guide for edges and voids.
5. **Plan** (local): `plan_slab.py L10 --example example_slabs.json:L10 [--config slab_config.json]` →
   `slab_L10.json`. Then `render_slabs.py L10 plan_L10.png --plan` and
   `compare_example.py L10 L10 cmpplan_L10.png --outline plan`. Look at both before building.
6. **Build** (Revit): `build_slab.py` with `PLAN`, `MANIFEST`, `STEP = "check"` (validates the loops with
   `BoundaryValidation`) then `"build"`. One transaction per level ("MCP: floor slab LEVEL 10 from IFC").
   `"rebuild"` deletes the recorded floor first, `"remove"` only deletes it.
7. **Verify:** floor parameters (type, thickness, offset, elevations at top/bottom, structural, area),
   `doc.GetWarnings()` on the new floors, a 3D picture with the link hidden (`typical-floorplate-remodel`
   `scripts/revit/view_3d.py`, rolled back).
8. **Report and record** (below), then ask whether to save the model.

## How the IFC slab is read (`extract_slabs.py`)

- **Candidates:** every element of the scanned categories (`Floors`, `Roofs`, `Generic Models`) with an up-facing
  face within `ZWIN` (-700…+150 mm) of the level. Solids give planar faces with true arcs. Typical floors in this
  IFC were **meshes**: their up-facing triangles are chained into boundary loops (arcs come out as short segments).
  A `TessellatedShapeBuilder` solid could not be built from them (open meshes).
- **Slab rule:** `Floors`, top within -100…+50 of the level, at least 150 thick, material containing `CONCRETE`.
  Ceilings (`3 Ceiling` layer, 10-40 thick) and the podium's `NLA` space volumes (generic models) drop out. Force
  elements in/out with `INCLUDE`/`EXCLUDE`.
- **Walls and columns are cut out of the IFC slab** (ArchiCAD-style export): its top face has a hole for every
  wall and column that passes through it. The script cuts `Walls`, `Structural Columns`, `Columns` at mid-slab
  ("fills", solids and meshes) and fills back the holes they cover: `R = A - (H - F)`. What stays open are real
  voids (lifts, stairs, risers) and service penetrations. Booleans run on thin extrusions of the loops projected
  onto the level. Two pitfalls:
  - `R = (A - H) + F` ("union" mode) failed in Revit's booleans on both levels (coincident faces, and holes that
    touch the outer loop at a vertex). The default "holes" mode worked every time.
  - Walls that cut into the slab *edge* are notches, not holes: a facade wall splitting the slab from its balcony
    strip leaves a slot open at one end. Fill faces whose surroundings are ≥ `EMBED` (0.6) slab are unioned in one
    by one.
- **Stairs:** every IFC stair within `STAIR_REACH` of the level, as a plan convex hull, for the stair void rule.

## Planning rules (`plan_slab.py`)

- **Outline clean-up:** loops touching themselves (a column square pinched onto the edge at one vertex, 1 mm apart)
  are split. Collinear runs are merged. Runs of short segments are fitted back to arcs: at least 3 edges, every
  vertex turning 0.3-30° the same way, at least 20° of sweep, within 5 mm (this rules out square corners, which
  are concyclic, and nearly straight runs). Arcs with a sagitta under 5 mm become lines.
- **Pockets** (the example runs straight past them):
  - straight pocket: a notch in an otherwise straight edge (facade columns, small recesses), up to
    `pocket_area` 6 m² / `pocket_mouth` 3.5 m / `pocket_depth` 2.5 m, closed with the chord;
  - corner pocket: a wall standing in a corner of the edge. Both edges are extended to the corner, only when fills
    cover ≥ `corner_fill` (0.5) of the added area, so real steps in the edge stay.
- **Snap** lines within 0.2° of the host axes (vertex moves < 1 mm here).
- **Holes:** `stair` (overlaps an IFC stair reaching the slab band), `penetration` (< 0.5 m² or thinner than
  150 mm), `shaft` (the rest). **Stair void** = the union of the flights' plan rectangles, with the gap between
  scissor flights (≤ `stair_bridge` 300) bridged. The IFC's stair hole covers the whole shaft (the IFC has no floor
  landings); the example keeps the landings as slab. First job: 2400 x 5097, the example's 2400 x 4565 has the
  same staggered shape. Islands < 2 m² inside voids (the scissor stair's central wall) are dropped.
- `keep_voids` (default `["stair"]`, as in the example) decides which classes become voids of the floor. The rest
  are listed in the plan with sizes, for the report.
- Type, offset and structural come from `--example`. The IFC's own slab top was at the level; the example sets it
  20 below, so FFL = level.

## Result on the first job

LEVEL 5 and LEVEL 10: one `CONCRETE 220MM` structural floor each, offset -20, 30-edge outline (12 arcs, R1000/R1500),
one stair void; 1060.3 m² each (example 1061.6-1061.7). The two outlines are identical within 1.2 mm, and the
example matches to 3.8 m² (L10) and 5.4 m² (L5). No warnings on the floors.

## Report to the user

- per level: floor id, type, thickness, offset, area; the type created and from what;
- what the outline cleaned up (pockets closed, pinched column holes);
- voids modelled and **voids left out**, with sizes: lift shafts, risers, penetrations. On the first job the
  example had no lift or riser voids, so they were left out. The REP puts downpipe penetrations and any cutout
  that voids the floor in the Floor Slab group, so offer to add them (`keep_voids` + `"penetration"` /
  `"shaft"`, or void families). Ask whether lifts/risers should be slab voids or shaft openings;
- families left out (setdowns) and why - they belong in the Floor Slab group once the units are set out;
- grouping proposal per the REP: one `Floor Slab` group per level band, e.g. `A1-Floor Slab_L05` and
  `A1-Floor Slab_L06-L24`. Take the band from the IFC slabs of every level, like `group_bands.py` does for walls.
  Identical outlines on several levels share one type (L5 and L10 were identical on the first job). Workset: ask
  (`12_STRUCTURE` suggested; the REP doesn't say).

Then copy `slab_<LV>.json`, the manifests, `ifc_slabs.json`, `example_slabs.json` and the renders to
`<model folder>\remodel_records\floor_slab\` with a short README, and ask whether to save the model.
