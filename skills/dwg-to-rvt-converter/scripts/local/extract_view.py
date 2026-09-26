"""Import and align CAD, step 1: cut one view out of a DWG/DXF sheet set as a clean 1:1 DXF in millimetres.

    uv run --with ezdxf python extract_view.py <file.dxf> <view id> [--analysis dwg_analysis.json]
        [--origin auto|x,y] [--ref <view id>] [--scale 50] [--out <dir>] [--name GF]

- Takes the view's entities (from analyze_dwg.py's view box), scales them by the view scale (1:50 drawn on an A1
  frame -> x50) so 1 unit = 1 mm, and moves them so the chosen origin is (0, 0).
- --origin auto: plans -> lower-left corner of the wall layers (rounded to 10 mm); elevations/sections ->
  left of the geometry at y = the ±0.000 datum. Or give a point in sheet units "x,y".
- --ref <view id>: register this view onto another, already extracted plan (translation only), by voting on the
  wall-layer vertices, so every floor shares one origin. The ref's transform is read from its _xform.json.
- Dimensions and leaders are exploded (their text keeps the drawn value), VNI text is re-encoded as Unicode (Arial).
Writes <out>/<name>.dxf and <out>/<name>_xform.json ({"scale", "origin_sheet", "kind", "view"...}).
The transform maps a sheet point p to mm: (p - origin_sheet) * scale.
"""
import argparse
import collections
import json
import math
import os
import re

import ezdxf
from ezdxf.addons import Importer
from ezdxf.math import Matrix44

import analyze_dwg
import cadlib

WALL_LAYER = re.compile(r"TUONG|WALL|A-WALL|^T$|XAY", re.I)
COLUMN_LAYER = re.compile(r"\bCOT\b|COLUMN|A-COLS|S-COLS", re.I)


def view_of(analysis, vid):
    for sh in analysis["sheets"]:
        for v in sh["views"]:
            if v["id"] == vid:
                return v
    raise SystemExit("view {} not in analysis".format(vid))


def sheet_of(analysis, vid):
    for sh in analysis["sheets"]:
        if any(v["id"] == vid for v in sh["views"]):
            return sh
    return None


def view_entities(msp, box):
    out = []
    for e in msp:
        t = e.dxftype()
        if t in ("TEXT", "MTEXT"):
            p = e.dxf.insert
            if cadlib.pt_in((p.x, p.y), box):
                out.append(e)
            continue
        try:
            b = cadlib.ext([e])
        except Exception:
            b = None
        if b and cadlib.inside(b, box, tol=0.5):
            out.append(e)
    return out


def layer_key(name):
    return cadlib.ascii_fold(name)


def struct_points(entities):
    """Vertices of wall/column layer geometry (sheet units)."""
    pts = []
    for seg in struct_segments(entities):
        pts += [seg[0], seg[1]]
    return pts


def struct_segments(entities):
    """Line work of the wall/column layers as segments (arcs flattened), sheet units."""
    from ezdxf import path as ezpath
    segs = []
    for e in entities:
        if not (WALL_LAYER.search(layer_key(e.dxf.layer)) or COLUMN_LAYER.search(layer_key(e.dxf.layer))):
            continue
        if e.dxftype() not in ("LINE", "LWPOLYLINE", "POLYLINE", "ARC", "CIRCLE"):
            continue
        try:
            pts = [(v.x, v.y) for v in ezpath.make_path(e).flattening(0.05)]
        except Exception:
            continue
        segs += [(pts[i], pts[i + 1]) for i in range(len(pts) - 1)]
    return segs


def _cells(segs, cs, dil=1):
    cells = set()
    for (x0, y0), (x1, y1) in segs:
        n = max(1, int(math.hypot(x1 - x0, y1 - y0) / (cs * 0.5)))
        for k in range(n + 1):
            i, j = int(math.floor((x0 + (x1 - x0) * k / n) / cs)), int(math.floor((y0 + (y1 - y0) * k / n) / cs))
            for di in range(-dil, dil + 1):
                for dj in range(-dil, dil + 1):
                    cells.add((i + di, j + dj))
    return cells


def _samples(segs, step):
    out = []
    for (x0, y0), (x1, y1) in segs:
        n = max(1, int(math.hypot(x1 - x0, y1 - y0) / step))
        out += [(x0 + (x1 - x0) * k / n, y0 + (y1 - y0) * k / n) for k in range(n + 1)]
    return out


def overlap_score(ref_cells, samples, d, cs):
    hit = sum(1 for x, y in samples if (int(math.floor((x + d[0]) / cs)), int(math.floor((y + d[1]) / cs))) in ref_cells)
    return hit / float(len(samples) or 1)


def register(ref_segs, segs, candidates=(), cs=0.4, bin_size=0.2, top=15):
    """Translation d mapping this view onto the ref (p + d). Candidates = top vertex-vote peaks + given shifts
    (e.g. same position on the sheet frame); each is refined locally and scored by the share of this view's
    wall line work that lands on the ref's. Returns (d, score, table of candidates)."""
    ref_pts = [p for s in ref_segs for p in s]
    pts = [p for s in segs for p in s]
    votes = collections.Counter()
    step = max(1, int(math.ceil(len(ref_pts) * len(pts) / 2000000.0)))
    for rx, ry in ref_pts:
        for x, y in pts[::step]:
            votes[(round((rx - x) / bin_size), round((ry - y) / bin_size))] += 1
    cands = [("vote#%d(%d)" % (k + 1, n), (b[0] * bin_size, b[1] * bin_size)) for k, (b, n) in enumerate(votes.most_common(top))]
    cands += list(candidates)
    ref_cells = _cells(ref_segs, cs)
    samples = _samples(segs, cs)
    table = []
    for name, d in cands:
        best = (overlap_score(ref_cells, samples, d, cs), d)
        for r in (4 * cs, 2 * cs, cs, cs / 2.0):          # local pattern search
            improved = True
            while improved:
                improved = False
                for ox, oy in ((r, 0), (-r, 0), (0, r), (0, -r)):
                    dd = (best[1][0] + ox, best[1][1] + oy)
                    sc = overlap_score(ref_cells, samples, dd, cs)
                    if sc > best[0] + 1e-9:
                        best, improved = (sc, dd), True
        table.append((name, best[1], best[0]))
    table.sort(key=lambda t: -t[2])
    return table[0][1], table[0][2], table


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("src")
    ap.add_argument("view")
    ap.add_argument("--analysis", default="dwg_analysis.json")
    ap.add_argument("--origin", default="auto")
    ap.add_argument("--ref", default=None, help="name of an already extracted plan (its <name>_xform.json) to register onto")
    ap.add_argument("--shift", default=None, help="with --ref: force this sheet-unit shift dx,dy (view -> ref)")
    ap.add_argument("--scale", type=float, default=None)
    ap.add_argument("--out", default=".")
    ap.add_argument("--name", default=None)
    ap.add_argument("--keep-layers", default=None, help="regex: only these layers (ascii-folded names)")
    ap.add_argument("--drop-layers", default=None, help="regex: drop these layers (ascii-folded names)")
    a = ap.parse_args()

    with open(a.analysis, encoding="utf-8") as f:
        analysis = json.load(f)
    v = view_of(analysis, a.view)
    scale = a.scale or v.get("scale_levels") or v.get("scale")
    if not scale:
        raise SystemExit("no scale for {} - pass --scale".format(a.view))
    sdoc = cadlib.load(a.src)
    msp = sdoc.modelspace()
    ents = view_entities(msp, v["box"])
    if a.keep_layers:
        ents = [e for e in ents if re.search(a.keep_layers, layer_key(e.dxf.layer), re.I)]
    if a.drop_layers:
        ents = [e for e in ents if not re.search(a.drop_layers, layer_key(e.dxf.layer), re.I)]

    info = {"view": a.view, "title": v["title"], "kind": v["kind"], "scale": scale, "source": os.path.abspath(a.src)}
    if a.ref:
        ref_x = json.load(open(os.path.join(a.out, "{}_xform.json".format(a.ref)), encoding="utf-8"))
        ref_v = view_of(analysis, ref_x["view"])
        ref_segs = struct_segments(view_entities(msp, ref_v["box"]))
        segs = struct_segments(ents)
        if not ref_segs or not segs:
            raise SystemExit("no wall/column geometry to register with")
        cands = []
        sh, ref_sh = sheet_of(analysis, a.view), sheet_of(analysis, ref_x["view"])
        if sh and ref_sh:
            cands.append(("same place on the sheet", (ref_sh["box"][0] - sh["box"][0], ref_sh["box"][1] - sh["box"][1])))
        if a.shift:
            cands.append(("given", tuple(float(c) for c in a.shift.split(","))))
        (dx, dy), score, table = register(ref_segs, segs, cands)
        if a.shift:  # an explicit shift wins
            (dx, dy), score = [(t[1], t[2]) for t in table if t[0] == "given"][0]
        # a point p here matches p + d in the ref view; the ref origin o_ref maps to o = o_ref - d
        origin = (ref_x["origin_sheet"][0] - dx, ref_x["origin_sheet"][1] - dy)
        info["registered_to"] = a.ref
        info["registration_score"] = round(score, 3)
        info["registration_candidates"] = [[t[0], round(t[1][0] * scale), round(t[1][1] * scale), round(t[2], 3)] for t in table[:6]]
        for t in table[:6]:
            print("  candidate {:<26} shift {:>7.0f},{:>7.0f} mm  overlap {:.3f}".format(t[0], t[1][0] * scale, t[1][1] * scale, t[2]))
        if abs(scale - ref_x["scale"]) > 1e-6:
            print("WARNING: scale 1:{} differs from ref 1:{}".format(scale, ref_x["scale"]))
    elif a.origin != "auto":
        origin = tuple(float(c) for c in a.origin.split(","))
    elif v["kind"] in ("elevation", "section") and v.get("datum_y") is not None:
        geo = [e for e in ents if e.dxftype() not in ("TEXT", "MTEXT", "DIMENSION", "LEADER", "INSERT")]
        b = cadlib.ext(geo)
        origin = (math.floor(b[0]), v["datum_y"])
    else:
        pts = struct_points(ents)
        if pts:
            origin = (min(p[0] for p in pts), min(p[1] for p in pts))
        else:
            b = cadlib.ext(ents)
            origin = (b[0], b[1])
        # round so the origin is a whole 10 mm in real units
        origin = (math.floor(origin[0] * scale / 10.0) * 10.0 / scale, math.floor(origin[1] * scale / 10.0) * 10.0 / scale)
    info["origin_sheet"] = [origin[0], origin[1]]

    # ---- build the target document
    tdoc = ezdxf.new("R2018", setup=True)
    tdoc.header["$INSUNITS"] = 4
    tdoc.header["$MEASUREMENT"] = 1
    tmsp = tdoc.modelspace()
    imp = Importer(sdoc, tdoc)
    plain, exploded = [], []
    for e in ents:
        if e.dxftype() in ("DIMENSION", "LEADER", "ARC_DIMENSION", "MULTILEADER"):
            try:
                exploded += list(e.virtual_entities())
            except Exception:
                pass
        else:
            plain.append(e)
    imp.import_entities(plain, tmsp)
    for ve in exploded:
        try:
            imp.import_entity(ve, tmsp)
        except Exception:
            pass
    imp.finalize()
    m = Matrix44.translate(-origin[0], -origin[1], 0) @ Matrix44.scale(scale, scale, scale)
    bad = collections.Counter()
    for e in list(tmsp):
        try:
            e.transform(m)
        except Exception:
            bad[e.dxftype()] += 1
            tmsp.delete_entity(e)
    # block attributes -> plain TEXT: ezdxf can write mirrored ATTRIBs that AutoCAD and Revit reject
    # ("Invalid DXF data ... in ATTRIB"; Revit's Document.Import just returns False)
    n_att = 0
    for ins in list(tmsp.query("INSERT")):
        if not ins.attribs:
            continue
        for at in ins.attribs:
            if at.dxf.text.strip() and not at.is_invisible:
                da = {"layer": at.dxf.layer, "height": at.dxf.height, "rotation": at.dxf.get("rotation", 0.0),
                      "style": at.dxf.get("style", "Standard"), "insert": at.dxf.insert, "width": at.dxf.get("width", 1.0)}
                txt = tmsp.add_text(at.dxf.text, dxfattribs=da)
                h, v = at.dxf.get("halign", 0), at.dxf.get("valign", 0)
                if (h or v) and at.dxf.hasattr("align_point"):
                    txt.dxf.halign, txt.dxf.valign, txt.dxf.align_point = h, v, at.dxf.align_point
                n_att += 1
        ins.delete_all_attribs()
    n_dec = analyze_dwg.decode_doc(tdoc)
    # text styles whose TrueType font lived in XDATA arrive with an empty font file: AutoCAD rejects the
    # DXF ("Invalid DXF data ... in TEXT") and Revit's Document.Import returns False. Give them Arial.
    for st in tdoc.styles:
        if not (st.dxf.get("font", "") or "").strip() and st.dxf.name not in ("", "*"):
            st.dxf.font = "arial.ttf"
    # styles referenced but never copied (block attributes' styles): point them at Arial too
    if "UNICODE_ARIAL" not in tdoc.styles:
        tdoc.styles.new("UNICODE_ARIAL", dxfattribs={"font": "arial.ttf"})
    for lay in [tmsp] + [b for b in tdoc.blocks]:
        for e in lay:
            if e.dxf.hasattr("style") and e.dxf.style not in tdoc.styles:
                e.dxf.style = "UNICODE_ARIAL"
    name = a.name or a.view
    path = os.path.join(a.out, name + ".dxf")
    tdoc.saveas(path)
    b = cadlib.ext(tmsp)
    info.update({"dxf": os.path.abspath(path), "entities": len(tmsp), "dropped": dict(bad), "decoded_texts": n_dec, "attribs_to_text": n_att,
                 "extents_mm": [round(c) for c in b] if b else None})
    with open(os.path.join(a.out, name + "_xform.json"), "w", encoding="utf-8") as f:
        json.dump(info, f, ensure_ascii=False, indent=1)
    print(json.dumps(info, ensure_ascii=False))


if __name__ == "__main__":
    main()
