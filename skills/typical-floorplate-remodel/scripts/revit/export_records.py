# -*- coding: utf-8 -*-
"""Write the remodel record: one row per IFC element of each remodelled floor -> what it became in Revit.

Runs INSIDE Revit (IronPython 2.7) through execute_revit_code, after building (and grouping):

    FLOORS = {"L5":  {"level_id": 950753, "build": r"<work>\\build_L5.json",  "manifest": r"<work>\\manifest_L5.json",
                      "groups": r"<work>\\groups_L5.json"},
              "L10": {...}}
    OUT_DIR = r"C:\\...\\<project folder>\\remodel_records"
    execfile(r"<repo>\\skills\\typical-floorplate-remodel\\scripts\\revit\\export_records.py")

Writes OUT_DIR\\remodel_records.csv (opens in Excel) and remodel_records.json. Key the rows by IFC GUID - the link's
element ids change when a revised IFC is re-linked, the GUIDs don't. Revit elements are given by ElementId and
UniqueId (UniqueId is the stable one).

Elements whose manifest id no longer exists because a shared group type was placed on their level (the loose copies
were deleted, see group_revit.py "place") are re-matched to the member of that level's group instance at the same
plan position (walls: location-curve midpoint, doors/windows: insertion point; within 100 mm, same category).
Screens, dropped duplicates and skipped IFC walls are listed with an empty Revit id and the reason.
"""
import json
import os
import codecs

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


def xy_of(e):
    loc = e.Location
    if isinstance(loc, DB.LocationCurve):
        p = loc.Curve.Evaluate(0.5, True)
    elif isinstance(loc, DB.LocationPoint):
        p = loc.Point
    else:
        return None
    return (p.X / MM, p.Y / MM)


def planned_xy(kind, rec):
    if kind == "walls":
        if rec.get("kind") == "arc":
            return tuple(rec["pm"])
        return ((rec["p0"][0] + rec["p1"][0]) / 2.0, (rec["p0"][1] + rec["p1"][1]) / 2.0)
    return tuple(rec["pt"]) if rec.get("pt") else None


CAT = {"walls": "Walls", "doors": "Doors", "windows": "Windows"}
rows = []
for key in sorted(FLOORS.keys()):
    F = FLOORS[key]
    level = doc.GetElement(DB.ElementId(F["level_id"]))
    B = json.loads(open(F["build"]).read())
    M = json.loads(open(F["manifest"]).read())
    G = json.loads(open(F["groups"]).read())
    # members of group instances on this level, for re-matching replaced elements
    pool = []
    for g in DB.FilteredElementCollector(doc).OfClass(DB.Group):
        if eid(g.LevelId) != eid(level):
            continue
        for mid in g.GetMemberIds():
            m = doc.GetElement(mid)
            if m is not None and m.Category is not None:
                pool.append((m, m.Category.Name, xy_of(m)))
    for kind in ("walls", "doors", "windows"):
        for rec in B.get(kind, []):
            k = str(rec["ifc_id"])
            rid = M[kind].get(k)
            el = doc.GetElement(DB.ElementId(rid)) if rid else None
            note = ""
            if rid and el is None:
                p = planned_xy(kind, rec)
                best = None
                if kind == "walls" and rec.get("kind") == "line":
                    # same line: parallel, < 50 mm apart, best length overlap (lengths can differ a little between
                    # floors because neighbouring walls trim them differently)
                    a0, a1 = rec["p0"], rec["p1"]
                    La = ((a1[0] - a0[0]) ** 2 + (a1[1] - a0[1]) ** 2) ** 0.5
                    d = ((a1[0] - a0[0]) / La, (a1[1] - a0[1]) / La)
                    for m, cname, q in pool:
                        if cname != "Walls" or not isinstance(m.Location, DB.LocationCurve) or not isinstance(m.Location.Curve, DB.Line):
                            continue
                        c = m.Location.Curve
                        b0 = (c.GetEndPoint(0).X / MM, c.GetEndPoint(0).Y / MM)
                        b1 = (c.GetEndPoint(1).X / MM, c.GetEndPoint(1).Y / MM)
                        Lb = ((b1[0] - b0[0]) ** 2 + (b1[1] - b0[1]) ** 2) ** 0.5
                        e = ((b1[0] - b0[0]) / Lb, (b1[1] - b0[1]) / Lb)
                        if abs(d[0] * e[1] - d[1] * e[0]) > 0.01:
                            continue
                        if abs((b0[0] - a0[0]) * -d[1] + (b0[1] - a0[1]) * d[0]) > 50:
                            continue
                        tb = sorted([(b[0] - a0[0]) * d[0] + (b[1] - a0[1]) * d[1] for b in (b0, b1)])
                        ov = min(La, tb[1]) - max(0.0, tb[0])
                        if ov > 0.5 * min(La, Lb) and (best is None or ov > best[0]):
                            best = (ov, m)
                else:
                    for m, cname, q in pool:
                        if cname != CAT[kind] or q is None or p is None:
                            continue
                        dist = ((q[0] - p[0]) ** 2 + (q[1] - p[1]) ** 2) ** 0.5
                        if dist < 100 and (best is None or -dist > best[0]):
                            best = (-dist, m)
                if best:
                    el = best[1]
                    note = "replaced by shared group instance member"
                else:
                    note = "Revit element missing"
            elif not rid:
                note = rec.get("special") or ("no family planned" if kind != "walls" else "not built")
            gname = gid = ""
            if el is not None and eid(el.GroupId) > 0:
                grp = doc.GetElement(el.GroupId)
                gname, gid = nm(doc.GetElement(grp.GetTypeId())), eid(grp)
            typ = doc.GetElement(el.GetTypeId()) if el is not None else None
            rows.append({
                "floor": key, "level": nm(level), "category": CAT[kind], "ifc_guid": rec.get("guid"),
                "ifc_link_id": k, "ifc_name": rec.get("name") or "", "ifc_layer": rec.get("layer") or "",
                "rep_group": G[kind].get(k, G["walls"].get(k, "")),
                "revit_id": eid(el) if el is not None else "", "revit_unique_id": el.UniqueId if el is not None else "",
                "revit_type": ("%s : %s" % (typ.FamilyName, nm(typ))) if typ is not None and hasattr(typ, "FamilyName") else (nm(typ) if typ else ""),
                "group_type": gname, "group_id": gid, "note": note})
    for k, rid in M["walls"].items():
        if not k.startswith("host-for-"):
            continue
        el = doc.GetElement(DB.ElementId(rid))
        typ = doc.GetElement(el.GetTypeId()) if el is not None else None
        grp = doc.GetElement(el.GroupId) if el is not None and eid(el.GroupId) > 0 else None
        rows.append({"floor": key, "level": nm(level), "category": "Walls", "ifc_guid": "", "ifc_link_id": "",
                     "ifc_name": "", "ifc_layer": "", "rep_group": G["walls"].get(k, ""),
                     "revit_id": rid if el is not None else "", "revit_unique_id": el.UniqueId if el is not None else "",
                     "revit_type": nm(typ) if typ else "", "group_type": nm(doc.GetElement(grp.GetTypeId())) if grp else "",
                     "group_id": eid(grp) if grp else "",
                     "note": "host wall created for IFC opening %s (no IFC host wall)" % k[len("host-for-"):]})
    for rec in B.get("screens", []):
        rows.append({"floor": key, "level": nm(level), "category": "Walls", "ifc_guid": "", "ifc_link_id": str(rec["ifc_id"]),
                     "ifc_name": "", "ifc_layer": rec.get("layer") or "", "rep_group": "", "revit_id": "", "revit_unique_id": "",
                     "revit_type": "", "group_type": "", "group_id": "", "note": "not modelled: screen of rods/posts"})
    for rec in B.get("dropped", []):
        rows.append({"floor": key, "level": nm(level), "category": "Walls", "ifc_guid": rec.get("guid"), "ifc_link_id": str(rec["ifc_id"]),
                     "ifc_name": rec.get("name") or "", "ifc_layer": rec.get("layer") or "", "rep_group": "", "revit_id": "",
                     "revit_unique_id": "", "revit_type": "", "group_type": "", "group_id": "",
                     "note": "not modelled: " + "; ".join(rec.get("notes", []))})
    for i in B.get("skipped", []):
        rows.append({"floor": key, "level": nm(level), "category": "Walls", "ifc_guid": "", "ifc_link_id": str(i), "ifc_name": "",
                     "ifc_layer": "", "rep_group": "", "revit_id": "", "revit_unique_id": "", "revit_type": "", "group_type": "",
                     "group_id": "", "note": "not modelled: degenerate outline"})

if not os.path.isdir(OUT_DIR):
    os.makedirs(OUT_DIR)
cols = ["floor", "level", "category", "ifc_guid", "ifc_link_id", "ifc_name", "ifc_layer", "rep_group", "revit_id",
        "revit_unique_id", "revit_type", "group_type", "group_id", "note"]


def cell(v):
    s = u"%s" % (v if v is not None else "")
    return u'"%s"' % s.replace(u'"', u'""') if (u"," in s or u'"' in s) else s


f = codecs.open(os.path.join(OUT_DIR, "remodel_records.csv"), "w", "utf-8-sig")
f.write(u",".join(cols) + u"\r\n")
for r in rows:
    f.write(u",".join(cell(r[c]) for c in cols) + u"\r\n")
f.close()
f = open(os.path.join(OUT_DIR, "remodel_records.json"), "w")
f.write(json.dumps(rows, indent=1))
f.close()
from collections import Counter
print("rows {} | {}".format(len(rows), dict(Counter((r["floor"], "built" if r["revit_id"] != "" else "not built") for r in rows))))
print("notes: {}".format(dict(Counter(r["note"].split(":")[0] for r in rows if r["note"]))))
print("written to {}".format(OUT_DIR))
