"""Summary of the extracted walls of a floor by layer / width / material / height / axis kind.

    python wall_stats.py L5
"""
import json, sys
from collections import defaultdict, Counter
from plot import Canvas, loop_pts, curve_pts
lv = sys.argv[1]
D = json.load(open("ifc_%s.json" % lv))
meta, rows = D["meta"], D["rows"]
lz = meta["level_z"]
print(meta)
# wall stats
st = defaultdict(list)
for r in rows:
    if r["cat"] != "Walls": continue
    bb = r.get("bb")
    ax = [a for a in r["axis"] if a.get("style") == "Axis"]
    npts = len(ax[0]["pts"]) if ax and "pts" in ax[0] else (-1 if ax else 0)
    key = (r["IfcPresentationLayer"], r["Width"], r["IfcMaterial"])
    st[key].append((r["id"], r["IfcSpatialContainer"], round(bb[2]-lz), round(bb[5]-lz), npts, r["Length"], len(r["cut"]), len(r["top"])))
for k in sorted(st, key=lambda k: (str(k[0]), k[1] or 0)):
    v = st[k]
    zs = Counter((x[2], x[3]) for x in v)
    ax = Counter(x[4] for x in v)
    print("%-28s w=%-6s mat=%-28s n=%3d  z(base,top)=%s  axis_npts=%s  storeys=%s" % (k[0], k[1], (k[2] or '')[:28], len(v), dict(zs.most_common(6)), dict(ax), dict(Counter(x[1] for x in v).most_common(4))))
