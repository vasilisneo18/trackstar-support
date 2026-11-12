"""Builds static hiking-trail tiles for trackstar.fitness/trails from Geofabrik OSM extracts.

Each tile {floor(lat*10)}_{floor(lon*10)}.json = {"trails": [{id, name, ref, network, colour,
lines: [{latitudes, longitudes}]}]} — the same shape as the backend's /api/trails/tile. coverage.json
lists the tiles these extracts cover (a 404 inside coverage means "no trails here").
Data © OpenStreetMap contributors (ODbL).

usage: python build_trail_tiles.py OUT_DIR extract.osm.pbf [extract2.osm.pbf ...]
"""
import json, math, os, sys
import osmium

TILE = 0.1
SIMPLIFY_DEG = 0.00003   # ~3 m


def tile_key(lat, lon):
    return f"{math.floor(lat / TILE)}_{math.floor(lon / TILE)}"


class Relations(osmium.SimpleHandler):
    def __init__(self):
        super().__init__()
        self.trails = {}      # rel id -> dict
        self.way_ids = set()

    def relation(self, r):
        if r.tags.get("route") not in ("hiking", "foot"):
            return
        ways = [m.ref for m in r.members if m.type == "w"]
        if not ways:
            return
        tags = r.tags
        osmc = tags.get("osmc:symbol")
        colour = tags.get("colour") or (osmc.split(":")[0] if osmc and ":" in osmc and osmc.split(":")[0] else None)
        self.trails[r.id] = {"id": r.id, "name": tags.get("name"),
                             "ref": tags.get("ref"), "network": tags.get("network"), "colour": colour, "ways": ways}
        self.way_ids.update(ways)


class Ways(osmium.SimpleHandler):
    def __init__(self, wanted):
        super().__init__()
        self.wanted = wanted
        self.geom = {}
        self.coverage = set()

    def way(self, w):
        try:
            if "highway" in w.tags and len(w.nodes):
                loc = w.nodes[0].location
                if loc.valid():
                    self.coverage.add(tile_key(loc.lat, loc.lon))
            if w.id in self.wanted:
                pts = [(n.location.lat, n.location.lon) for n in w.nodes if n.location.valid()]
                if len(pts) >= 2:
                    self.geom[w.id] = pts
        except osmium.InvalidLocationError:
            pass


def rdp(points, eps):
    if len(points) < 3:
        return points
    (ax, ay), (bx, by) = points[0], points[-1]
    dx, dy = bx - ax, by - ay
    norm = math.hypot(dx, dy) or 1e-12
    best, idx = 0.0, 0
    for i in range(1, len(points) - 1):
        px, py = points[i]
        d = abs(dy * px - dx * py + bx * ay - by * ax) / norm
        if d > best:
            best, idx = d, i
    if best > eps:
        return rdp(points[: idx + 1], eps)[:-1] + rdp(points[idx:], eps)
    return [points[0], points[-1]]


def split_by_tile(points):
    """Runs of a line per tile; a run carries one point past the edge so lines stay joined."""
    runs = {}
    cur_key, cur = None, []
    for i, p in enumerate(points):
        k = tile_key(*p)
        if k != cur_key:
            if cur:
                cur.append(p)
                runs.setdefault(cur_key, []).append(cur)
            cur_key, cur = k, ([points[i - 1]] if i > 0 else [])
        cur.append(p)
    if len(cur) >= 2:
        runs.setdefault(cur_key, []).append(cur)
    return runs


def main():
    out_dir, extracts = sys.argv[1], sys.argv[2:]
    os.makedirs(out_dir, exist_ok=True)
    tiles, coverage = {}, set()
    for path in extracts:
        rel = Relations()
        rel.apply_file(path)
        ways = Ways(rel.way_ids)
        ways.apply_file(path, locations=True)
        coverage |= ways.coverage
        for t in rel.trails.values():
            for wid in t["ways"]:
                pts = ways.geom.get(wid)
                if not pts:
                    continue
                pts = rdp(pts, SIMPLIFY_DEG)
                for key, runs in split_by_tile(pts).items():
                    tile = tiles.setdefault(key, {})
                    entry = tile.setdefault(t["id"], {k: t[k] for k in ("id", "name", "ref", "network", "colour")} | {"lines": []})
                    for run in runs:
                        if len(run) >= 2:
                            entry["lines"].append({"latitudes": [round(a, 5) for a, _ in run],
                                                   "longitudes": [round(b, 5) for _, b in run]})
        print(path, len(rel.trails), "trails")
    for key, trails in tiles.items():
        with open(os.path.join(out_dir, key + ".json"), "w") as f:
            json.dump({"trails": list(trails.values())}, f, ensure_ascii=False, separators=(",", ":"))
    with open(os.path.join(out_dir, "coverage.json"), "w") as f:
        json.dump({"source": "OpenStreetMap contributors (ODbL)", "tiles": sorted(coverage | set(tiles))}, f, separators=(",", ":"))
    print(len(tiles), "tiles,", len(coverage | set(tiles)), "covered")


if __name__ == "__main__":
    main()
