---
name: dwg-to-rvt-converter
description: Create a Revit model from a DWG/DXF drawing set - import and align the CAD, analyze the floor plans, elevations and sections (sheets, views, scales, levels, grids, door/window schedule), set up the levels, find and load families from the default (Australian) Revit library, then build walls, columns, doors, windows, floors and roofs. Use this skill whenever the user wants to model, convert, trace or "create a Revit model" from a DWG or AutoCAD file, import a DWG into Revit, read levels off DWG elevations, or remodel a building from CAD drawings - even if they only say "import this DWG" or "model the restaurant from the CAD".
---

# DWG-to-RVT converter

Builds a native Revit model from a DWG set, one floor at a time, with the CAD imported underneath for checking. Use
it with the `revit` skill (MCP tools, feet vs mm, IronPython 2.7, transactions, unattended mode). For a **simple
remodel** (the user's words for the first job) the Revit Execution Plan does not apply: no REP grouping, worksets
or naming - just the model. Use the REP skills only when the user asks for them.

First job: `Claude-DWG-Import-Test\Restaurant.dwg` (LAVITA restaurant, Vietnamese architect, 2 storeys + gable roof),
Revit 2025, GroupGSA template. Numbers below come from that job; its records are in
`<model folder>\dwg_records\Restaurant\` (README there has every input).

## The user's rules

1. **Import the DWG into Revit** - the plans go into their level's plan view (origin to origin, this view only,
   pinned); elevations into the matching Revit elevation to check the levels.
2. **Inform the levels from the DWG**: the elevations/sections carry the level marks. Create the levels from them
   and show the table (name, mm, where it came from) with anything inconsistent.
3. **Use the families in the model first; if one is missing, use the default Australia library**
   `C:\ProgramData\Autodesk\RVT <year>\Libraries\English\Australia` (the Revit version of the model).
4. Scripts live in this skill's `scripts/` (local = CPython, revit = IronPython), data in the session scratchpad,
   records next to the model (`dwg_records\<name>\`). Add to this skill as you go.
5. Ask before saving; never sync. Check the central path first (see "Saving").

## Tools

Local scripts need `ezdxf` (and `matplotlib` for pictures). Run them without installing anything globally:
`uv run -q --with ezdxf --with matplotlib python <script> ...` with `PYTHONPATH=<skill>/scripts/local` and
`PYTHONIOENCODING=utf-8`. Revit scripts run through `execute_revit_code`: set the inputs, then
`execfile(r"<skill>\scripts\revit\<script>.py")`. Every Revit script prints what it did and makes one named
transaction ("MCP: ...").

| Tool | Scripts |
|---|---|
| **Import and align CAD** | `local/dwg_to_dxf.py` (AutoCAD accoreconsole: DWG -> DXF, and `--to-dwg` DXF -> DWG), `local/extract_view.py` (one view -> 1:1 mm DXF, shared origin, registration between floors), `local/overlay.py` (check alignment), `revit/import_cad.py` (import into a view, `ALIGN` a DWG point onto a model point), `revit/place_elevation_markers.py` |
| **Analyze floor plan and section** | `local/analyze_dwg.py` (sheets, views, kinds, scales, level marks + consistency, grids, layers; `--render`), `local/plan_model.py` (walls, columns, openings with tags and swings from one plan), `local/outlines.py` (slab/roof outlines), `local/cadlib.py` (shared: VNI decoding, level parsing, sheet frames) |
| **Find and load default library** | `local/find_family.py "awning window" --year 2025 [--size 700x700]` (searches the AU library, reads type catalogs), `revit/load_family.py` (loads families / catalog types) |
| Levels and build | `revit/create_levels.py`, `revit/build_model.py` (walls -> columns -> openings per floor, manifest), `revit/build_envelope.py` (floors, footprint roofs) |

## Workflow

0. **Survey** (`get_revit_status`, levels, views, wall/door/window/column types, worksharing, central path).
   Switch unattended mode on for the build (`import revit_mcp.unattended as U; U.enable(minutes=240)`), off at the end.
1. **DWG -> DXF**: `dwg_to_dxf.py <file.dwg> --out <scratch>`. Works on a copy, so the DWG may stay open in AutoCAD.
   Revit's import API does not expose DWG text, and the levels are text - so read the DXF.
2. **Analyze**: `analyze_dwg.py <file.dxf> --out dwg_analysis.json --render`. Look at `overview.png` and the
   `view_<id>.png` of every plan, elevation and section before deciding anything. It prints per view: kind, scale
   (title, else DIMLFAC), storey, level marks with their drawn height (`(!-0.750)` = label disagrees), grids.
3. **Levels**: take the storeys' FFLs, eaves/top of wall, ridge (geometry top of the elevations when not labelled)
   and existing ground. Show the table, then `create_levels.py` (DRY_RUN first). Keep the template's own datum
   level (e.g. AHD LEVEL at 0) - report a clash, don't rename it.
4. **Extract the views**: `extract_view.py <dxf> <view id> --name GF`; upper floors `--ref GF`; elevations with
   their own name. Check floors with `overlay.py ov.png GF.dxf L1.dxf --layers "."` - stairs, wet areas and
   columns must stack.
5. **Import**: `import_cad.py` per plan (FILE, VIEW_NAME, VIEW_TYPE="FloorPlan"), then the main elevation into its
   Revit elevation with `ALIGN = {"dwg": (x, 0), "model": (X, None, 0)}` (x = a known edge in the elevation, e.g. the
   slab edge line, X = the same edge in the model). `place_elevation_markers.py` if a marker sits in the building.
   Look at the elevation: the Revit levels must sit on the DWG level marks.
6. **Schedule**: read the door/window schedule sheet (texts + dims of the `schedule`/door-detail sheet) into
   `schedule.json` `{tag: {kind, width, height, sill, count, desc}}`.
7. **Plan each floor**: `plan_model.py GF.dxf --out plan_GF.json --schedule schedule.json --render plan_GF.png`
   (upper floors add `--below plan_GF.json`, and `--config` for drawing-specific layers). Look at the render:
   red/blue walls by thickness, black columns, green doors with swing arrows, cyan windows, tags.
8. **Families**: map every tag to a family (template first). Missing -> `find_family.py`, then `load_family.py`.
9. **Build**: `build_model.py` per floor, STEP "walls", "columns", "openings" (LEVEL, TOP_LEVEL, PLAN, MANIFEST,
   RULES). Then `outlines.py` for the slab edges and `build_envelope.py` for floors and roofs.
10. **Check and report**: counts per level and type vs the schedule counts, `doc.GetWarnings()`, 3D view, the
    elevation overlay. Copy the scratchpad data to `<model folder>\dwg_records\<name>\` with a README. Ask to save.

## Reading the DWG (what analyze_dwg.py knows)

- **Sheets in model space.** VN/Asian sets draw every sheet at 1:1 paper size (A1 frame ~ 767 x 564 units) with the
  drawings scaled into it; one DWG unit is then 50 mm at 1:50. Every view has its own scale: the title
  ("TL 1/50", "TỈ LỆ 1/25", "SCALE 1:100") or the DIMLFAC of its dimension style ("NAT 1-50" = 50). With no frames,
  the model space is one sheet at 1:1 (the usual AU/UK set with paper-space layouts).
- **Views** = clusters of geometry inside a frame, title-block strip excluded. A title that stands apart is merged
  into the drawing above it. Kinds by title keywords (Vietnamese folded to ASCII, English): MẶT BẰNG = plan,
  MẶT ĐỨNG = elevation, MẶT CẮT = section, MÁI = roof, CHI TIẾT = detail, THỐNG KÊ/BẢNG = schedule. Storeys: TRỆT /
  TẦNG 1 = ground, LẦU 1 / TẦNG 2 = level 1, LỬNG = mezzanine.
- **VNI text.** Fonts VNI-Helve/VNI-Times store Vietnamese as ASCII + marks ("MAËT ÑÖÙNG" = "MẶT ĐỨNG").
  `cadlib.decode` converts it; renders and extracted DXFs are re-encoded as Unicode with an Arial style, so Revit
  shows proper Vietnamese.
- **Level marks**: blocks whose attribute is a level (`CDO2` / `CAODO` = "+ 3.900", "%%p 0.000") or level texts
  (RL / FFL / ±). In elevations and sections each mark's drawn height is fitted: y = datum + value x 1000/scale.
  First job: ±0.000, +3.900, +6.680, -0.150 agreed on three elevations; the existing ground was labelled "+0.650"
  but drawn at -0.750 (a drafting error, reported), and section 1-1 of the kiosk was drawn at 1:25 under a
  "TL 1/50" title (the marks gave the true scale).
- **Layers** (VN names): TƯỜNG = wall lines, HATCH (ANSI31) = wall fill, CỘT = columns, CỬA = doors and windows,
  CỬA SỔ/CUASO = windows, TRỤC = grids, KHUNG = frame, KÍCH THƯỚC/dim = dimensions, GHI CHÚ/CHÚ THÍCH = notes.

## Import and align (extract_view.py + import_cad.py)

- One DXF per view, scaled to mm, dimensions/leaders exploded (their text keeps the drawn value), VNI decoded.
  Origin = lower-left of the ground floor's wall layers (rounded to 10 mm); every other plan is registered onto it.
- **Registering floors**: candidates are the top vertex-vote shifts plus "same place on the sheet" (architects copy
  the frame), each refined and scored by how much wall line work lands on the reference. The plain vertex vote
  picked a repeated pattern on the first job (L1 1.9 m off); the scored version agreed with the sheet position.
  Force a shift with `--shift dx,dy` (sheet units) when needed, and always look at `overlay.py`.
- **Revit import quirks** found on the first job:
  - Elevation/section/drafting views refuse origin-to-origin: `import_cad.py` falls back to centred and `ALIGN`
    moves the import. Revit pins the new import: unpin, move, pin.
  - `Document.Import` returns False without a message when the DXF has a text style without a font file (a
    TrueType style that kept its font in XDATA) or a text pointing at a missing style (block attributes' styles are
    not copied by ezdxf's Importer). `extract_view.py` gives such styles Arial and turns attributes into TEXT.
    `dwg_to_dxf.py <x.dxf> --to-dwg` (AutoCAD AUDIT + SAVEAS) names the bad entity when a file still fails.

## Planning a floor (plan_model.py)

- **Walls from the wall hatches**: each hatch loop is cut into rectangles (orthogonal decomposition, longest runs
  first); long side = axis, short side = thickness. Stubs shorter than 2x their thickness take the orientation of
  the long wall they line up with (a 140 x 200 pier in a 200 wall is a wall piece, not a 140 wall across it).
  Unhatched partitions: `"wall_outline_layers"` (closed polylines whose parts are all <= 200 thick; the stair
  treads on layer 0 were 280 and must stay out).
- **Columns**: solid hatches / closed squares on the column layer; small square hatches standing alone; blocks by
  name (`"column_blocks"`, e.g. the steel `I` posts, kind steel). Columns outside the walls' box + 2.5 m are legend
  symbols. `--below` snaps upper-floor columns within 150 mm onto the floor below (L1 posts were 35-85 mm off).
- **Openings**: from each wall end the nearest solid along the axis (wall piece, crossing wall or column). The gap is
  an opening only with evidence: a **door arc hinged at a gap end whose closed leaf lies in the wall band**, or 2+
  parallel lines inside the band covering 80% of the gap (window). Pieces are merged / extended across it. Arcs
  from mirrored blocks have their centre in OCS - convert (`ocs().to_wcs`). Windows drawn as several sash blocks
  count by combined coverage.
- **Tags**: each tag text (Đ1, S2, VK1, D01, W03) goes to its nearest opening - one tag, one opening. Tags the
  plans don't show a size for take it from the schedule; untagged doors keep the drawn gap.
- **Duplicates**: a hatch and an outline of the same wall (same axis, similar thickness) keep the longer; a wall
  that thickens along its length keeps the thicker in the overlap.
- Overrides in `--config`: `extra_walls`, `drop_walls_near`, layer regexes, `column_margin`, `max_opening`.

## Building (build_model.py, build_envelope.py)

- Walls `GROUPGSA - GENERIC-<t>mm` (made from GENERIC-100mm when missing), centreline, base = the floor's level,
  top = next level. Columns: `COLUMN - CONCRETE - SQUARE` "<w> x <d>mm" (param b), steel `COLUMN - STEEL - UNIVERSAL`
  "150 UC" (d, bf, tf, tw, k).
- Openings: type "<W> x <H>" in the tag's family, params by formula of W/H in `RULES["openings"][tag]` (ASCII keys:
  Đ -> D). Template conventions: DOOR - SINGLE - STD Width = W-60, Height = H-50; DOOR - DOUBLE - STD Width = W-80,
  Panel Width = half; DOOR - SLIDER 2P Width/Height = W/H, Sliding Sash Width = W/2-10, Centre Sash = half of it,
  Leaf Height = H-70. Windows get the schedule sill. The tag becomes the type's Type Mark (instance Marks must be
  unique). Doors are flipped until their own plan arc matches the DWG hinge and swing side.
- Every step records plan id -> element id in its manifest (written atomically). Walls also carry
  `DWG <plan> <id>` in Comments, so a run whose manifest was lost adopts them instead of building them twice.
  `ElementId.Value` is Int64: cast to int before json.
- Floors: `outlines.py` finds the slab edge (the L1 edge was a 250 m2 polyline on a notes layer). Roofs are footprint
  roofs; `slope_edges` "x-min,x-max" + `ridge_mm` gives a gable whose ridge lands on the DWG ridge.
- `NewFootPrintRoof` in IronPython needs `clr.Reference[DB.ModelCurveArray](DB.ModelCurveArray())` - an empty
  reference throws "Value cannot be null".

## Result on the first job (Restaurant)

Levels EXISTING GROUND -750, GROUND FLOOR 0, LEVEL 1 3900, ROOF 6680, RIDGE 10630. GF 25 walls, 20 concrete columns,
12 doors, 3 windows; L1 35 walls, 19 steel posts, 4 doors, 2 windows; GF slab + east terrace (-150), L1 slab; gable
roof 29.1 deg. Door counts matched the schedule (D1 1, D2 2, D3 2, D4 1, D5 5, D6 3, D7 1); S1 3 of 4. Only the D1
glass door came from the AU library (`Doors\Double-Glass 1.rfa`); everything else was in the template. Left out:
stairs, furniture, fixtures, site/fence/kiosk/guard house, gable infill, arched heads, dumbwaiter hatches. The main
elevation overlay in Revit's SOUTH view matched the model's gable and all five levels.

## Saving

The first model was a Save-As of another project's local file: workshared, with its central path still pointing at
`Claude-Remodel-Test\remodeling-test-towerC.rvt`. Syncing would have written the restaurant into the tower C central.
Check `doc.GetWorksharingCentralModelPath()` before any save; if it is someone else's central, offer "Save As new
central" (or detach) and let the user choose. Never sync on your own.

## Report to the user

Levels table (and the inconsistencies found), what was imported where, counts per level and type vs the schedule,
families loaded from the library, what was left out, warnings, where the records are, and the save question.
