"""Map layers: the committed boundaries (data/boundaries, written by scripts/make_boundaries.py) and the matching
of a dataset's labels to them, so a measure by town, planning area, region or electoral division can be drawn as
a map. Labels are matched by name, ignoring letter case and punctuation."""
import json
import re
from dataclasses import dataclass, field
from functools import cache

from app.data.sources import DATA_DIR

BOUNDARIES = DATA_DIR / "boundaries"
MIN_COVERAGE = 0.6  # a layer is used when it covers at least this share of the labels
# NEA reports PSI for five regions as label points, not boundaries (the real-time PSI API's region metadata).
NEA_REGIONS = {"NORTH": (103.82, 1.41803), "SOUTH": (103.82, 1.29587), "EAST": (103.94, 1.35735),
               "WEST": (103.7, 1.35735), "CENTRAL": (103.82, 1.35735)}
ELECTION_YEARS = (2006, 2011, 2015, 2020, 2025)  # the electoral boundaries committed


@dataclass
class Match:
    layer: str                                              # planning_areas, regions, electoral_2025, nea_regions
    shapes: dict[str, list] = field(default_factory=dict)   # label -> outer rings, [(lon, lat), ...]
    points: dict[str, tuple] = field(default_factory=dict)  # label -> (lon, lat)
    unmatched: list[str] = field(default_factory=list)
    note: str = ""

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
    best = max(candidates, key=lambda m: m.coverage)  # the first wins a tie
    if best.coverage < MIN_COVERAGE:
        return None
    if best.layer == "planning_areas" and any(key(label) in ("CENTRAL AREA", "CENTRAL") for label in best.shapes):
        best.note = "the Central Area town is shown as URA's Central Area planning areas"
    return best


def base_rings() -> list:
    """Every planning area: the outline of Singapore under a map."""
    return [ring for area in layer("planning_areas").values() for ring in area["rings"]]
