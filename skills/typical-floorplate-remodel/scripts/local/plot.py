"""Tiny dependency-free PNG plotter (lines, polygons, digit labels) for plan analysis."""
import struct
import zlib

FONT = {  # 3x5 bitmap font
    '0': ["111", "101", "101", "101", "111"], '1': ["010", "110", "010", "010", "111"],
    '2': ["111", "001", "111", "100", "111"], '3': ["111", "001", "111", "001", "111"],
    '4': ["101", "101", "111", "001", "001"], '5': ["111", "100", "111", "001", "111"],
    '6': ["111", "100", "111", "101", "111"], '7': ["111", "001", "010", "010", "010"],
    '8': ["111", "101", "111", "101", "111"], '9': ["111", "101", "111", "001", "111"],
    '-': ["000", "000", "111", "000", "000"], '.': ["000", "000", "000", "000", "010"],
    'A': ["010", "101", "111", "101", "101"], 'B': ["110", "101", "110", "101", "110"],
    'C': ["011", "100", "100", "100", "011"], 'D': ["110", "101", "101", "101", "110"],
    'E': ["111", "100", "110", "100", "111"], 'F': ["111", "100", "110", "100", "100"],
    'G': ["011", "100", "101", "101", "011"], 'H': ["101", "101", "111", "101", "101"],
    'I': ["111", "010", "010", "010", "111"], 'L': ["100", "100", "100", "100", "111"],
    'M': ["101", "111", "111", "101", "101"], 'N': ["110", "101", "101", "101", "101"],
    'O': ["010", "101", "101", "101", "010"], 'P': ["110", "101", "110", "100", "100"],
    'R': ["110", "101", "110", "101", "101"], 'S': ["011", "100", "010", "001", "110"],
    'T': ["111", "010", "010", "010", "010"], 'U': ["101", "101", "101", "101", "111"],
    'W': ["101", "101", "111", "111", "101"], 'X': ["101", "101", "010", "101", "101"],
    'Y': ["101", "101", "010", "010", "010"], 'K': ["101", "110", "100", "110", "101"],
    'V': ["101", "101", "101", "101", "010"], ' ': ["000"] * 5, '/': ["001", "001", "010", "100", "100"],
}

COLORS = {
    "black": (0, 0, 0), "red": (220, 30, 30), "blue": (30, 60, 220), "green": (20, 150, 40),
    "orange": (240, 140, 0), "purple": (150, 40, 180), "grey": (160, 160, 160), "cyan": (0, 170, 190),
    "magenta": (220, 0, 160), "brown": (140, 80, 20), "lightgrey": (215, 215, 215), "olive": (120, 120, 0),
    "pink": (255, 150, 180), "navy": (0, 0, 120), "teal": (0, 120, 120), "gold": (200, 160, 0),
}


class Canvas:
    def __init__(self, bounds, px_per_mm=None, width=None, margin=20):
        x0, y0, x1, y1 = bounds
        if px_per_mm is None:
            px_per_mm = (width - 2 * margin) / float(x1 - x0)
        self.s = px_per_mm
        self.x0, self.y0, self.x1, self.y1 = x0, y0, x1, y1
        self.m = margin
        self.w = int((x1 - x0) * self.s) + 2 * margin
        self.h = int((y1 - y0) * self.s) + 2 * margin
        self.buf = bytearray([255] * (self.w * self.h * 3))

    def px(self, x, y):
        return (int(round((x - self.x0) * self.s)) + self.m, int(round((self.y1 - y) * self.s)) + self.m)

    def set(self, i, j, c):
        if 0 <= i < self.w and 0 <= j < self.h:
            k = (j * self.w + i) * 3
            self.buf[k:k + 3] = bytes(c)

    def line_px(self, a, b, c, t=1):
        (x0, y0), (x1, y1) = a, b
        dx, dy = abs(x1 - x0), -abs(y1 - y0)
        sx, sy = (1 if x0 < x1 else -1), (1 if y0 < y1 else -1)
        err = dx + dy
        n = 0
        while True:
            for u in range(-(t // 2), t - t // 2):
                for v in range(-(t // 2), t - t // 2):
                    self.set(x0 + u, y0 + v, c)
            if x0 == x1 and y0 == y1:
                break
            e2 = 2 * err
            if e2 >= dy:
                err += dy
                x0 += sx
            if e2 <= dx:
                err += dx
                y0 += sy
            n += 1
            if n > 200000:
                break

    def line(self, p, q, color="black", t=1):
        c = COLORS.get(color, color) if isinstance(color, str) else color
        self.line_px(self.px(p[0], p[1]), self.px(q[0], q[1]), c, t)

    def poly(self, pts, color="black", t=1, closed=False):
        for i in range(len(pts) - 1):
            self.line(pts[i], pts[i + 1], color, t)
        if closed and len(pts) > 2:
            self.line(pts[-1], pts[0], color, t)

    def fill(self, pts, color="lightgrey"):
        c = COLORS.get(color, color) if isinstance(color, str) else color
        P = [self.px(p[0], p[1]) for p in pts]
        if len(P) < 3:
            return
        ys = [p[1] for p in P]
        for y in range(max(min(ys), 0), min(max(ys), self.h - 1) + 1):
            xs = []
            for i in range(len(P)):
                (xa, ya), (xb, yb) = P[i], P[(i + 1) % len(P)]
                if (ya <= y < yb) or (yb <= y < ya):
                    xs.append(xa + (y - ya) * (xb - xa) / float(yb - ya))
            xs.sort()
            for k in range(0, len(xs) - 1, 2):
                for x in range(int(xs[k]), int(xs[k + 1]) + 1):
                    self.set(x, y, c)

    def text(self, x, y, s, color="red", scale=2, world=True):
        c = COLORS.get(color, color) if isinstance(color, str) else color
        i0, j0 = self.px(x, y) if world else (x, y)
        for n, ch in enumerate(str(s).upper()):
            g = FONT.get(ch, FONT[' '])
            for r, row in enumerate(g):
                for k, bit in enumerate(row):
                    if bit == "1":
                        for u in range(scale):
                            for v in range(scale):
                                self.set(i0 + n * 4 * scale + k * scale + u, j0 + r * scale + v, c)

    def save(self, path):
        raw = b"".join(b"\x00" + bytes(self.buf[j * self.w * 3:(j + 1) * self.w * 3]) for j in range(self.h))

        def chunk(tag, data):
            return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xffffffff)
        png = b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", self.w, self.h, 8, 2, 0, 0, 0))
        png += chunk(b"IDAT", zlib.compress(raw, 6)) + chunk(b"IEND", b"")
        with open(path, "wb") as f:
            f.write(png)


def curve_pts(rec, n=24):
    """Points of a curve record from extract_view.py ({'pts':...} or {'arc': [c, r, p0, p1, pm]})."""
    import math
    if "pts" in rec:
        return [(p[0], p[1]) for p in rec["pts"]]
    c, r, p0, p1, pm = rec["arc"]
    a0 = math.atan2(p0[1] - c[1], p0[0] - c[0])
    a1 = math.atan2(p1[1] - c[1], p1[0] - c[0])
    am = math.atan2(pm[1] - c[1], pm[0] - c[0])
    # choose direction that passes through am
    def norm(a):
        while a < 0:
            a += 2 * math.pi
        while a >= 2 * math.pi:
            a -= 2 * math.pi
        return a
    ccw_span = norm(a1 - a0)
    if norm(am - a0) <= ccw_span:
        span = ccw_span
    else:
        span = -(2 * math.pi - ccw_span)
    return [(c[0] + r * math.cos(a0 + span * i / float(n)), c[1] + r * math.sin(a0 + span * i / float(n))) for i in range(n + 1)]


def loop_pts(loop):
    pts = []
    for rec in loop:
        cp = curve_pts(rec)
        if pts and cp and abs(pts[-1][0] - cp[0][0]) < 1 and abs(pts[-1][1] - cp[0][1]) < 1:
            cp = cp[1:]
        pts.extend(cp)
    return pts
