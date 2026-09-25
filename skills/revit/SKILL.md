---
name: revit
description: How to work with a live Autodesk Revit model through the Revit MCP tools (get_revit_model_info, list_levels, list_revit_views, get_revit_view, list_families, place_family, color_splash, execute_revit_code, and the document tools). Use this skill whenever the user asks anything about their Revit model or project - levels, views, sheets, schedules, rooms, families, parameters, element counts, exporting a view image, placing or changing elements, color-coding by parameter, opening/saving/syncing documents - or wants Revit API, pyRevit or IronPython code run inside Revit, even if they never mention MCP or name a tool.
---

# Working with Revit through the Revit MCP tools

The Revit tools talk to a **live** Revit session through pyRevit Routes (HTTP on `127.0.0.1:48884`, served from inside Revit). Everything runs against the model the user has open right now: reads show its current state, and writes change it immediately. The user may be editing the same model while you work, so re-read state before acting on something you looked at a while ago.

## Start by confirming the connection

Call `get_revit_status` before the first real request in a conversation. It shows whether Revit answers and which document is active.

- **`Cannot connect to Revit at http://127.0.0.1:48884`**: Revit is not running, the pyRevit Routes server is off, or the extension is not loaded. Tell the user which of these to check. If they want Revit started, use `list_revit_installations` and then `launch_revit`, which waits until the bridge is ready.
- **No active document**: use `open_document` with a path the user gives you.

## Choose the dedicated tool first

The dedicated tools are tested, handle differences between Revit versions, and return compact structured output. Use `execute_revit_code` only for what they don't cover.

| Need | Tool |
|---|---|
| Overview: element counts by category, levels, rooms, view/sheet counts, warnings | `get_revit_model_info` |
| Levels with elevations and IDs | `list_levels` |
| View names by type | `list_revit_views` |
| Picture of a view (plan, 3D, sheet...) | `get_revit_view(view_name)`, using an exact name from `list_revit_views`. Sheets are listed under `other` by sheet name, not sheet number |
| Active view details / elements in it | `get_current_view_info`, `get_current_view_elements(limit, include_levels, include_location)` |
| Loaded families and types | `list_family_categories`, `list_families(contains, limit)` |
| Place a family instance | `place_family(family_name, type_name, x, y, z, rotation, level_name, properties)` |
| Color elements by a parameter value | `list_category_parameters` → `color_splash` → `clear_colors` to undo |
| Open / save / close / sync | `open_document`, `save_document`, `close_document`, `sync_with_central` |
| Anything else | `execute_revit_code` |

`get_current_view_elements` can return thousands of rows. Start with the default fields and a modest `limit`, and add `include_location` only when you need coordinates. If the result says `truncated`, `category_counts` still covers every element.

## Units: Revit's API works in feet

Every length the API returns or accepts is in **decimal feet** (areas in ft², volumes in ft³, angles in radians), whatever the project displays. The tools pass these values through unchanged:

- `list_levels` / `get_revit_model_info` elevations are feet. For example, 13.12 is 4000 mm.
- `place_family` x/y/z are feet. `rotation` is the exception: it takes degrees and the tool converts it.

Many projects display millimetres, so convert before you report numbers and before you pass numbers the user gave you. 1 ft = 304.8 mm. Inside `execute_revit_code`, let Revit convert:

```python
mm = DB.UnitUtils.ConvertFromInternalUnits(value_ft, DB.UnitTypeId.Millimeters)
ft = DB.UnitUtils.ConvertToInternalUnits(4000, DB.UnitTypeId.Millimeters)
```

`param.AsValueString()` returns the value formatted in the project's display units ("4000.00"), which suits reporting. `param.AsDouble()` returns internal units (feet), which suits calculations.

## Writing code for `execute_revit_code`

The code runs inside Revit under **IronPython 2.7**, not CPython 3. Code that looks right but is Python 3 fails.

- **No f-strings.** They are a `SyntaxError`. Use `"{} - {}".format(a, b)`.
- **`print` is the Python 2 statement.** `print("a", b)` prints the tuple `('a', 'b')`. Build one string and print that.
- Other Python 3-only syntax also fails: type hints, `nonlocal`, keyword-only arguments, `async`.
- **Available names:** `doc` (active Document), `uidoc` (UIDocument), `DB` (`Autodesk.Revit.DB`) and `revit` (pyRevit's module). Import anything else yourself.
- **Transactions:** none are opened for you. Wrap every model change in a named `DB.Transaction` and roll back on failure. An exception after `Start()` with no rollback leaves the change half-done. The transaction name is what the user sees in Revit's Undo list, so make it descriptive:

  ```python
  t = DB.Transaction(doc, "MCP: renumber Level 2 rooms")
  t.Start()
  try:
      # ... changes ...
      t.Commit()
  except Exception:
      t.RollBack()
      raise
  ```

- **UI changes don't go in a transaction.** For example, `uidoc.ActiveView = view` cannot run inside one.
- **Names:** `element.Name` raises `AttributeError` on some element types in IronPython. Use `DB.Element.Name.__get__(element)` as a fallback.
- **Element IDs:** Revit 2024+ has `ElementId.Value`, and Revit 2026 removed `.IntegerValue`. Read IDs with `getattr(eid, "Value", None)`, falling back to `.IntegerValue`, so the code works on every version. `DB.ElementId(123)` builds one.
- **Keep output small.** Everything you print comes back into the conversation. Print counts and the first few dozen rows rather than every element, and say that you truncated.
- **Timeouts:** calls time out after 60 s, but Revit may still finish the work. Before re-running a script that changes the model, check whether the first run already applied, or the change could happen twice.
- **Read the error.** A failure comes back with the traceback and often a `hints` list.

Tested snippets for common jobs are in [references/api-recipes.md](references/api-recipes.md): collecting elements, reading and writing parameters, levels in mm, sheets, rooms, and selection. Read it before writing anything non-trivial.

## Changing the model safely

The model is live and belongs to the user. It may be a real project shared with a team.

1. **Look before you change.** Query first and confirm the target set, for example "this matches 214 doors on Level 2". Change elements by ID once you have them, not by re-running a broad filter.
2. **Get a go-ahead for large or destructive changes.** Deleting elements, bulk parameter edits, and renumbering or renaming many items need a short summary of what will change and a clear yes from the user. Suggest `save_document` first on anything big.
3. **One named transaction per logical change,** so the user can undo it as a single step in Revit.
4. **Leave documents alone unless asked.** Don't save, close or `sync_with_central` on your own initiative: syncing publishes the user's work to their team.
5. **Undo colors after a visual check.** `color_splash` applies view overrides. Offer `clear_colors` when the user is done looking.

## When a call fails

| Symptom | Likely cause and what to do |
|---|---|
| `Cannot connect to Revit...` | See "Start by confirming the connection" above |
| Timeout on a simple call | Revit is busy: a modal dialog is open, a command is running, or the user is mid-edit. Ask them to check the Revit window. |
| `View '<name>' not found` | Get exact names from `list_revit_views`. The error shows only the first 20 names, so don't assume the view is missing. |
| `Level not found` in `place_family` | Use a name exactly as `list_levels` returns it |
| `InvalidOperationException` in code | The change needs a transaction, or a UI change is running inside one |
| `SyntaxError: invalid syntax` pointing at a string | Almost always an f-string or other Python 3 syntax (IronPython 2.7) |
