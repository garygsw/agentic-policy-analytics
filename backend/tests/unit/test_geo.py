"""Map layers and the matching of labels to them, on the committed boundaries."""
from app.analysis import geo


def test_the_committed_layers_have_every_area():
    assert len(geo.layer("planning_areas")) == 55 and len(geo.layer("regions")) == 5
    assert [len(geo.layer(f"electoral_{y}")) for y in geo.ELECTION_YEARS] == [23, 27, 29, 31, 33]
    assert all(len(ring) >= 4 for area in geo.layer("planning_areas").values() for ring in area["rings"])


def test_hdb_towns_match_planning_areas_including_the_ones_that_span_several():
    found = geo.match(["Ang Mo Kio", "KALLANG/WHAMPOA", "Central Area", "Pasir Ris"])
    assert found.layer == "planning_areas" and found.coverage == 1.0
    assert len(found.shapes["Central Area"]) >= 10  # URA's Central Area: about a dozen planning areas
    assert "Central Area planning areas" in found.note


def test_each_kind_of_region_finds_its_own_layer():
    assert geo.match(["north", "south", "east", "west", "central"]).layer == "nea_regions"  # PSI regions
    ura = ["Central Region", "East Region", "North Region", "North-East Region", "West Region"]
    assert geo.match(ura).layer == "regions"
    assert geo.match(["ict", "retail", "construction"]) is None


def test_electoral_divisions_use_the_boundaries_of_their_election():
    divisions = [area["name"] for area in geo.layer("electoral_2020").values()]
    assert geo.match(divisions, "2020").layer == "electoral_2020"
    assert geo.match(divisions, "2023").layer == "electoral_2020"  # in force until the 2025 election
