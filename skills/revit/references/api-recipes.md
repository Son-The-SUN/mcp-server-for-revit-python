# Revit API recipes for `execute_revit_code`

Every snippet is IronPython 2.7 and was run through `execute_revit_code` against Revit 2025. `doc`, `uidoc` and `DB` are already defined. Copy the helpers into any script that needs them; each call runs in a fresh namespace, so nothing carries over between calls.

## Contents

1. [Helpers: IDs and names](#helpers-ids-and-names)
2. [Collect elements](#collect-elements)
3. [Read parameters](#read-parameters)
4. [Levels in millimetres](#levels-in-millimetres)
5. [Sheets](#sheets)
6. [Rooms and areas](#rooms-and-areas)
7. [Current selection](#current-selection)
8. [Write parameters in a transaction](#write-parameters-in-a-transaction)
9. [Delete elements](#delete-elements)

## Helpers: IDs and names

```python
def eid_int(element_id):
    # Revit 2024+ has .Value; .IntegerValue was removed in 2026
    v = getattr(element_id, "Value", None)
    return int(v) if v is not None else element_id.IntegerValue

def el_name(el):
    # element.Name raises AttributeError on some types in IronPython
    try:
        return el.Name
    except AttributeError:
        return DB.Element.Name.__get__(el)

element = doc.GetElement(DB.ElementId(694))   # look up by integer id
```

## Collect elements

```python
# Instances (not types) of one category
walls = (DB.FilteredElementCollector(doc)
         .OfCategory(DB.BuiltInCategory.OST_Walls)
         .WhereElementIsNotElementType()
         .ToElements())
print("walls: {}".format(len(walls)))

# Count by type name
counts = {}
for el in walls:
    t = doc.GetElement(el.GetTypeId())
    key = el_name(t) if t else "<no type>"
    counts[key] = counts.get(key, 0) + 1

# Everything hosted on / associated with a level
level = [l for l in DB.FilteredElementCollector(doc).OfClass(DB.Level) if el_name(l) == "LEVEL 1"][0]
ids = (DB.FilteredElementCollector(doc)
       .WherePasses(DB.ElementLevelFilter(level.Id))
       .WhereElementIsNotElementType()
       .ToElementIds())
```

Use `OfClass(DB.Level)`, `OfClass(DB.ViewSheet)`, `OfClass(DB.FamilyInstance)` and so on when you know the API class, and `OfCategory(DB.BuiltInCategory.OST_...)` when you know the category. Instance counts per category are also available without code from `get_revit_model_info`.

## Read parameters

```python
p = level.get_Parameter(DB.BuiltInParameter.LEVEL_ELEV)   # by built-in id (language-independent)
p = level.LookupParameter("Elevation")                     # by display name (UI-language dependent)
if p:
    p.AsDouble()        # 13.1233595801  -> internal units (feet)
    p.AsValueString()   # "4000.00"      -> formatted in project display units
    # also: p.AsString(), p.AsInteger(), p.AsElementId(), p.StorageType

# Type parameters live on the type element
el_type = doc.GetElement(level.GetTypeId())
type_name = el_type.get_Parameter(DB.BuiltInParameter.ALL_MODEL_TYPE_NAME).AsString()
```

`LookupParameter` returns `None` when the name doesn't exist, so check before calling methods on it. `list_category_parameters` shows which parameter names exist on a category without writing code.

## Levels in millimetres

```python
levels = sorted(DB.FilteredElementCollector(doc).OfClass(DB.Level).ToElements(), key=lambda l: l.Elevation)
for lv in levels:
    mm = DB.UnitUtils.ConvertFromInternalUnits(lv.Elevation, DB.UnitTypeId.Millimeters)
    print("{} | id {} | {:.0f} mm".format(el_name(lv), eid_int(lv.Id), mm))
```

Other units: `DB.UnitTypeId.Meters`, `SquareMeters`, `CubicMeters`, `Degrees`. To check what the project displays, use `doc.GetUnits().GetFormatOptions(DB.SpecTypeId.Length).GetUnitTypeId().TypeId`, which returns for example `autodesk.unit.unit:millimeters-1.0.1`.

## Sheets

```python
sheets = sorted(DB.FilteredElementCollector(doc).OfClass(DB.ViewSheet).ToElements(), key=lambda s: s.SheetNumber)
print("sheets: {}".format(len(sheets)))
for s in sheets[:30]:
    print("{} - {}".format(s.SheetNumber, s.Name))
```

## Rooms and areas

```python
rooms = (DB.FilteredElementCollector(doc)
         .OfCategory(DB.BuiltInCategory.OST_Rooms)
         .WhereElementIsNotElementType()
         .ToElements())
placed = [r for r in rooms if r.Area > 0]   # unplaced / unenclosed rooms have Area 0
for r in placed:
    m2 = DB.UnitUtils.ConvertFromInternalUnits(r.Area, DB.UnitTypeId.SquareMeters)
    print("{} {} {:.2f} m2 ({})".format(r.Number, el_name(r), m2, el_name(r.Level) if r.Level else "-"))
```

## Current selection

```python
ids = uidoc.Selection.GetElementIds()
for eid in list(ids)[:50]:
    el = doc.GetElement(eid)
    print("{} | {} | {}".format(eid_int(eid), el.Category.Name if el.Category else "-", el_name(el)))
```

Use this when the user says "the selected elements" or "these walls": it reads what they highlighted in Revit.

## Write parameters in a transaction

```python
def set_param(param, value):
    # value must already be in internal units for doubles (feet, radians...)
    st = param.StorageType
    if st == DB.StorageType.String:
        return param.Set(str(value))
    if st == DB.StorageType.Integer:
        return param.Set(int(value))
    if st == DB.StorageType.Double:
        return param.Set(float(value))
    if st == DB.StorageType.ElementId:
        return param.Set(value)
    return False

t = DB.Transaction(doc, "MCP: set Level 1 elevation to 8100 mm")
t.Start()
try:
    p = level.get_Parameter(DB.BuiltInParameter.LEVEL_ELEV)
    if p is None or p.IsReadOnly:
        raise Exception("parameter missing or read-only")
    set_param(p, DB.UnitUtils.ConvertToInternalUnits(8100, DB.UnitTypeId.Millimeters))
    t.Commit()
except Exception:
    t.RollBack()
    raise
```

`param.Set` returns `False` rather than raising when Revit rejects a value, so check the return value when it matters. To try a change without keeping it (for example to preview how many elements a delete would take), do the work and then call `t.RollBack()` instead of `t.Commit()`.

## Delete elements

```python
from System.Collections.Generic import List

ids = List[DB.ElementId]([DB.ElementId(123456)])
t = DB.Transaction(doc, "MCP: delete 1 sheet")
t.Start()
try:
    deleted = doc.Delete(ids)   # returns every deleted id, including dependents
    print("deleted {} elements".format(deleted.Count))
    t.Commit()
except Exception:
    t.RollBack()
    raise
```

Deleting one element often removes dependents too: in testing, deleting one sheet removed 15 elements, including its viewports and title block. Report the returned count to the user. For a destructive request, do a dry run first: delete, print the count, then `t.RollBack()`, and commit only after the user confirms.
