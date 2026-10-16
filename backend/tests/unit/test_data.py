"""Data layer: parsing, cleaning, quality reports, curated cleaners, the load/fallback order and retries.
Fixtures are tiny inline samples shaped like the real files; no network."""
import json
import sqlite3

import httpx
import pandas as pd
import pytest

from app.config import Settings
from app.data import catalogue, http
from app.data.cleaning import normalise_period, tidy, to_number
from app.data.http import SourceUnavailable
from app.data.registry import CEA, CURATED, ICT_MANPOWER, MRT_EXITS, RAIL_PLAN, RETRENCHED
from app.data.sources import DATA_DIR
from app.data.store import DataStore, DataUnavailable


# -- parsing ----------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("value,frequency,expected", [
    ("2024", "annual", "2024"), ("2024.0", "annual", "2024"), ("2024-01-01", "annual", "2024"),
    ("2024-Q1", "quarterly", "2024-Q1"), ("2024 1Q", "quarterly", "2024-Q1"), ("1Q 2024", "quarterly", "2024-Q1"),
    ("2024-03", "monthly", "2024-03"), ("2024-3-01", "monthly", "2024-03"), ("OCT-2017", "monthly", "2017-10"),
    ("Mar 2024", "monthly", "2024-03"), ("2008", "financial_year", "FY2008"),
    ("Q5 2024", "quarterly", None), ("2024-13", "monthly", None), ("total", "annual", None),
])
def test_periods_are_normalised(value, frequency, expected):
    assert normalise_period(value, frequency) == expected


def test_numbers_placeholders_and_nil_markers():
    numbers, missing, unparseable = to_number(pd.Series(["1,234", "$5.6", "12%", "na", "-", "TBC", None]))
    assert numbers.tolist()[:3] == [1234.0, 5.6, 12.0]
    assert (missing, unparseable) == (3, 1)
    numbers, missing, _ = to_number(pd.Series(["-", "40"]), zero_markers=("-",))  # MOM: "-" means nil
    assert numbers.tolist() == [0.0, 40.0] and missing == 0


def test_tidy_reports_every_dropped_row():
    raw = pd.DataFrame({"year": ["2020", "2020", "2021", "2021", "bad", "2022"],
                        "industry": ["ICT", "Total", "ICT", "ICT", "ICT", "ICT"],
                        "employed": ["100", "500", "110", "110", "9", "n.a."]})
    frame, report = tidy(raw, "t", period="year", frequency="annual", dims=["industry"],
                         measures={"employed": "persons"}, total_labels=("Total",))
    assert frame[["period", "industry", "measure", "value", "unit"]].values.tolist() == [
        ["2020", "ICT", "employed", 100.0, "persons"], ["2021", "ICT", "employed", 110.0, "persons"]]
    assert (report.total_rows_removed, report.invalid_periods, report.duplicates_removed) == (1, 1, 1)
    assert report.missing_values == {"employed": 1} and report.coverage == ("2020", "2021")
    assert "2 values (one per measure per row) from 6 source rows" in report.summary()


def test_labels_differing_only_in_case_are_one_series():
    # HDB's town names changed case in 2019; without merging, every town's series stops at 2019.
    raw = pd.DataFrame({"quarter": ["2018-Q4", "2019-Q1", "2019-Q2", "2019-Q3", "2019-Q2"],
                        "town": ["Ang Mo Kio", "Ang Mo Kio", "ANG MO KIO", "ANG MO KIO", "BEDOK"],
                        "price": ["400000", "405000", "410000", "420000", "380000"]})
    frame, report = tidy(raw, "t", period="quarter", frequency="quarterly", dims=["town"], measures={"price": "S$"})
    assert sorted(frame["town"].unique()) == ["ANG MO KIO", "BEDOK"]  # a tie goes to the first in sort order
    assert frame.groupby("town").size().max() == 4  # one series of four quarters
    assert "1 town labels differing only in letter case were merged" in report.summary()


# -- curated cleaners --------------------------------------------------------------------------------------------
def test_retrenchment_dash_means_nil():
    frame, _ = CURATED["retrenchment_by_industry"].clean(
        {RETRENCHED: b"quarter,industry,retrench\n2024-Q1,information and communications,-\n"})
    assert frame["value"].tolist() == [0.0]


def test_cea_drops_personal_columns_and_counts_records():
    raw = (b"salesperson_name,transaction_date,salesperson_reg_num,property_type,transaction_type,represented,town,"
           b"district,general_location\n"
           b"A PERSON,OCT-2017,R000001A,HDB,RESALE,SELLER,YISHUN,-,-\n"
           b"B PERSON,OCT-2017,R000002B,HDB,RESALE,SELLER,YISHUN,-,-\n"
           b"C PERSON,OCT-2017,R000003C,CONDOMINIUM_APARTMENTS,WHOLE RENTAL,LANDLORD,-,15,EAST\n")
    frame, report = CURATED["cea_transactions"].clean({CEA: raw})
    assert report.personal_data_columns_dropped == ["salesperson_name", "salesperson_reg_num"]
    assert "PERSON" not in frame.to_csv() and "R00000" not in frame.to_csv()
    assert sorted(zip(frame["location"], frame["value"], strict=True)) == [("District 15", 1.0), ("YISHUN", 2.0)]


def test_rail_stations_match_names_across_layers():
    def geo(rows):
        return json.dumps({"features": [{"properties": r} for r in rows]}).encode()
    plan = geo([{"NAME": "ANG MO KIO INTERCHANGE", "RAIL_TYPE": "MRT", "GRND_LEVEL": "UNDERGROUND"},
                {"NAME": "PUNGGOL COAST", "RAIL_TYPE": "MRT", "GRND_LEVEL": "UNDERGROUND"},
                {"NAME": "TENGAH PLANTATION", "RAIL_TYPE": "MRT", "GRND_LEVEL": "ABOVEGROUND"}])
    exits = geo([{"STATION_NA": "ANG MO KIO MRT STATION"}, {"STATION_NA": "ANG MO KIO MRT STATION"},
                 {"STATION_NA": "NE18"}])
    frame, _ = CURATED["rail_stations"].clean({RAIL_PLAN: plan, MRT_EXITS: exits})
    status = dict(zip(frame["station"], frame["status"], strict=True))
    assert status == {"Ang Mo Kio": "open", "Punggol Coast": "open", "Tengah Plantation": "planned (no exits yet)"}


def test_internal_workbook_problems_are_caught():
    raw = {"tech_talent_programmes.xlsx": (DATA_DIR / "internal" / "tech_talent_programmes.xlsx").read_bytes()}
    frame, report = CURATED["internal_tech_programmes"].clean(raw)
    assert (report.total_rows_removed, report.duplicates_removed) == (8, 1)
    assert sum(report.missing_values.values()) == 1 and sum(report.unparseable_values.values()) == 1
    assert "Total" not in set(frame["programme"])


def test_gdp_levels_come_from_series_numbers():
    raw = {"M015731": json.dumps({"Data": {"dataLastUpdated": "10/02/2026", "row": [
        {"seriesNo": "1", "rowText": "GDP At Current Market Prices", "columns": [{"key": "2024", "value": "765497.5"}]},
        {"seriesNo": "1.2.4", "rowText": "Information & Communications", "columns": [{"key": "2024", "value": "na"}]},
    ]}}).encode()}
    frame, report = CURATED["gdp_by_industry"].clean(raw)
    assert frame[["industry", "level", "value"]].values.tolist() == [["GDP At Current Market Prices", "1", 765497.5]]
    assert report.missing_values == {"gdp": 1}


# -- uncurated datasets ------------------------------------------------------------------------------------------
def test_infer_wide_statistical_table():
    raw = pd.DataFrame({"DataSeries": ["Total", "Services"], "2024": ["10", "6"], "2023": ["9", "5"],
                        "2022": ["8", "na"]})
    frame, report = catalogue.infer_tidy(raw, "d_x")
    assert len(frame) == 5 and set(frame["period"]) == {"2022", "2023", "2024"}
    assert "uncurated" in report.notes[0]


def test_infer_drops_high_cardinality_text_such_as_names():
    raw = pd.DataFrame({"year": ["2024"] * 300, "licensee_name": [f"Person {i}" for i in range(300)],
                        "type": ["clinic"] * 300, "count": ["1"] * 300})
    frame, report = catalogue.infer_tidy(raw, "d_y")
    assert "licensee_name" not in frame.columns and "type" in frame.columns
    assert any("licensee_name" in note for note in report.notes)


def test_catalogue_search_ranks_title_matches(monkeypatch):
    rows = [{"datasetId": "d_1", "name": "COE Bidding Results", "description": "premium by category",
             "managedByAgencyName": "LTA", "format": "CSV", "lastUpdatedAt": "2026-10-01"},
            {"datasetId": "d_2", "name": "Vehicle population", "description": "mentions COE once",
             "managedByAgencyName": "LTA", "format": "CSV", "lastUpdatedAt": "2026-10-01"},
            {"datasetId": "d_3", "name": "Hospital beds", "description": "", "managedByAgencyName": "MOH",
             "format": "CSV", "lastUpdatedAt": "2026-10-01"}]
    monkeypatch.setattr(catalogue, "load_catalogue", lambda: rows)
    assert [r["dataset_id"] for r in catalogue.search("COE premium")] == ["d_1", "d_2"]


# -- load order and fallbacks -------------------------------------------------------------------------------------
ICT_CSV = b"year,manpower,growth\n2017,210100,7.7\n2018,220800,5.1\n"


class FakeSource:
    name, licence = "fake source", "test licence"

    def __init__(self, fail=False):
        self.fail, self.calls = fail, 0

    def download(self, source_id):
        self.calls += 1
        if self.fail:
            raise SourceUnavailable("HTTP 503")
        return ICT_CSV

    @staticmethod
    def page_url(source_id):
        return f"https://example.org/{source_id}"


def store(tmp_path, source, database="app.sqlite", **settings):
    return DataStore(Settings(_env_file=None, **settings), database=f"sqlite:///{tmp_path / database}",
                     snapshot_dir=tmp_path / "snapshots", sources={"datagovsg": source})


def test_live_load_is_stored_in_the_database_then_reused(tmp_path):
    source = FakeSource()
    first = store(tmp_path, source).load("ict_manpower")
    second = store(tmp_path, source).load("ict_manpower")
    assert (first.provenance.origin, second.provenance.origin, source.calls) == ("live", "cache", 1)
    assert second.frame.equals(first.frame) and second.quality == first.quality
    raw = store(tmp_path, source).tables.raw("datagovsg", ICT_MANPOWER)  # the download, kept for auditing
    assert raw.data == ICT_CSV and raw.format == "csv"
    with sqlite3.connect(tmp_path / "app.sqlite") as db:  # plain rows, queryable with SQL
        rows = db.execute("select period, measure, value from dataset_rows where dataset_key = 'ict_manpower' "
                          "order by id").fetchall()
    assert len(rows) == len(first.frame) and rows[0][2] == first.frame["value"].iloc[0]


def test_a_town_published_under_two_names_is_one_series():
    """HDB published the central town as "Central" until 2018 and "CENTRAL AREA" after: one series, noted."""
    raw = pd.DataFrame({"quarter": ["2018-Q4", "2019-Q1", "2019-Q1"], "town": ["Central", "CENTRAL AREA", "Bedok"],
                        "flat_type": ["4-room"] * 3, "price": ["900000", "950000", "500000"]})
    frame, report = tidy(raw, "t", period="quarter", frequency="quarterly", dims=["town", "flat_type"],
                         measures={"price": "S$"}, same_as={"town": {"Central": "Central Area",
                                                                     "CENTRAL AREA": "Central Area"}})
    assert sorted(frame["town"].unique()) == ["Bedok", "Central Area"]
    assert any("'Central' as 'Central Area'" in note for note in report.notes)


def test_psi_is_summarised_by_month_and_region_from_both_timestamp_formats():
    from app.data.registry import PSI_24H, clean_psi
    rows = ["24hr_psi,north,south,east,west,central",
            "1/9/2015 1:00,120,90,80,70,60", "1/9/2015 2:00,140,95,85,75,65",  # day 1: north unhealthy
            "2015-09-02 010:00:00,90,101,80,70,60",  # the other format, with a 3-digit hour: day 2, south
            "1/10/2015 1:00,50,50,50,50,50"]
    frame, report = clean_psi({PSI_24H: "\n".join(rows).encode()})
    def value(period, region, measure):
        return frame[(frame.period == period) & (frame.region == region) & (frame.measure == measure)]["value"].item()
    assert value("2015-09", "north", "highest_psi") == 140.0
    assert value("2015-09", "north", "average_psi") == pytest.approx((120 + 140 + 90) / 3)  # 02 Sept, not 09 Feb
    assert (value("2015-09", "north", "unhealthy_days"), value("2015-09", "south", "unhealthy_days")) == (1.0, 1.0)
    assert value("2015-10", "west", "highest_psi") == 50.0
    assert report.rows_in == 4 and "1 timestamps written in another format were read" in report.notes[0]


def test_election_results_keep_parties_votes_and_shares_but_not_walkovers_or_names():
    from app.data.registry import GE_RESULTS, clean_ge_results
    rows = ["year,constituency,constituency_type,candidates,party,vote_count,vote_percentage",
            "2025,Bukit Panjang,SMC,A Candidate,PAP,20000,0.6",
            "2025,Bukit Panjang,SMC,B Candidate,SDP,13333,0.4",
            "2025,Marine Parade-Braddell Heights,GRC,C Candidate,PAP,na,na"]  # a walkover
    frame, report = clean_ge_results({GE_RESULTS: "\n".join(rows).encode()})
    shares = frame[frame.measure == "vote_share"].set_index("party")["value"]
    assert shares.to_dict() == {"PAP": 60.0, "SDP": 40.0}  # per cent of valid votes
    assert "Marine Parade-Braddell Heights" not in set(frame["constituency"]) and "candidates" not in frame
    assert report.personal_data_columns_dropped == ["candidates"] and "1 uncontested" in report.notes[-1]


def test_hotspots_are_counted_by_season_country_and_cell():
    from app.data.registry import HOTSPOTS, clean_hotspots
    csv = ("latitude,longitude,acq_date,confidence\n"
           "-0.5,101.4,2015-09-10,80\n-0.4,101.2,2015-09-12,60\n"  # Riau, Sumatra: one cell
           "3.0,105.0,2015-09-10,90\n"  # the South China Sea
           "-0.6,101.5,2015-09-11,10\n"  # low confidence
           "-2.2,113.9,2019-09-01,70\n")  # Central Kalimantan
    frame, report = clean_hotspots({HOTSPOTS: csv.encode()})
    counts = {(r.period, r.country, r.cell): r.value for r in frame.itertuples()}
    assert counts == {("2015", "Indonesia", "0.5S 101.5E"): 2.0, ("2019", "Indonesia", "2.5S 113.5E"): 1.0}
    assert "1 low-confidence ones and 1 outside" in report.notes[-1] and report.rows_in == 5


def test_a_snapshot_only_dataset_loads_its_snapshot_without_a_warning(tmp_path):
    loaded = DataStore(Settings(_env_file=None), database=f"sqlite:///{tmp_path / 'd.db'}").load("fire_hotspots")
    assert loaded.provenance.origin == "snapshot" and loaded.provenance.warning is None
    assert loaded.provenance.source == "NASA FIRMS" and set(loaded.frame["measure"]) == {"hotspots"}


def test_stores_created_at_once_in_parallel_runs_share_one_set_of_tables(tmp_path):
    """Parallel runs each create a store; the tables must be created once, not by every thread at the same time."""
    from concurrent.futures import ThreadPoolExecutor

    from app.data.database import DatasetTables
    url = f"sqlite:///{tmp_path / 'parallel.sqlite'}"
    with ThreadPoolExecutor(8) as pool:
        tables = list(pool.map(lambda _: DatasetTables(url), range(16)))
    assert len({id(t.sessions) for t in tables}) == 1


def test_source_outage_falls_back_to_stale_cache_with_warning(tmp_path):
    store(tmp_path, FakeSource()).load("ict_manpower")
    loaded = store(tmp_path, FakeSource(fail=True), data_max_age_hours=0).load("ict_manpower")
    assert loaded.provenance.origin == "cache" and "Live source unavailable (HTTP 503)" in loaded.provenance.warning


def test_source_outage_without_cache_uses_snapshot(tmp_path):
    live = store(tmp_path, FakeSource()).load("ict_manpower")
    DataStore.write_snapshot(tmp_path / "snapshots", live)
    loaded = store(tmp_path, FakeSource(fail=True), database="empty.sqlite").load("ict_manpower")
    assert loaded.provenance.origin == "snapshot" and loaded.provenance.warning


def test_nothing_available_raises(tmp_path):
    with pytest.raises(DataUnavailable, match="no stored copy"):
        store(tmp_path, FakeSource(fail=True)).load("ict_manpower")
    with pytest.raises(DataUnavailable, match="unknown dataset"):
        store(tmp_path, FakeSource()).load("not_a_dataset")


def test_force_source_fail_skips_live_even_with_fresh_cache(tmp_path):
    store(tmp_path, FakeSource()).load("ict_manpower")
    source = FakeSource()
    loaded = store(tmp_path, source, force_source_fail=True).load("ict_manpower")
    assert source.calls == 0 and "FORCE_SOURCE_FAIL" in loaded.provenance.warning


def test_committed_snapshots_load_for_every_curated_dataset(tmp_path):
    """Every curated dataset must work offline from its committed snapshot."""
    offline = DataStore(Settings(_env_file=None, force_source_fail=True), database=f"sqlite:///{tmp_path / 'a.db'}")
    for key in CURATED:
        loaded = offline.load(key)
        assert loaded.provenance.origin == "snapshot" and len(loaded.frame) > 0, key
        assert {"period", "measure", "value", "unit"} <= set(loaded.frame.columns), key


# -- retries ------------------------------------------------------------------------------------------------------
def test_fetch_retries_rate_limits_with_backoff(monkeypatch):
    responses = iter([httpx.Response(429), httpx.Response(429), httpx.Response(200, text="ok")])
    monkeypatch.setattr(httpx, "get", lambda *a, **k: next(responses))
    waits = []
    assert http.fetch("https://x", sleep=waits.append).text == "ok"
    assert waits == [10.0, 20.0]


def test_fetch_does_not_retry_client_errors(monkeypatch):
    calls = []
    monkeypatch.setattr(httpx, "get", lambda *a, **k: calls.append(1) or httpx.Response(404))
    with pytest.raises(SourceUnavailable, match="HTTP 404"):
        http.fetch("https://x", sleep=lambda s: None)
    assert len(calls) == 1
