"""Write group_plan.json: which REP group types to create, their names, and which levels share them.

    python group_plan.py --building A1 --floor L5:950753:LEVEL 5 --floor L10:950770:LEVEL 10 [--share-tol 30]

Each --floor is key:host level id:IFC reference level name. Needs, per floor, groups_<key>.json (classify_groups.py),
bands_<key>.json (group_bands.py), ifc_<key>.json and manifest_<key>.json in the working directory.

Naming (REP 4.2): <building>-<Group>_L<first>-L<last>, level range from the band plus the other levels where the
layout is identical (exception levels in between get their own type later); a band of one level is written _Lxx. A category that is identical on two modelled floors becomes ONE type, created on the lower floor and placed
on the other ("also_on"); its range spans both bands. Units get <building>-Unit-<nn>_...
"""
import argparse
import json
import os
import re

ap = argparse.ArgumentParser()
ap.add_argument("--building", required=True)
ap.add_argument("--floor", action="append", required=True, help="key:level_id:IFC level name")
ap.add_argument("--share-tol", type=float, default=30.0)
args = ap.parse_args()

floors = []
for f in args.floor:
    key, lid, lname = f.split(":", 2)
    floors.append({"key": key, "level_id": int(lid), "ref": lname,
                   "groups": json.load(open("groups_%s.json" % key)), "bands": json.load(open("bands_%s.json" % key)),
                   "ifc": json.load(open("ifc_%s.json" % key))})


def lnum(name):
    m = re.search(r"(\d+)\s*$", name)
    return "L%02d" % int(m.group(1)) if m else name.replace(" ", "")


def signature(fl, category):
    """Bounding boxes (z relative to level) of the IFC elements of a category, for identity checks."""
    lz = fl["ifc"]["meta"]["level_z"]
    cat_of = {}
    for kind in ("walls", "doors", "windows"):
        for k, v in fl["groups"][kind].items():
            cat_of[k.split("-")[0]] = v
    sig = []
    for r in fl["ifc"]["rows"]:
        if cat_of.get(str(r["id"])) == category:
            b = r["bb"]
            sig.append((r["cat"], r["Width"], [b[0], b[1], b[2] - lz, b[3], b[4], b[5] - lz]))
    return sig


def same(sa, sb):
    if len(sa) != len(sb):
        return False
    used = set()
    for c, w, b in sa:
        tol = args.share_tol if c == "Walls" else 400
        hit = next((j for j, (c2, w2, b2) in enumerate(sb) if j not in used and c2 == c and abs((w or 0) - (w2 or 0)) < 1
                    and all(abs(b[i] - b2[i]) < tol for i in range(6))), None)
        if hit is None:
            return False
        used.add(hit)
    return True


def label(cat):
    return cat if not cat.startswith("Unit") else cat


plan = {"levels": {}, "groups": [], "shared": []}
for fl in floors:
    # absolute paths: group_revit.py runs inside Revit, whose working directory is not this one
    plan["levels"][fl["key"]] = {"level_id": fl["level_id"], "manifest": os.path.abspath("manifest_%s.json" % fl["key"]),
                                 "groups": os.path.abspath("groups_%s.json" % fl["key"])}
cats = sorted(set(c for fl in floors for c in set(list(fl["groups"]["walls"].values()) + list(fl["groups"]["doors"].values()))))
done = set()
for i, fl in enumerate(floors):
    for c in cats:
        if (fl["key"], c) in done or not any(v == c for v in fl["groups"]["walls"].values()):
            continue
        band = fl["bands"].get(c, {"from": fl["ref"], "to": fl["ref"]})
        # the type spans every level where the layout repeats (exception levels in between get their own type)
        span = [lnum(band["from"]), lnum(band["to"])] + [lnum(x) for x in band.get("also_identical", [])]
        lo, hi = min(span), max(span)
        also = []
        for other in floors[i + 1:]:
            if (other["key"], c) not in done and same(signature(fl, c), signature(other, c)):
                ob = other["bands"].get(c, {"from": other["ref"], "to": other["ref"]})
                ospan = [lnum(ob["from"]), lnum(ob["to"])] + [lnum(x) for x in ob.get("also_identical", [])]
                hi = max([hi] + ospan)
                lo = min([lo] + ospan)
                also.append(other["key"])
                done.add((other["key"], c))
                plan["shared"].append("%s identical on %s and %s" % (c, fl["key"], other["key"]))
        rng = lo if lo == hi else "%s-%s" % (lo, hi)
        plan["groups"].append({"name": "%s-%s_%s" % (args.building, label(c), rng), "source": fl["key"], "category": c, "also_on": also})
        done.add((fl["key"], c))
json.dump(plan, open("group_plan.json", "w"), indent=1)
for g in plan["groups"]:
    print("%-28s from %-4s %-13s also on %s" % (g["name"], g["source"], g["category"], g["also_on"] or "-"))
print("shared:", plan["shared"] or "none")
