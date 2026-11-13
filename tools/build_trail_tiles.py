"""Builds static hiking-trail tiles for trackstar.fitness/trails from Geofabrik OSM extracts.

Also writes index.json (every named trail: id, names, length, network, colour, nearest place,
country, box) for the app's trail browser, and t/{id}.json with each trail's full route.

Each tile {floor(lat*10)}_{floor(lon*10)}.json = {"trails": [{id, name, ref, network, colour,
lines: [{latitudes, longitudes}]}]} — the same shape as the backend's /api/trails/tile. coverage.json
lists the tiles these extracts cover (a 404 inside coverage means "no trails here").
Safety: races / marathons and routes that are mostly main road are left out; motorway sections are
cut out (pedestrians aren't allowed there); sections on main roads (primary / secondary / trunk) are
kept but tagged "road": "main" so the apps draw them as roads and warn ("roadKm" per trail).

Data © OpenStreetMap contributors (ODbL).

usage: python build_trail_tiles.py OUT_DIR CC:extract.osm.pbf [CC:extract2.osm.pbf ...]   (CC = ISO country)
"""
import json, math, os, re, sys
import osmium

TILE = 0.1
RACE = re.compile(r"(race|marathon|μαραθ|αγών|αγων|ultra|trail run|koşu)", re.I)
MOTORWAY = {"motorway", "motorway_link"}
MAIN_ROAD = {"trunk", "trunk_link", "primary", "primary_link", "secondary", "secondary_link"}
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
        if RACE.search(r.tags.get("name", "") + " " + r.tags.get("name:en", "")):
            return   # a race course, not a hiking trail
        ways = [m.ref for m in r.members if m.type == "w"]
        if not ways:
            return
        tags = r.tags
        osmc = tags.get("osmc:symbol")
        colour = tags.get("colour") or (osmc.split(":")[0] if osmc and ":" in osmc and osmc.split(":")[0] else None)
        self.trails[r.id] = {"id": r.id, "name": tags.get("name"), "name_en": tags.get("name:en"),
                             "name_el": tags.get("name:el"),
                             "ref": tags.get("ref"), "network": tags.get("network"), "colour": colour, "ways": ways}
        self.way_ids.update(ways)


class Places(osmium.SimpleHandler):
    """Villages / towns / cities, to say where a trail is ("near Platres")."""
    def __init__(self):
        super().__init__()
        self.places = []

    def node(self, n):
        if n.tags.get("place") in ("city", "town", "village") and "name" in n.tags:
            self.places.append((n.location.lat, n.location.lon, n.tags.get("name"), n.tags.get("name:en")))


def dist_m(a, b):
    la1, lo1 = map(math.radians, a); la2, lo2 = map(math.radians, b)
    x = (lo2 - lo1) * math.cos((la1 + la2) / 2); y = la2 - la1
    return 6371000 * math.hypot(x, y)


class Ways(osmium.SimpleHandler):
    def __init__(self, wanted):
        super().__init__()
        self.wanted = wanted
        self.geom = {}
        self.main_road = set()
        self.coverage = set()

    def way(self, w):
        try:
            if "highway" in w.tags and len(w.nodes):
                loc = w.nodes[0].location
                if loc.valid():
                    self.coverage.add(tile_key(loc.lat, loc.lon))
            if w.id in self.wanted:
                if w.tags.get("highway") in MOTORWAY:
                    return   # cut out: never show a trail on a motorway
                pts = [(n.location.lat, n.location.lon) for n in w.nodes if n.location.valid()]
                if len(pts) >= 2:
                    self.geom[w.id] = pts
                    if w.tags.get("highway") in MAIN_ROAD:
                        self.main_road.add(w.id)
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
    os.makedirs(os.path.join(out_dir, "t"), exist_ok=True)
    tiles, coverage, index, routes = {}, set(), {}, {}
    for arg in extracts:
        cc, path = arg.split(":", 1)
        rel = Relations()
        rel.apply_file(path)
        ways = Ways(rel.way_ids)
        ways.apply_file(path, locations=True)
        places = Places()
        places.apply_file(path)
        coverage |= ways.coverage
        for t in rel.trails.values():
            # Index + full route for the browser (named trails only; a trail in two extracts once).
            member_ways = [w for w in t["ways"] if w in ways.geom]
            full = [ways.geom[w] for w in member_ways]
            if not full:
                continue
            length = sum(dist_m(a, b) for line in full for a, b in zip(line, line[1:]))
            road = sum(dist_m(a, b) for w in member_ways if w in ways.main_road
                       for a, b in zip(ways.geom[w], ways.geom[w][1:]))
            if length > 0 and road / length > 0.5:
                continue   # mostly main road: a road walk, not a trail
            if (t["name"] or t["ref"]) and t["id"] not in index:
                lats = [p[0] for line in full for p in line]; lons = [p[1] for line in full for p in line]
                center = ((min(lats) + max(lats)) / 2, (min(lons) + max(lons)) / 2)
                start = full[0][0]
                near = min(places.places, key=lambda p: dist_m(start, (p[0], p[1])), default=None)
                near = near if near and dist_m(start, (near[0], near[1])) < 15000 else None
                index[t["id"]] = {"id": t["id"], "n": t["name"] or t["ref"], "en": t["name_en"], "el": t["name_el"],
                                  "ref": t["ref"], "net": t["network"], "c": t["colour"], "cc": cc,
                                  "km": round(length / 1000, 1), "roadKm": round(road / 1000, 1),
                                  "lat": round(center[0], 4), "lon": round(center[1], 4),
                                  "box": [round(min(lats), 4), round(min(lons), 4), round(max(lats), 4), round(max(lons), 4)],
                                  "near": near[2] if near else None, "nearEn": near[3] if near else None}
                route = []
                for w in member_ways:
                    l = rdp(ways.geom[w], SIMPLIFY_DEG)
                    if len(l) >= 2:
                        line = {"latitudes": [round(a, 5) for a, _ in l], "longitudes": [round(b, 5) for _, b in l]}
                        if w in ways.main_road:
                            line["road"] = "main"
                        route.append(line)
                routes[t["id"]] = route
                with open(os.path.join(out_dir, "t", f"{t['id']}.json"), "w") as f:
                    json.dump({"lines": route}, f, separators=(",", ":"))
            for wid in member_ways:
                pts = ways.geom.get(wid)
                if not pts:
                    continue
                pts = rdp(pts, SIMPLIFY_DEG)
                for key, runs in split_by_tile(pts).items():
                    tile = tiles.setdefault(key, {})
                    entry = tile.setdefault(t["id"], {k: t[k] for k in ("id", "name", "ref", "network", "colour")} | {"lines": []})
                    for run in runs:
                        if len(run) >= 2:
                            line = {"latitudes": [round(a, 5) for a, _ in run], "longitudes": [round(b, 5) for _, b in run]}
                            if wid in ways.main_road:
                                line["road"] = "main"
                            entry["lines"].append(line)
        print(path, len(rel.trails), "trails")
    # One index per country (the app loads only the ones it needs) + countries.json listing them.
    # Countries already in OUT_DIR from earlier runs are kept, so countries can be added one at a time.
    os.makedirs(os.path.join(out_dir, "index"), exist_ok=True)
    by_cc = {}
    for t in index.values():
        by_cc.setdefault(t["cc"], []).append(t)
    countries_path = os.path.join(out_dir, "countries.json")
    countries = {c["cc"]: c for c in json.load(open(countries_path))["countries"]} if os.path.exists(countries_path) else {}
    os.makedirs(os.path.join(out_dir, "pack"), exist_ok=True)
    for cc, trails in by_cc.items():
        trails = sorted(trails, key=lambda x: x["n"].lower())
        with open(os.path.join(out_dir, "index", cc + ".json"), "w") as f:
            json.dump({"trails": trails}, f, ensure_ascii=False, separators=(",", ":"))
        # Offline pack: the index plus every route, one download.
        pack_path = os.path.join(out_dir, "pack", cc + ".json")
        with open(pack_path, "w") as f:
            json.dump({"trails": trails, "routes": {str(t["id"]): routes.get(t["id"], []) for t in trails}},
                      f, ensure_ascii=False, separators=(",", ":"))
        boxes = [t["box"] for t in trails]
        countries[cc] = {"cc": cc, "count": len(trails), "packBytes": os.path.getsize(pack_path),
                         "box": [min(b[0] for b in boxes), min(b[1] for b in boxes), max(b[2] for b in boxes), max(b[3] for b in boxes)]}
    with open(countries_path, "w") as f:
        json.dump({"source": "OpenStreetMap contributors (ODbL)", "countries": sorted(countries.values(), key=lambda c: c["cc"])},
                  f, separators=(",", ":"))
    print(len(index), "trails in", len(by_cc), "countries")
    for key, trails in tiles.items():
        with open(os.path.join(out_dir, key + ".json"), "w") as f:
            json.dump({"trails": list(trails.values())}, f, ensure_ascii=False, separators=(",", ":"))
    cov_path = os.path.join(out_dir, "coverage.json")
    if os.path.exists(cov_path):
        coverage |= set(json.load(open(cov_path))["tiles"])
    with open(cov_path, "w") as f:
        json.dump({"source": "OpenStreetMap contributors (ODbL)", "tiles": sorted(coverage | set(tiles))}, f, separators=(",", ":"))
    print(len(tiles), "tiles,", len(coverage | set(tiles)), "covered")


if __name__ == "__main__":
    main()
