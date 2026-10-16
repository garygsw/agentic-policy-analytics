"""Map layers: the committed boundaries (data/boundaries, written by scripts/make_boundaries.py) and the matching
of a dataset's labels to them, so a measure by town, planning area, region, electoral division, Southeast Asian
country or 1-degree grid cell can be drawn as a map. Labels are matched by name, ignoring letter case and
punctuation."""
import json
import re
from dataclasses import dataclass, field
from functools import cache

import numpy as np
from matplotlib.path import Path as Outline

from app.data.sources import DATA_DIR

BOUNDARIES = DATA_DIR / "boundaries"
MIN_COVERAGE = 0.6  # a layer is used when it covers at least this share of the labels
# NEA reports PSI for five regions as label points, not boundaries (the real-time PSI API's region metadata).
NEA_REGIONS = {"NORTH": (103.82, 1.41803), "SOUTH": (103.82, 1.29587), "EAST": (103.94, 1.35735),
               "WEST": (103.7, 1.35735), "CENTRAL": (103.82, 1.35735)}
ELECTION_YEARS = (2006, 2011, 2015, 2020, 2025)  # the electoral boundaries committed
SINGAPORE = (103.82, 1.35)  # marked on maps of the region
CELL = re.compile(r"(\d+(?:\.\d+)?)([NS]) (\d+(?:\.\d+)?)([EW])")  # a grid cell's centre, e.g. "1.5S 101.5E"


@dataclass
class Match:
    layer: str                                              # planning_areas, regions, electoral_2025, nea_regions
    shapes: dict[str, list] = field(default_factory=dict)   # label -> outer rings, [(lon, lat), ...]
    points: dict[str, tuple] = field(default_factory=dict)  # label -> (lon, lat)
    unmatched: list[str] = field(default_factory=list)
    note: str = ""
    base: str = "singapore"                                 # the outline drawn underneath: singapore or region

    @property
    def coverage(self) -> float:
        found = len(self.shapes) + len(self.points)
        return found / (found + len(self.unmatched)) if found + len(self.unmatched) else 0.0


def key(label: str) -> str:
    """A name as compared: upper case, punctuation as spaces, without URA's " REGION" suffix."""
    return re.sub(r"[^A-Z0-9]+", " ", str(label).upper()).strip().removesuffix(" REGION")


@cache
def layer(name: str) -> dict[str, dict]:
    """One boundary layer: {key(name): {"name": ..., "rings": [...], other properties}}."""
    collection = json.loads((BOUNDARIES / f"{name}.geojson").read_text())
    return {key(f["properties"]["name"]): {**f["properties"],
                                           "rings": [polygon[0] for polygon in f["geometry"]["coordinates"]]}
            for f in collection["features"]}


def town_areas() -> dict[str, list[str]]:
    """HDB towns that are not one planning area, as the planning areas that make them up."""
    areas = layer("planning_areas")
    central = [k for k, a in areas.items() if a.get("central_area") == "Y"]
    return {"KALLANG WHAMPOA": ["KALLANG"], "CENTRAL AREA": central, "CENTRAL": central}


def _shapes(labels: list[str], layer_name: str, aliases: dict[str, list[str]] | None = None) -> Match:
    areas, found = layer(layer_name), Match(layer_name)
    for label in labels:
        k = key(label)
        if k in areas:
            found.shapes[label] = areas[k]["rings"]
        elif aliases and k in aliases:
            found.shapes[label] = [ring for area in aliases[k] for ring in areas[area]["rings"]]
        else:
            found.unmatched.append(label)
    return found


def cell_label(lon: float, lat: float) -> str:
    """The 1-degree cell a point falls in, named by its centre: "1.5S 101.5E"."""
    centre_lat, centre_lon = np.floor(lat) + 0.5, np.floor(lon) + 0.5
    north, east = "N" if centre_lat >= 0 else "S", "E" if centre_lon >= 0 else "W"
    return f"{abs(centre_lat):.1f}{north} {abs(centre_lon):.1f}{east}"


def cell_square(label: str) -> list | None:
    """A grid cell's outline: the 1-degree square around its centre."""
    centre = cell_point(label)
    if centre is None:
        return None
    lon, lat = centre
    return [(lon - 0.5, lat - 0.5), (lon + 0.5, lat - 0.5), (lon + 0.5, lat + 0.5), (lon - 0.5, lat + 0.5),
            (lon - 0.5, lat - 0.5)]


def cell_point(label: str) -> tuple[float, float] | None:
    found = CELL.fullmatch(str(label).strip())
    if not found:
        return None
    lat, north, lon, east = found.groups()
    return (float(lon) * (1 if east == "E" else -1), float(lat) * (1 if north == "N" else -1))


def country_of(lons: np.ndarray, lats: np.ndarray) -> np.ndarray:
    """The Southeast Asian country each point falls in, or None (sea, or a country outside the region)."""
    points = np.column_stack([lons, lats])
    found = np.full(len(points), None, dtype=object)
    for area in layer("southeast_asia").values():
        for ring in area["rings"]:
            inside = Outline(ring).contains_points(points) & (found == None)  # noqa: E711 - element-wise
            found[inside] = area["name"]
    return found


def match(labels: list[str], year: str | None = None) -> Match | None:
    """The layer that covers most of the labels (at least MIN_COVERAGE), or None. `year` picks the electoral
    boundaries in force then (the latest election up to that year)."""
    candidates = [_shapes(labels, "planning_areas", town_areas()), _shapes(labels, "regions")]
    nea = Match("nea_regions", note="NEA's PSI regions are reporting areas with no boundaries: shown as points")
    for label in labels:
        if key(label) in NEA_REGIONS:
            nea.points[label] = NEA_REGIONS[key(label)]
        else:
            nea.unmatched.append(label)
    candidates.append(nea)
    election = max((y for y in ELECTION_YEARS if not year or not year[:4].isdigit() or y <= int(year[:4])),
                   default=None)
    if election:
        electoral = _shapes(labels, f"electoral_{election}")
        electoral.note = f"electoral divisions as drawn for the {election} general election"
        candidates.append(electoral)
    countries = _shapes(labels, "southeast_asia")
    countries.base = "region"
    cells = Match("grid_cells", base="region", note="1-degree grid cells, drawn as squares")
    for label in labels:
        square = cell_square(label)
        if square:
            cells.shapes[label] = [square]
        else:
            cells.unmatched.append(label)
    candidates += [countries, cells]
    best = max(candidates, key=lambda m: m.coverage)  # the first wins a tie
    if best.coverage < MIN_COVERAGE:
        return None
    if best.layer == "planning_areas" and any(key(label) in ("CENTRAL AREA", "CENTRAL") for label in best.shapes):
        best.note = "the Central Area town is shown as URA's Central Area planning areas"
    return best


def base_rings(base: str = "singapore") -> list:
    """The outline under a map: every planning area for Singapore, every country for the region."""
    name = "southeast_asia" if base == "region" else "planning_areas"
    return [ring for area in layer(name).values() for ring in area["rings"]]
