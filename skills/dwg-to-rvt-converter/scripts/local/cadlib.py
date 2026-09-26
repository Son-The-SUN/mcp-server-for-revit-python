"""Shared helpers for reading architectural DWG/DXF sets with ezdxf (CPython 3).

Run the scripts that import this with `uv run --with ezdxf [--with matplotlib] python <script>.py ...`,
so nothing has to be installed globally.

- load(path): read a DXF (a DWG is converted first with dwg_to_dxf.py / AutoCAD accoreconsole)
- decode(s): legacy Vietnamese VNI text (VNI-Helve / VNI-Times fonts) -> Unicode, other text unchanged
- text_of(e): decoded plain text of TEXT / MTEXT / ATTRIB
- find_sheets(msp): title-block frames drawn in model space (sheets at 1:1 paper size, views scaled into them)
- level_value(s): "+ 3.900", "%%p 0.000", "RL 12.345", "FFL 3900" -> metres (float) or None
"""
import math
import os
import re
import subprocess
import sys
import unicodedata

import ezdxf
from ezdxf import bbox as ezbbox

HERE = os.path.dirname(os.path.abspath(__file__))


# ---------------------------------------------------------------- loading

def load(path, out_dir=None):
    """Read a DXF. For a DWG, convert it next to out_dir (default: cwd) first, reusing a newer DXF."""
    if path.lower().endswith(".dwg"):
        out_dir = out_dir or os.getcwd()
        dxf = os.path.join(out_dir, os.path.splitext(os.path.basename(path))[0] + ".dxf")
        if not (os.path.isfile(dxf) and os.path.getmtime(dxf) >= os.path.getmtime(path)):
            subprocess.check_call([sys.executable, os.path.join(HERE, "dwg_to_dxf.py"), path, "--out", out_dir])
        path = dxf
    return ezdxf.readfile(path)


# ---------------------------------------------------------------- VNI (Vietnamese legacy encoding)

_ACUTE, _GRAVE, _HOOK, _TILDE, _DOT = u"́", u"̀", u"̉", u"̃", u"̣"
_CIRC, _BREVE = u"̂", u"̆"
# mark characters that follow a vowel; same meaning in lower and upper case
_VNI_MARKS = {
    u"ù": _ACUTE, u"ø": _GRAVE, u"û": _HOOK, u"õ": _TILDE, u"ï": _DOT,
    u"â": _CIRC, u"ê": _BREVE,
    u"á": _CIRC + _ACUTE, u"à": _CIRC + _GRAVE, u"å": _CIRC + _HOOK, u"ã": _CIRC + _TILDE, u"ä": _CIRC + _DOT,
    u"é": _BREVE + _ACUTE, u"è": _BREVE + _GRAVE, u"ú": _BREVE + _HOOK, u"ü": _BREVE + _TILDE, u"ë": _BREVE + _DOT,
}
_VNI_MARKS.update({k.upper(): v for k, v in list(_VNI_MARKS.items())})
# stand-alone letters
_VNI_LETTERS = {
    u"ô": u"ơ", u"ö": u"ư", u"ñ": u"đ", u"í": u"í", u"ì": u"ì", u"æ": u"ỉ", u"ó": u"ĩ", u"ò": u"ị",
}
_VNI_LETTERS.update({k.upper(): v.upper() for k, v in list(_VNI_LETTERS.items())})
_VOWELS = set(u"aeiouyAEIOUYơưƠƯ")
_BREVE_ONLY = set(u"êÊéÉèÈúÚüÜëË")          # only valid after a/A
_CIRC_BASE = set(u"aeoAEO")                  # â-type marks only after a/e/o
_VNI_HINT = re.compile(u"[aeouyAEOUY][ùøûõïâêáàåãäéèúüëÙØÛÕÏÂÊÁÀÅÃÄÉÈÚÜË]|[ñÑöÖ]")


def looks_vni(s):
    return bool(s) and bool(_VNI_HINT.search(s))


def decode(s, force=False):
    """VNI -> Unicode (NFC). Leaves text without VNI patterns alone unless force=True."""
    if not s or not (force or looks_vni(s)):
        return s
    out = []
    for ch in s:
        prev = out[-1] if out else u""
        base = prev[:1]
        if ch in _VNI_MARKS and base in _VOWELS and len(prev) == 1 or (ch in _VNI_MARKS and prev in (u"ơ", u"ư", u"Ơ", u"Ư")):
            m = _VNI_MARKS[ch]
            if (ch in _BREVE_ONLY and base not in u"aA") or (m[0] == _CIRC and base not in _CIRC_BASE):
                out.append(ch)  # not a valid VNI combination: keep the character
                continue
            out[-1] = prev + m
        elif ch in _VNI_LETTERS:
            out.append(_VNI_LETTERS[ch])
        else:
            out.append(ch)
    return unicodedata.normalize("NFC", u"".join(out))


def text_of(e):
    t = e.dxftype()
    if t == "MTEXT":
        s = e.plain_text()
    elif t in ("TEXT", "ATTRIB", "ATTDEF"):
        s = e.dxf.text
    else:
        return u""
    s = s.replace("%%p", u"±").replace("%%P", u"±").replace("%%c", u"Ø").replace("%%C", u"Ø").replace("%%d", u"°")
    return decode(s).strip()


def ascii_fold(s):
    """Uppercase, accents stripped, đ->D: for keyword matching ('MẶT ĐỨNG' -> 'MAT DUNG')."""
    s = s.replace(u"đ", u"d").replace(u"Đ", u"D")
    s = unicodedata.normalize("NFD", s)
    return u"".join(c for c in s if unicodedata.category(c) != "Mn").upper()


# ---------------------------------------------------------------- levels

_LEVEL_RE = re.compile(r"^\s*(?:(?:RL|FFL|SSL|TOC|TOS|EL\.?|COS|CAO ĐỘ|CAO DO)\s*[:=]?\s*)?([+\-±]?)\s*(\d{1,3}(?:[.,]\d{1,3}))\s*(?:M)?\s*$", re.I)
_LEVEL_MM_RE = re.compile(r"^\s*(?:RL|FFL|SSL)\s*[:=]?\s*([+\-]?)\s*(\d{3,6})\s*(?:MM)?\s*$", re.I)


def level_value(s):
    """Parse a level annotation to metres. Accepts '+ 3.900', '±0.000', '- 0.150', 'RL 12.345', 'FFL 3900'."""
    if not s:
        return None
    s = s.replace("%%p", u"±").replace("%%P", u"±").strip()
    m = _LEVEL_RE.match(s)
    if m:
        sign, num = m.groups()
        v = float(num.replace(",", "."))
        return -v if sign == "-" else v
    m = _LEVEL_MM_RE.match(s)
    if m:
        sign, num = m.groups()
        v = float(num) / 1000.0
        return -v if sign == "-" else v
    return None


# ---------------------------------------------------------------- geometry helpers

def ext(entities):
    b = ezbbox.extents(entities, fast=True)
    if not b.has_data:
        return None
    return (b.extmin.x, b.extmin.y, b.extmax.x, b.extmax.y)


def inside(box, outer, tol=0.0):
    return (box[0] >= outer[0] - tol and box[1] >= outer[1] - tol and
            box[2] <= outer[2] + tol and box[3] <= outer[3] + tol)


def pt_in(p, outer):
    return outer[0] <= p[0] <= outer[2] and outer[1] <= p[1] <= outer[3]


def _is_rect(pl):
    pts = [(p[0], p[1]) for p in pl.get_points("xy")]
    if len(pts) >= 5 and math.hypot(pts[0][0] - pts[-1][0], pts[0][1] - pts[-1][1]) < 1e-6:
        pts = pts[:-1]
    if len(pts) != 4 or not (pl.closed or len(pts) == 4):
        return None
    xs = sorted(set(round(p[0], 3) for p in pts))
    ys = sorted(set(round(p[1], 3) for p in pts))
    if len(xs) != 2 or len(ys) != 2:
        return None
    return (xs[0], ys[0], xs[1], ys[1])


def find_sheets(msp, min_size=200.0, ratios=(1.25, 1.55)):
    """Sheet frames drawn in model space: closed axis-aligned rectangles with a paper-like aspect ratio
    that contain drawing content and don't contain another such frame (containers are dropped).
    Returns [(x0, y0, x1, y1, layer)] sorted top-to-bottom, left-to-right."""
    rects = []
    for pl in msp.query("LWPOLYLINE"):
        r = _is_rect(pl)
        if not r:
            continue
        w, h = r[2] - r[0], r[3] - r[1]
        if min(w, h) < min_size:
            continue
        ar = max(w, h) / min(w, h)
        if ratios[0] <= ar <= ratios[1]:
            rects.append(r + (pl.dxf.layer,))
    # de-duplicate (double frames: outer trim line + inner border) -> keep the inner
    rects.sort(key=lambda r: (r[2] - r[0]) * (r[3] - r[1]))
    leaf = []
    for r in rects:
        if any(inside(o[:4], r[:4], tol=1e-6) and o[:4] != r[:4] for o in leaf):
            # r contains an already accepted frame: it is a container or an outer border
            inner = [o for o in leaf if inside(o[:4], r[:4], tol=1e-6)]
            if len(inner) == 1 and (inner[0][2] - inner[0][0]) > 0.85 * (r[2] - r[0]):
                continue  # outer border of the same sheet
            continue
        if any(o[:4] == r[:4] for o in leaf):
            continue
        leaf.append(r)
    leaf.sort(key=lambda r: (-round(r[3] / 50.0), r[0]))
    return leaf


def scale_from_text(s):
    """'TL 1/50', 'TỈ LỆ 1:100', 'SCALE 1:50', '1:20 @ A1' -> 50 / 100 / 50 / 20."""
    m = re.search(r"(?:^|[^\d])1\s*[:/]\s*(\d{1,4})(?:[^\d]|$)", s)
    return int(m.group(1)) if m else None
