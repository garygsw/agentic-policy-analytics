"""Write the committed map boundaries: official data.gov.sg GeoJSON, simplified to a few hundred kilobytes.

    uv run python scripts/make_boundaries.py

Each layer keeps one name per area (plus a few useful properties) and only its outer rings, simplified with
Douglas-Peucker to about 45 metres, which is finer than a map in a report can show. The files are redistributed
under the Singapore Open Data Licence v1.0, with the source named in each file.
"""
import json
import re
import time

import numpy as np

from app.data.sources import DATA_DIR, DataGovSg

OUT = DATA_DIR / "boundaries"
TOLERANCE = 0.0004  # degrees, about 45 m
LICENCE = "Singapore Open Data Licence v1.0 (https://data.gov.sg/open-data-licence)"
# layer: (data.gov.sg dataset, title, the property holding each area's name, other properties to keep)
LAYERS = {
    "planning_areas": ("d_4765db0e87b9c86336792efe8a1f7a66", "URA Master Plan 2019 Planning Area Boundary (No Sea)",
                       "PLN_AREA_N", {"region": "REGION_N", "central_area": "CA_IND"}),
    "regions": ("d_bf4d24df9129d5a8ff8cf82e20959ee0", "URA Master Plan 2019 Region Boundary (No Sea)",
                "REGION_N", {}),
    "electoral_2006": ("d_7fb48bf0b7b7c8deeccfb2b40d120e08", "ELD Electoral Boundary 2006", "ED_DESC", {}),
    "electoral_2011": ("d_305b03ed3c477aba648eeddaea2d4279", "ELD Electoral Boundary 2011", "ED_DESC", {}),
    "electoral_2015": ("d_1dea85025d48bc75ed566eb2696b7e0f", "ELD Electoral Boundary 2015", "ED_DESC", {}),
    "electoral_2020": ("d_6077aa5ab73d447b32f451ea224221b6", "ELD Electoral Boundary 2020", "ED_DESC", {}),
    "electoral_2025": ("d_7ddf956dfc1c59080bf95bba1c58a5d2", "ELD Electoral Boundary 2025", "ED_DESC", {}),
}


def simplify_line(points: list, tolerance: float) -> list:
    """Douglas-Peucker: keep the end points, and recursively the point furthest from the line between them while
    it is further than the tolerance."""
    if len(points) < 3:
        return points
    pts = np.array(points)
    (x0, y0), (x1, y1) = pts[0], pts[-1]
    length = np.hypot(x1 - x0, y1 - y0) or 1e-12
    distances = np.abs((x1 - x0) * (pts[1:-1, 1] - y0) - (y1 - y0) * (pts[1:-1, 0] - x0)) / length
    i = int(np.argmax(distances)) + 1
    if distances[i - 1] <= tolerance:
        return [points[0], points[-1]]
    return simplify_line(points[:i + 1], tolerance)[:-1] + simplify_line(points[i:], tolerance)


def simplify_ring(ring: list, tolerance: float) -> list:
    """A closed ring: split at the point furthest from its start, then simplify both halves."""
    pts = np.array(ring)
    far = int(np.argmax(np.hypot(pts[:, 0] - pts[0, 0], pts[:, 1] - pts[0, 1])))
    return simplify_line(ring[:far + 1], tolerance)[:-1] + simplify_line(ring[far:], tolerance)


def outer_rings(geometry: dict) -> list[list]:
    polygons = geometry["coordinates"] if geometry["type"] == "MultiPolygon" else [geometry["coordinates"]]
    return [[(x, y) for x, y, *_ in polygon[0]] for polygon in polygons]


def prop(feature: dict, key: str) -> str:
    """A property, or the same field inside the HTML table some data.gov.sg files put in "Description"."""
    properties = feature["properties"]
    if key in properties:
        return str(properties[key]).strip()
    found = re.search(rf"<th>{key}</th>\s*<td>(.*?)</td>", properties.get("Description", ""))
    if not found:
        raise KeyError(f"no {key} in {list(properties)}")
    return found.group(1).strip()


def build(dataset_id: str, title: str, name_key: str, extra: dict) -> tuple[dict, int, int]:
    source = json.loads(DataGovSg().download(dataset_id))
    areas: dict[str, dict] = {}
    before = after = 0
    for feature in source["features"]:
        name = prop(feature, name_key)
        rings = []
        for ring in outer_rings(feature["geometry"]):
            before += len(ring)
            simple = [(round(x, 5), round(y, 5)) for x, y in simplify_ring(ring, TOLERANCE)]
            if len(simple) >= 4:  # a ring smaller than the tolerance disappears
                rings.append(simple)
                after += len(simple)
        area = areas.setdefault(name, {"name": name, **{k: prop(feature, v) for k, v in extra.items()}, "rings": []})
        area["rings"] += rings  # an area split over several features keeps all its parts
    features = [{"type": "Feature", "properties": {k: v for k, v in a.items() if k != "rings"},
                 "geometry": {"type": "MultiPolygon", "coordinates": [[list(map(list, r))] for r in a["rings"]]}}
                for a in areas.values() if a["rings"]]
    layer = {"type": "FeatureCollection", "source": f"{title}, data.gov.sg {dataset_id}", "licence": LICENCE,
             "simplified": f"outer rings only; Douglas-Peucker, {TOLERANCE} degrees", "features": features}
    return layer, before, after


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    for name, (dataset_id, title, name_key, extra) in LAYERS.items():
        layer, before, after = build(dataset_id, title, name_key, extra)
        path = OUT / f"{name}.geojson"
        path.write_text(json.dumps(layer, separators=(",", ":"), ensure_ascii=False))
        print(f"{name:16s} {len(layer['features']):3d} areas  {before:7,} -> {after:6,} points  "
              f"{path.stat().st_size / 1e3:6.1f} kB")
        time.sleep(6)  # data.gov.sg rate limit


if __name__ == "__main__":
    main()
