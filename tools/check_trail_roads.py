"""How much of each hiking route runs on roads, by OSM highway class (and which named trails use big roads)."""
import math, sys, collections
import osmium

class Rel(osmium.SimpleHandler):
    def __init__(self):
        super().__init__(); self.ways = collections.defaultdict(list); self.names = {}
    def relation(self, r):
        if r.tags.get("route") in ("hiking", "foot"):
            self.names[r.id] = r.tags.get("name") or r.tags.get("ref")
            for m in r.members:
                if m.type == "w": self.ways[m.ref].append(r.id)

class Ways(osmium.SimpleHandler):
    def __init__(self, wanted):
        super().__init__(); self.wanted = wanted; self.info = {}
    def way(self, w):
        if w.id not in self.wanted: return
        pts = [(n.location.lat, n.location.lon) for n in w.nodes if n.location.valid()]
        L = 0.0
        for (a, b), (c, d) in zip(pts, pts[1:]):
            x = math.radians(d - b) * math.cos(math.radians((a + c) / 2)); y = math.radians(c - a)
            L += 6371000 * math.hypot(x, y)
        self.info[w.id] = (w.tags.get("highway", "none"), L, w.tags.get("sidewalk"), w.tags.get("foot"))

for path in sys.argv[1:]:
    rel = Rel(); rel.apply_file(path)
    ways = Ways(set(rel.ways)); ways.apply_file(path, locations=True)
    by_class = collections.Counter(); total = 0.0
    big = collections.defaultdict(float)
    for wid, (hw, L, sw, foot) in ways.info.items():
        by_class[hw] += L; total += L
        if hw in ("motorway", "motorway_link", "trunk", "trunk_link", "primary", "primary_link"):
            for rid in rel.ways[wid]: big[(rid, hw)] += L
    print(f"\n== {path}: {total/1000:.0f} km of trail ways")
    for hw, L in by_class.most_common():
        print(f"  {hw:16s} {L/1000:8.1f} km  {100*L/total:5.1f}%")
    print("  routes using motorway/trunk/primary (m):")
    for (rid, hw), L in sorted(big.items(), key=lambda x: -x[1])[:25]:
        print(f"    {rel.names.get(rid)!s:60.60s} {hw:14s} {L:7.0f}")
