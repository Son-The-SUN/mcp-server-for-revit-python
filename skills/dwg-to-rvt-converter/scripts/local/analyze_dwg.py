"""Analyze floor plan and section: read a DWG/DXF drawing set and report its sheets, views, scales, levels and grids.

    uv run --with ezdxf --with matplotlib python analyze_dwg.py <file.dwg|dxf> [--out dwg_analysis.json] [--render]

What it finds (written to dwg_analysis.json, summary printed):
- sheets: title-block frames drawn in model space (common in VN/Asian sets: every sheet at 1:1 paper size with
  the drawings scaled into it). With no frames, the whole model space is one "sheet" (drawings at 1:1).
- views per sheet: spatial clusters of geometry with their title ("MẶT BẰNG TẦNG 1 TL 1/50"), kind
  (plan / elevation / section / roof_plan / ceiling_plan / site_plan / detail / schedule / perspective),
  storey for plans, and scale (title text, else the DIMLFAC of the view's dimensions).
- level marks: blocks whose attribute is a level ("+ 3.900", "%%p 0.000") or level texts. In elevations and
  sections each mark's height is checked against the drawing: y = y0 + value * 1000 / scale. Marks whose label
  disagrees with their position are flagged (mm error).
- levels: every level value seen, the views that show it, and a proposed Revit level list.
- grids: grid lines and bubble labels in plans (layers named like truc/grid/axis), in real mm.
- layers per view with entity counts, to pick wall/door/window/column layers for the build.
With --render: overview.png (sheets + view ids) and view_<id>.png per view (text decoded to Unicode).
"""
import argparse
import collections
import json
import math
import os
import re

import cadlib
from cadlib import ascii_fold, level_value, scale_from_text, text_of

KINDS = [  # (kind, ascii-folded keywords) - first match wins
    ("site_plan", ["TONG MAT BANG", "MAT BANG TONG THE", "SITE PLAN", "MAT BANG DINH VI"]),
    ("roof_plan", ["MAT BANG MAI", "ROOF PLAN", "MB MAI"]),
    ("ceiling_plan", ["MAT BANG TRAN", "CEILING", "RCP", "REFLECTED"]),
    ("section", ["MAT CAT", "SECTION"]),
    ("elevation", ["MAT DUNG", "ELEVATION"]),
    ("detail", ["CHI TIET", "DETAIL"]),
    ("schedule", ["THONG KE", "SCHEDULE", "BANG CUA", "BANG KE"]),
    ("perspective", ["PHOI CANH", "PERSPECTIVE", "3D VIEW"]),
    ("plan", ["MAT BANG", "FLOOR PLAN", "PLAN", "LAYOUT", "MB "]),
]
STOREYS = [  # (regex on ascii-folded title, storey key, order)
    (r"\bTANG HAM\b|\bBASEMENT\b", "B1", -1),
    (r"\bTRET\b|\bTANG 1\b|\bGROUND\b|\bLEVEL 0?1\b|\bL0?1\b", "L1", 1),
    (r"\bLUNG\b|\bMEZZ", "M", 1.5),
    (r"\bTANG 2\b|\bLAU 1\b|\bFIRST FLOOR\b|\bLEVEL 0?2\b|\bL0?2\b", "L2", 2),
    (r"\bTANG 3\b|\bLAU 2\b|\bSECOND FLOOR\b|\bLEVEL 0?3\b|\bL0?3\b", "L3", 3),
    (r"\bTANG 4\b|\bLAU 3\b|\bTHIRD FLOOR\b|\bLEVEL 0?4\b|\bL0?4\b", "L4", 4),
    (r"\bMAI\b|\bROOF\b|\bSAN THUONG\b", "ROOF", 99),
]
GRID_LAYER = re.compile(r"TRUC|GRID|AXIS|A-GRID|S-GRID", re.I)
FRAME_LAYER = re.compile(r"KHUNG|FRAME|BORDER|TITLE|TB$", re.I)
TB_KEYS = ["TEN BAN VE", "KY HIEU BAN VE", "DRAWING TITLE", "DRAWING NO", "SHEET NO", "CONG TRINH", "CHU DAU TU", "PROJECT"]


def kind_of(title):
    t = ascii_fold(title) + " "
    for k, keys in KINDS:
        if any(key in t for key in keys):
            return k
    return None


def storey_of(title):
    t = ascii_fold(title)
    for rx, key, order in STOREYS:
        if re.search(rx, t):
            return key, order
    return None, None


# ---------------------------------------------------------------- entity index

class Item(object):
    __slots__ = ("e", "box", "kind", "text", "h", "layer")

    def __init__(self, e, box, kind, text=u"", h=0.0):
        self.e, self.box, self.kind, self.text, self.h = e, box, kind, text, h
        self.layer = e.dxf.layer


def text_box(e, text, h):
    """Estimated box of a TEXT/MTEXT from its insertion point (ezdxf's fast MTEXT box includes the column width)."""
    lines = text.split(chr(10)) or [u""]
    w = max(len(l) for l in lines) * 0.62 * h
    ht = h * (1 + 1.6 * (len(lines) - 1))
    p = e.dxf.insert
    if e.dxftype() == "MTEXT":
        ap = e.dxf.get("attachment_point", 1)
        col, row = (ap - 1) % 3, (ap - 1) // 3          # 0 left/top .. 2 right/bottom
        x0 = p.x - w * col / 2.0
        y1 = p.y + ht * row / 2.0
        return (x0, y1 - ht, x0 + w, y1)
    ha = e.dxf.get("halign", 0)
    if ha in (1, 2, 4) and e.dxf.hasattr("align_point"):
        p = e.dxf.align_point
    x0 = p.x - (w / 2.0 if ha in (1, 4) else w if ha == 2 else 0.0)
    return (x0, p.y, x0 + w, p.y + h)


def index_msp(msp):
    items = []
    for e in msp:
        t = e.dxftype()
        if t in ("TEXT", "MTEXT"):
            h = e.dxf.char_height if t == "MTEXT" else e.dxf.height
            txt = text_of(e)
            if txt:
                items.append(Item(e, text_box(e, txt, h), "text", txt, h))
            continue
        try:
            box = cadlib.ext([e])
        except Exception:
            box = None
        if box is None:
            continue
        if t == "DIMENSION":
            items.append(Item(e, box, "dim"))
        elif t in ("LEADER", "MULTILEADER"):
            items.append(Item(e, box, "note"))
        else:
            items.append(Item(e, box, "geo"))
    return items


def title_block_top(items, sheet):
    """y of the title-block strip: a full-width horizontal line in the lower 20% of the frame, or None."""
    x0, y0, x1, y1 = sheet[:4]
    w, h = x1 - x0, y1 - y0
    best = None
    for it in items:
        if it.e.dxftype() not in ("LINE", "LWPOLYLINE"):
            continue
        b = it.box
        if b[3] - b[1] < 1e-6 and b[2] - b[0] > 0.9 * w and y0 + 1e-6 < b[1] < y0 + 0.2 * h:
            best = b[1] if best is None else max(best, b[1])
    if best is None:  # fall back on the title-block keywords
        ys = [it.box[3] for it in items if it.kind == "text" and any(k in ascii_fold(it.text) for k in TB_KEYS)
              and it.box[1] < y0 + 0.2 * h]
        if ys:
            best = max(ys) + 0.01 * h
    return best


# ---------------------------------------------------------------- clustering into views

def cluster(items, box, cells=320, gap_frac=0.012):
    x0, y0, x1, y1 = box
    cs = max(x1 - x0, y1 - y0) / float(cells)
    nx, ny = int((x1 - x0) / cs) + 1, int((y1 - y0) / cs) + 1
    g = int(math.ceil(gap_frac * max(x1 - x0, y1 - y0) / cs))
    grid = {}
    owner = []
    for k, it in enumerate(items):
        b = it.box
        i0, j0 = max(0, int((b[0] - x0) / cs) - g), max(0, int((b[1] - y0) / cs) - g)
        i1, j1 = min(nx - 1, int((b[2] - x0) / cs) + g), min(ny - 1, int((b[3] - y0) / cs) + g)
        owner.append((i0, j0, i1, j1))
    # union-find over items sharing dilated cells
    parent = list(range(len(items)))

    def find(a):
        while parent[a] != a:
            parent[a] = parent[parent[a]]
            a = parent[a]
        return a

    for k, (i0, j0, i1, j1) in enumerate(owner):
        if (i1 - i0 + 1) * (j1 - j0 + 1) > 40000:
            continue  # very large entity (hatch over the whole sheet...) doesn't link clusters
        for i in range(i0, i1 + 1):
            for j in range(j0, j1 + 1):
                o = grid.get((i, j))
                if o is None:
                    grid[(i, j)] = k
                else:
                    ra, rb = find(o), find(k)
                    if ra != rb:
                        parent[ra] = rb
    groups = collections.defaultdict(list)
    for k in range(len(items)):
        groups[find(k)].append(items[k])
    return list(groups.values())


def merge_titles(groups):
    """A view title often stands apart from its drawing. Clusters that are only a title (few non-text items)
    join the nearest drawing cluster, preferring one above the title."""
    def is_title(g):
        return sum(1 for i in g if i.kind != "text") <= 5 and any(i.kind == "text" and kind_of(i.text) for i in g)
    titles = [g for g in groups if is_title(g)]
    rest = [g for g in groups if not is_title(g)]
    for t in titles:
        tb = gbox(t)
        best, bd = None, None
        for g in rest:
            b = gbox(g)
            dx = max(0.0, b[0] - tb[2], tb[0] - b[2])
            dy = max(0.0, b[1] - tb[3], tb[1] - b[3])
            d = math.hypot(dx, dy) * (1.0 if b[1] >= tb[1] else 3.0)   # drawings sit above their titles
            if bd is None or d < bd:
                best, bd = g, d
        if best is not None:
            best.extend(t)
        else:
            rest.append(t)
    return rest


def gbox(its):
    return (min(i.box[0] for i in its), min(i.box[1] for i in its), max(i.box[2] for i in its), max(i.box[3] for i in its))


# ---------------------------------------------------------------- level marks

def level_marks(items):
    marks = []
    for it in items:
        e = it.e
        if e.dxftype() == "INSERT" and e.attribs:
            for a in e.attribs:
                raw = a.dxf.text
                v = level_value(raw)
                if v is not None:
                    p = e.dxf.insert
                    marks.append({"label": cadlib.decode(raw.replace("%%p", u"±").replace("%%P", u"±")), "value": v,
                                  "x": p.x, "y": p.y, "src": "block:" + e.dxf.name})
                    break
            else:
                txt = [cadlib.decode(a.dxf.text) for a in e.attribs]
                if txt:
                    p = e.dxf.insert
                    marks.append({"label": u" ".join(txt), "value": None, "x": p.x, "y": p.y, "src": "block:" + e.dxf.name})
        elif it.kind == "text":
            v = level_value(it.text)
            if v is not None:
                p = e.dxf.insert
                marks.append({"label": it.text, "value": v, "x": p.x, "y": p.y, "src": "text", "h": it.h})
    # text marks: snap y to the nearest horizontal line just below the text
    hl = [it.box for it in items if it.e.dxftype() in ("LINE", "LWPOLYLINE") and it.box[3] - it.box[1] < 1e-6]
    for m in marks:
        if m["src"] == "text":
            best = None
            for b in hl:
                if b[0] - 1 <= m["x"] <= b[2] + 1 and m["y"] - 2.5 * m["h"] <= b[1] <= m["y"] + 0.2 * m["h"]:
                    if best is None or abs(b[1] - m["y"]) < abs(best - m["y"]):
                        best = b[1]
            if best is not None:
                m["y"] = best
    return marks


def _fit(vm, scale):
    k = 1000.0 / scale
    y0s = [m["y"] - m["value"] * k for m in vm]
    best = max(y0s, key=lambda y: sum(1 for z in y0s if abs(z - y) <= 0.5))
    agree = [z for z in y0s if abs(z - best) <= 0.5]
    y0 = sum(agree) / len(agree)
    return y0, [((m["y"] - y0) / k - m["value"]) * 1000.0 for m in vm]


def fit_levels(marks, scale):
    """y = y0 + value * 1000/scale. Returns (y0, scale, residuals in mm) with robust y0 (median)."""
    vm = [m for m in marks if m["value"] is not None]
    if not vm:
        return None, scale
    if not scale:
        # estimate from pairs of distinct values
        ks = []
        for i in range(len(vm)):
            for j in range(i + 1, len(vm)):
                dv = vm[j]["value"] - vm[i]["value"]
                if abs(dv) > 0.5:
                    ks.append((vm[j]["y"] - vm[i]["y"]) / (dv * 1000.0))
        if ks:
            ks.sort()
            k = ks[len(ks) // 2]
            if k > 0:
                scale = round(1.0 / k)
    if not scale:
        return None, scale
    y0, res = _fit(vm, scale)
    if len(vm) >= 2 and sum(1 for r in res if abs(r) > 20) * 2 > len(vm):
        # most labels disagree: try the scale the marks themselves give
        ks = sorted((b["y"] - a["y"]) / ((b["value"] - a["value"]) * 1000.0)
                    for i, a in enumerate(vm) for b in vm[i + 1:] if abs(b["value"] - a["value"]) > 0.5)
        if ks and ks[len(ks) // 2] > 0:
            s2 = round(1.0 / ks[len(ks) // 2], 1)
            y2, res2 = _fit(vm, s2)
            if sum(1 for r in res2 if abs(r) > 20) < sum(1 for r in res if abs(r) > 20):
                for m in marks:
                    m["scale_note"] = "marks fit 1:{:g}, not 1:{:g}".format(s2, scale)
                scale = s2
    k = 1000.0 / scale
    y0s = sorted(m["y"] - m["value"] * k for m in vm)
    # the datum most marks agree with (mode within 1 unit), not the plain median
    best = max(y0s, key=lambda y: sum(1 for z in y0s if abs(z - y) <= 0.5))
    agree = [z for z in y0s if abs(z - best) <= 0.5]
    y0 = sum(agree) / len(agree)
    for m in marks:
        m["z_geo"] = round((m["y"] - y0) / k, 3)
        if m["value"] is not None:
            m["err_mm"] = round((m["z_geo"] - m["value"]) * 1000.0)
    return y0, scale


# ---------------------------------------------------------------- grids

def grids(items, view_box):
    lines, labels = [], []
    for it in items:
        if not GRID_LAYER.search(it.layer):
            continue
        e = it.e
        t = e.dxftype()
        if t == "LINE":
            lines.append(((e.dxf.start.x, e.dxf.start.y), (e.dxf.end.x, e.dxf.end.y)))
        elif t == "LWPOLYLINE" and len(e) == 2 and not e.closed:
            p = list(e.get_points("xy"))
            lines.append((p[0], p[1]))
        elif t == "INSERT":
            txt = [cadlib.decode(a.dxf.text) for a in e.attribs if a.dxf.text.strip()]
            p = e.dxf.insert
            if txt:
                labels.append((txt[0], p.x, p.y))
        elif it.kind == "text" and len(it.text) <= 3:
            p = e.dxf.insert
            labels.append((it.text, p.x, p.y))
    # also plain texts of 1-3 chars inside circles on any layer
    circles = [it for it in items if it.e.dxftype() == "CIRCLE"]
    for it in items:
        if it.kind == "text" and 0 < len(it.text) <= 3 and not GRID_LAYER.search(it.layer):
            p = it.e.dxf.insert
            for c in circles:
                cc, r = c.e.dxf.center, c.e.dxf.radius
                if math.hypot(cc.x - p.x, cc.y - p.y) < r * 1.2 and r > it.h:
                    labels.append((it.text, cc.x, cc.y))
                    break
    return lines, labels


# ---------------------------------------------------------------- main

def analyze(doc, src):
    msp = doc.modelspace()
    items = index_msp(msp)
    sheets = cadlib.find_sheets(msp)
    if not sheets:
        b = gbox(items)
        sheets = [b + ("",)]
    out = {"source": src, "insunits": doc.header.get("$INSUNITS"), "sheets": [], "levels": [], "warnings": []}
    for si, sh in enumerate(sheets):
        sbox = sh[:4]
        its = [it for it in items if (cadlib.pt_in((it.e.dxf.insert.x, it.e.dxf.insert.y), sbox) if it.kind == "text"
                                      else cadlib.inside(it.box, sbox, tol=0.5))]
        tb = title_block_top(its, sbox)
        def below_tb(it):
            return tb is not None and (it.e.dxf.insert.y if it.kind == "text" else it.box[3]) <= tb + 0.5
        body = [it for it in its if not below_tb(it) and not (FRAME_LAYER.search(it.layer) and it.kind == "geo")]
        tbits = [it for it in its if it.kind == "text" and below_tb(it)]
        sheet = {"id": "S%02d" % si, "box": [round(v, 2) for v in sbox], "title_block_top": tb, "views": []}
        # sheet name / number from the title block
        big = sorted(tbits, key=lambda it: -it.h)
        num = [it.text for it in tbits if re.match(r"^[A-Z]{1,4}[- .]?\d{1,4}[A-Z]?$", it.text)]
        if num:
            sheet["number"] = num[0]
        names = [it.text for it in big if kind_of(it.text) and not any(k in ascii_fold(it.text) for k in TB_KEYS)]
        if names:
            sheet["name"] = names[0]
        groups = merge_titles(cluster(body, sbox))
        groups = [g for g in groups if sum(1 for i in g if i.kind != "text") >= 3]
        groups.sort(key=lambda g: -len(g))
        if groups:
            biggest = len(groups[0])
            groups = [g for g in groups if len(g) >= 0.02 * biggest or any(i.kind == "text" and kind_of(i.text) for i in g)]
        vi = 0
        for g in sorted(groups, key=lambda g: (-round(gbox(g)[3] / 20.0), gbox(g)[0])):
            vb = gbox(g)
            texts = [it for it in g if it.kind == "text"]
            titles = sorted([it for it in texts if kind_of(it.text)], key=lambda it: -it.h)
            title = titles[0].text if titles else u""
            kind = kind_of(title) if title else None
            scale = scale_from_text(title) if title else None
            if not scale and titles:
                # "TL 1/50" often sits in its own text just below the title
                tt = titles[0]
                for it in texts:
                    s = scale_from_text(it.text)
                    if s and abs(it.box[0] - tt.box[0]) < 20 * tt.h and tt.box[1] - 3 * tt.h <= it.box[3] <= tt.box[1] + 0.5 * tt.h:
                        scale = s
                        break
            scale_src = "title" if scale else None
            dl = collections.Counter()
            for it in g:
                if it.kind == "dim":
                    try:
                        f = it.e.override().get("dimlfac", 1.0) or 1.0
                    except Exception:
                        f = 1.0
                    dl[round(f, 3)] += 1
            dim_scale = dl.most_common(1)[0][0] if dl else None
            if not scale and dim_scale and dim_scale != 1.0:
                scale, scale_src = dim_scale, "dims"
            marks = level_marks(g)
            y0 = None
            if kind in ("elevation", "section") or (not kind and len([m for m in marks if m["value"] is not None]) >= 2):
                y0, s2 = fit_levels(marks, scale)
                if not scale and s2:
                    scale, scale_src = s2, "levels"
                if not kind and y0 is not None:
                    kind = "elevation"
            if not kind:
                kind = "unknown"
            view = {"id": "%s-V%d" % (sheet["id"], vi), "box": [round(v, 2) for v in vb], "title": title, "kind": kind,
                    "scale": scale, "scale_src": scale_src, "dim_scale": dim_scale, "n": len(g)}
            if kind in ("plan", "roof_plan", "ceiling_plan"):
                st, order = storey_of(title)
                if st:
                    view["storey"], view["storey_order"] = st, order
            if marks and marks[0].get("scale_note"):
                out["warnings"].append(u"{} ({}): {} - the level marks are drawn at a different scale than the title".format(
                    view["id"], title.replace(chr(10), " "), marks[0]["scale_note"]))
                view["scale_levels"] = float(marks[0]["scale_note"].split(":")[1].split(",")[0])
            if marks:
                view["level_marks"] = [{k: (round(v, 3) if isinstance(v, float) else v) for k, v in m.items() if k != "h"} for m in marks]
                if y0 is not None:
                    view["datum_y"] = round(y0, 3)
                    for m in marks:
                        if m.get("err_mm") is not None and abs(m["err_mm"]) > 20:
                            out["warnings"].append(u"{} ({}): level mark '{}' sits at {:+.3f} by the drawing ({:+d} mm)".format(
                                view["id"], title, m["label"], m["z_geo"], int(m["err_mm"])))
            lay = collections.Counter(it.layer for it in g if it.kind == "geo")
            view["layers"] = dict(lay.most_common(25))
            if kind in ("plan", "roof_plan", "ceiling_plan", "site_plan"):
                gl, lab = grids(g, vb)
                if gl:
                    view["grid_lines"] = len(gl)
                    view["grid_labels"] = sorted(set(l[0] for l in lab))
            sheet["views"].append(view)
            vi += 1
        out["sheets"].append(sheet)

    # ------------------------------------------------ levels summary
    vals = collections.defaultdict(lambda: {"labels": set(), "views": set(), "z_geo": []})
    for sh in out["sheets"]:
        for v in sh["views"]:
            for m in v.get("level_marks", []):
                if m.get("value") is None:
                    continue
                key = round(m["value"], 3)
                d = vals[key]
                d["labels"].add(m["label"])
                d["views"].add("{} {}".format(v["id"], v["kind"]))
                if "z_geo" in m:
                    d["z_geo"].append(m["z_geo"])
    for key in sorted(vals):
        d = vals[key]
        zg = sorted(d["z_geo"])
        out["levels"].append({"value": key, "labels": sorted(d["labels"]), "views": sorted(d["views"]),
                              "z_geo": zg[len(zg) // 2] if zg else None,
                              "consistent": all(abs(z - key) < 0.02 for z in zg) if zg else None})
    # storeys from plans
    st = {}
    for sh in out["sheets"]:
        for v in sh["views"]:
            if v.get("storey"):
                s = st.setdefault(v["storey"], {"order": v["storey_order"], "plans": [], "marks": collections.Counter()})
                s["plans"].append(u"{} {}".format(v["id"], v["title"]))
                for m in v.get("level_marks", []):
                    if m.get("value") is not None:
                        s["marks"][round(m["value"], 3)] += 1
    out["storeys"] = [{"storey": k, "order": s["order"], "plans": s["plans"], "plan_level_marks": dict(s["marks"])}
                      for k, s in sorted(st.items(), key=lambda kv: kv[1]["order"])]
    return out


def render(doc, out, folder):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from ezdxf.addons.drawing import Frontend, RenderContext
    from ezdxf.addons.drawing.config import BackgroundPolicy, ColorPolicy, Configuration
    from ezdxf.addons.drawing.matplotlib import MatplotlibBackend

    decode_doc(doc)
    msp = doc.modelspace()
    cfg = Configuration(background_policy=BackgroundPolicy.WHITE, color_policy=ColorPolicy.BLACK)
    fig = plt.figure(figsize=(16, 11), dpi=110)
    ax = fig.add_axes([0, 0, 1, 1])
    Frontend(RenderContext(doc), MatplotlibBackend(ax), config=cfg).draw_layout(msp, finalize=False)
    ax.set_aspect("equal")
    ax.axis("off")
    for sh in out["sheets"]:
        for v in sh["views"]:
            x0, y0, x1, y1 = v["box"]
            m = 0.03 * max(x1 - x0, y1 - y0)
            ax.set_xlim(x0 - m, x1 + m)
            ax.set_ylim(y0 - m, y1 + m)
            fig.savefig(os.path.join(folder, "view_%s.png" % v["id"]), facecolor="white")
    # overview with ids
    xs = [s["box"] for s in out["sheets"]]
    X0, Y0 = min(b[0] for b in xs), min(b[1] for b in xs)
    X1, Y1 = max(b[2] for b in xs), max(b[3] for b in xs)
    ax.set_xlim(X0, X1)
    ax.set_ylim(Y0, Y1)
    import matplotlib.patches as mp
    for sh in out["sheets"]:
        x0, y0, x1, y1 = sh["box"]
        ax.add_patch(mp.Rectangle((x0, y0), x1 - x0, y1 - y0, fill=False, ec="blue", lw=1.5))
        ax.text(x0, y1, sh["id"], color="blue", fontsize=10, va="bottom")
        for v in sh["views"]:
            a, b, c, d = v["box"]
            ax.add_patch(mp.Rectangle((a, b), c - a, d - b, fill=False, ec="red", lw=0.8))
            ax.text(a, d, "{} {}".format(v["id"].split("-")[1], v["kind"]), color="red", fontsize=7, va="bottom")
    fig.savefig(os.path.join(folder, "overview.png"), facecolor="white")


def decode_doc(doc):
    """Rewrite VNI text in the document as Unicode with an Arial style, so renders and Revit imports are readable."""
    if "UNICODE_ARIAL" not in doc.styles:
        doc.styles.new("UNICODE_ARIAL", dxfattribs={"font": "arial.ttf"})
    targets = [doc.modelspace()] + [b for b in doc.blocks]
    n = 0
    for lay in targets:
        for e in lay:
            t = e.dxftype()
            if t == "MTEXT":
                raw = e.text
                if cadlib.looks_vni(raw) or "VNI" in raw.upper():
                    e.text = text_of(e).replace("\n", "\\P")
                    e.dxf.style = "UNICODE_ARIAL"
                    n += 1
            elif t in ("TEXT", "ATTDEF"):
                if cadlib.looks_vni(e.dxf.text):
                    e.dxf.text = cadlib.decode(e.dxf.text)
                    e.dxf.style = "UNICODE_ARIAL"
                    n += 1
            if t == "INSERT":
                for a in e.attribs:
                    if cadlib.looks_vni(a.dxf.text):
                        a.dxf.text = cadlib.decode(a.dxf.text)
                        a.dxf.style = "UNICODE_ARIAL"
                        n += 1
    return n


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("src")
    ap.add_argument("--out", default="dwg_analysis.json")
    ap.add_argument("--render", action="store_true")
    a = ap.parse_args()
    doc = cadlib.load(a.src)
    res = analyze(doc, os.path.abspath(a.src))
    with open(a.out, "w", encoding="utf-8") as f:
        json.dump(res, f, ensure_ascii=False, indent=1)
    # summary
    print(u"{} sheets, INSUNITS {}".format(len(res["sheets"]), res["insunits"]))
    for sh in res["sheets"]:
        print(u"{} {} {}".format(sh["id"], sh.get("number", ""), sh.get("name", "")))
        for v in sh["views"]:
            extra = u""
            if v.get("storey"):
                extra += u" storey=" + v["storey"]
            if v.get("level_marks"):
                extra += u" marks=" + u", ".join(u"{}{}".format(m["label"], u"" if abs(m.get("err_mm") or 0) <= 20 else u"(!{:+.3f})".format(m["z_geo"]))
                                                for m in v["level_marks"] if m.get("value") is not None)
            if v.get("grid_labels"):
                extra += u" grids=" + u",".join(v["grid_labels"])
            print(u"  {} {:<12} 1:{:<4} ({}) n={:<5} {}{}".format(v["id"], v["kind"], v["scale"], v["scale_src"], v["n"], v["title"][:60], extra))
    print(u"levels:")
    for l in res["levels"]:
        print(u"  {:+.3f}  {}  geo={}  in {} views".format(l["value"], u"/".join(l["labels"]), l["z_geo"], len(l["views"])))
    print(u"storeys: " + u"; ".join(u"{} {}".format(s["storey"], s["plan_level_marks"]) for s in res["storeys"]))
    for w in res["warnings"]:
        print(u"WARNING " + w)
    if a.render:
        render(doc, res, os.path.dirname(os.path.abspath(a.out)))
        print("rendered overview.png + view_<id>.png")


if __name__ == "__main__":
    main()
