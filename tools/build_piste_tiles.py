import json, sys, os, math, collections, hashlib
src, out = sys.argv[1], sys.argv[2]
TILE = 0.1
tiles = collections.defaultdict(dict)
# Ski area id (first 8 chars, as in the app's resort index) -> [minLat, minLon, maxLat, maxLon]
area_bounds = {}

def simplify(pts, tol):
    # Douglas–Peucker in degrees-scaled meters
    if len(pts) < 3: return pts
    lat0 = math.radians(pts[0][1]); kx = 111320 * math.cos(lat0); ky = 110540
    def d(p, a, b):
        ax, ay = (a[0]-p[0])*kx, (a[1]-p[1])*ky; bx, by = (b[0]-p[0])*kx, (b[1]-p[1])*ky
        dx, dy = bx-ax, by-ay; l2 = dx*dx+dy*dy
        t = 0 if l2 == 0 else max(0, min(1, -(ax*dx+ay*dy)/l2))
        cx, cy = ax+t*dx, ay+t*dy; return math.hypot(cx, cy)
    keep = [False]*len(pts); keep[0] = keep[-1] = True; stack = [(0, len(pts)-1)]
    while stack:
        i, j = stack.pop(); best, bi = 0, -1
        for k in range(i+1, j):
            dd = d(pts[k], pts[i], pts[j])
            if dd > best: best, bi = dd, k
        if best > tol: keep[bi] = True; stack += [(i, bi), (bi, j)]
    return [p for p, k in zip(pts, keep) if k]

def lines(geom):
    t = geom.get("type"); c = geom.get("coordinates") or []
    if t == "LineString": return [c]
    if t == "MultiLineString": return c
    if t == "Polygon": return c[:1]
    if t == "MultiPolygon": return [poly[0] for poly in c if poly]
    return []

def osm_id(props):
    for s in props.get("sources") or []:
        sid = s.get("id", "")
        if s.get("type") == "openstreetmap" and "/" in sid and sid.split("/")[1].isdigit():
            return int(sid.split("/")[1])
    return int(hashlib.md5(props["id"].encode()).hexdigest()[:12], 16)

def handle(feature, kind):
    p = feature["properties"]
    if p.get("status") not in ("operating", None): return
    if kind == "piste" and "downhill" not in (p.get("uses") or []): return
    base = osm_id(p)
    areas = sorted({(a.get("properties") or {}).get("id", "")[:8] for a in (p.get("skiAreas") or []) if (a.get("properties") or {}).get("id")})
    for n, line in enumerate(lines(feature.get("geometry") or {})):
        pts = simplify([(x[0], x[1]) for x in line], 4)
        if len(pts) < 2: continue
        item = {"id": base * 10 + n if n else base, "kind": kind, "name": p.get("name"),
                "difficulty": p.get("difficulty") if kind == "piste" else None,
                "liftType": p.get("liftType") if kind == "lift" else None,
                "latitudes": [round(q[1], 5) for q in pts], "longitudes": [round(q[0], 5) for q in pts]}
        if areas: item["areas"] = areas
        lats = [q[1] for q in pts]; lons = [q[0] for q in pts]
        for a in areas:
            b = area_bounds.get(a)
            area_bounds[a] = [min(lats), min(lons), max(lats), max(lons)] if b is None else \
                [min(b[0], min(lats)), min(b[1], min(lons)), max(b[2], max(lats)), max(b[3], max(lons))]
        for key in {(math.floor(q[1] / TILE), math.floor(q[0] / TILE)) for q in pts}:
            tiles[key][item["id"]] = item

for path, kind in ((src + "/runs.geojson", "piste"), (src + "/lifts.geojson", "lift")):
    with open(path) as f:
        for line in f:
            line = line.strip().rstrip(",")
            if not line.startswith('{"type":"Feature"'): continue
            handle(json.loads(line), kind)

os.makedirs(out, exist_ok=True)
total = 0
for (la, lo), items in tiles.items():
    path = f"{out}/{la}_{lo}.json"
    with open(path, "w") as f: json.dump({"pistes": list(items.values())}, f, separators=(",", ":"), ensure_ascii=False)
    total += os.path.getsize(path)
print(len(tiles), "tiles,", round(total / 1e6, 1), "MB")
json.dump({k: [round(v, 5) for v in b] for k, b in area_bounds.items()}, open(out + "/../area_bounds.json", "w"))
print("troodos:", len(tiles.get((349, 328), {})))
