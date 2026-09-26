# -*- coding: utf-8 -*-
"""Find and load default library, step 2: load library families (and catalog types) into the model.

Runs INSIDE Revit (IronPython 2.7) through execute_revit_code:

    FAMILIES = [
        r"C:\\ProgramData\\Autodesk\\RVT 2025\\Libraries\\English\\Australia\\Doors\\Double-Glass 1.rfa",
        {"path": r"...\\Windows\\Awning - 1L (AUS).rfa", "types": ["0600 x 0600"]},   # type catalog: only these
    ]
    # optional: OVERWRITE = False     # True reloads a family that is already in the model (keeps instances)
    # optional: RENAME = {"Double-Glass 1": "DOOR - DOUBLE - GLASS"}   # family name after loading
    execfile(r"<repo>\\skills\\dwg-to-rvt-converter\\scripts\\revit\\load_family.py")

A family already in the model (same name) is skipped unless OVERWRITE. Prints family, category and types.
One transaction: "MCP: load library families".
"""
import os
import clr

OVERWRITE = globals().get("OVERWRITE", False)
RENAME = globals().get("RENAME", {})


class _LoadOpts(DB.IFamilyLoadOptions):
    def OnFamilyFound(self, familyInUse, overwriteParameterValues):
        overwriteParameterValues.Value = False
        return bool(OVERWRITE)

    def OnSharedFamilyFound(self, sharedFamily, familyInUse, source, overwriteParameterValues):
        source.Value = DB.FamilySource.Family
        overwriteParameterValues.Value = False
        return bool(OVERWRITE)


def nm(e):
    try:
        return e.Name
    except Exception:
        return DB.Element.Name.__get__(e)


existing = {nm(f): f for f in DB.FilteredElementCollector(doc).OfClass(DB.Family)}
out = []
t = DB.Transaction(doc, "MCP: load library families")
t.Start()
try:
    for spec in FAMILIES:
        if isinstance(spec, dict):
            path, types = spec["path"], spec.get("types")
        else:
            path, types = spec, None
        fname = os.path.splitext(os.path.basename(path))[0]
        if not os.path.isfile(path):
            out.append("MISSING  " + path)
            continue
        fam = existing.get(RENAME.get(fname, fname)) or existing.get(fname)
        if fam is not None and not OVERWRITE and not types:
            out.append("in model {} ({})".format(nm(fam), fam.FamilyCategory.Name if fam.FamilyCategory else "?"))
            continue
        if types:
            loaded = []
            for tn in types:
                ref = clr.Reference[DB.FamilySymbol]()
                if doc.LoadFamilySymbol(path, tn, _LoadOpts(), ref):
                    loaded.append(tn)
            fam = [f for f in DB.FilteredElementCollector(doc).OfClass(DB.Family) if nm(f) == fname]
            fam = fam[0] if fam else None
            status = "loaded types {}".format(loaded) if loaded else "types already in model / not in catalog: {}".format(types)
        else:
            ref = clr.Reference[DB.Family]()
            ok = doc.LoadFamily(path, _LoadOpts(), ref)
            fam = ref.Value if ok else [f for f in DB.FilteredElementCollector(doc).OfClass(DB.Family) if nm(f) == fname][0]
            status = "loaded" if ok else "reloaded/kept"
        if fam is not None and fname in RENAME and nm(fam) != RENAME[fname]:
            fam.Name = RENAME[fname]
        if fam is not None:
            syms = [nm(doc.GetElement(i)) for i in fam.GetFamilySymbolIds()]
            out.append("{:<14} {} ({}) types: {}".format(status, nm(fam), fam.FamilyCategory.Name if fam.FamilyCategory else "?",
                                                      ", ".join(sorted(syms)[:12]) + (" ..." if len(syms) > 12 else "")))
    t.Commit()
except Exception:
    t.RollBack()
    raise
print("\n".join(out))
