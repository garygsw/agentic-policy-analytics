"""Analytics: statistics, summing rules, facts and charts, the tools' schemas, the ReAct loop and both agents.
All on small synthetic tables; no network, no real LLM."""
import json

import numpy as np
import pandas as pd
import pytest

from app.agents.tool_loop import run_tool_loop
from app.agents.tools import analytics_tools, extraction_tools
from app.agents.workers import AgentWorkers
from app.analysis import stats
from app.analysis.charts import line_chart, period_date
from app.analysis.workspace import Workspace, round_value
from app.data.cleaning import QualityReport
from app.data.registry import CuratedDataset
from app.data.store import LoadedDataset, Provenance
from app.llm.fake import ScriptedChatModel, reply

# A small employment table: 2 industries x 2 sexes x 5 years, plus a quarterly table and a non-time table.
YEARS = ["2020", "2021", "2022", "2023", "2024"]
EMPLOYMENT = pd.DataFrame([
    {"period": y, "industry": ind, "sex": sex, "measure": "employed", "unit": "persons",
     "value": float(base + 10 * i)}
    for i, y in enumerate(YEARS) for ind, base in (("ict", 100), ("retail", 300)) for sex in ("female", "male")])
QUARTERS = [f"{y}-Q{q}" for y in ("2023", "2024") for q in range(1, 5)]
RETRENCH = pd.DataFrame([{"period": p, "industry": ind, "measure": "retrench", "unit": "persons", "value": float(v)}
                         for p, v in zip(QUARTERS, [10, 20, 30, 40, 5, 15, 25, 35], strict=True)
                         for ind in ("ict", "services")])
GDP = pd.DataFrame([{"period": y, "industry": "ict", "measure": "gdp", "unit": "S$ million", "value": 50.0 + 5 * i}
                    for i, y in enumerate(YEARS)])
# Quarterly prices by town; town C has no value in the last quarter, D has none in the range at all.
PRICES = pd.DataFrame([{"period": p, "town": t, "measure": "price", "unit": "S$", "value": v}
                       for t, values in (("A", [100, 110, 150]), ("B", [200, 210, 220]), ("C", [300, 330, None]))
                       for p, v in zip(["2020-Q1", "2020-Q2", "2020-Q3"], values, strict=True) if v is not None]
                      + [{"period": "2019-Q4", "town": "D", "measure": "price", "unit": "S$", "value": 90.0}])
STATIONS = pd.DataFrame([{"period": "", "station": s, "status": st, "measure": "exits", "unit": "exits", "value": v}
                         for s, st, v in (("A", "open", 3.0), ("B", "planned", 0.0), ("C", "planned", 0.0))])


# Real place names, for maps: two HDB towns, two that span several planning areas, and one that is not a place.
TOWNS = pd.DataFrame([{"period": "2024", "town": t, "measure": "price", "unit": "S$", "value": v}
                      for t, v in (("Bedok", 500.0), ("Tampines", 550.0), ("Kallang/Whampoa", 700.0),
                                   ("Central Area", 900.0), ("Atlantis", 1.0))])
PSI = pd.DataFrame([{"period": "2015-09", "region": r, "measure": "highest_psi", "unit": "24-hour PSI", "value": v}
                    for r, v in (("north", 302.0), ("south", 322.0), ("east", 285.0), ("west", 321.0),
                                 ("central", 300.0))])


# Monthly PSI, June to October 2015-2019, for seasons: the highest month is October (100 more each year) and
# unhealthy days are 0 to 4 a month. October 2019 is missing, so 2019's July-October season is incomplete.
PSI_MONTHS = pd.DataFrame([
    {"period": f"{y}-{m:02d}", "region": r, "measure": measure, "unit": unit, "value": value}
    for y in range(2015, 2020) for m in range(6, 11) if (y, m) != (2019, 10) for r in ("south", "north")
    for measure, unit, value in (("highest_psi", "24-hour PSI", 10.0 * m + 100 * (y - 2015)),
                                 ("unhealthy_days", "days", float(m - 6)))])
FIRE_YEARS = pd.DataFrame([{"period": str(y), "measure": "hotspots", "unit": "hotspots", "value": v}
                           for y, v in ((2015, 9000.0), (2016, 1000.0), (2017, 800.0), (2018, 2000.0))])


def dataset(key, summable=None, frequency="annual", over_time=None):
    return CuratedDataset(key, key.title(), "Agency", "topic", "desc", "datagovsg", (key,), frequency,
                          clean=lambda raw: None, summable=summable or {}, over_time=over_time or {})


DATASETS = {
    "employment": (EMPLOYMENT, dataset("employment", {"employed": ("industry", "sex")})),
    "retrenchment": (RETRENCH, dataset("retrenchment", frequency="quarterly")),  # nothing may be added up
    "stations": (STATIONS, dataset("stations", {"exits": ("station", "status")}, "none")),
    "gdp": (GDP, dataset("gdp")),
    "prices": (PRICES, dataset("prices", frequency="quarterly")),
    "towns": (TOWNS, dataset("towns")),
    "psi": (PSI, dataset("psi", frequency="monthly")),
    "employment_strict": (EMPLOYMENT, dataset("employment_strict", {"employed": ("industry",)})),  # not by sex
    "psi_months": (PSI_MONTHS, dataset("psi_months", frequency="monthly",
                                       over_time={"highest_psi": "max", "unhealthy_days": "sum"})),
    "fire_years": (FIRE_YEARS, dataset("fire_years")),
    "fires": (pd.DataFrame([{"period": "2015", "country": c, "cell": "", "measure": "hotspots", "unit": "hotspots",
                             "value": v} for c, v in (("Indonesia", 9000.0), ("Malaysia", 700.0), ("Brunei", 20.0))]),
              dataset("fires", {"hotspots": ("country", "cell")})),
}


class FakeStore:
    def __init__(self):
        self.loads = []

    def load(self, key):
        self.loads.append(key)
        frame, ds = DATASETS[key]
        provenance = Provenance(origin="snapshot", source="test", agency="Agency", urls=[f"https://x/{key}"],
                                licence="test", retrieved_at="2026-10-14T00:00:00+00:00", warning="source down")
        return LoadedDataset(ds, frame.copy(), QualityReport(dataset_id=key, rows_in=len(frame),
                                                             rows_out=len(frame)), provenance)


def tools(ws=None):
    ws = ws or Workspace(FakeStore())
    return ws, {t.name: t for t in extraction_tools(ws) + analytics_tools(ws)}


def call(tool, **args):
    return json.loads(tool.invoke({**args, "reason": "test"}))


ICT = {"dataset_id": "employment", "measure": "employed", "filters": {"industry": "ict"}, "label": "ICT"}


# -- statistics ------------------------------------------------------------------------------------------------
def test_open_dimensions_are_added_up_only_where_allowed():
    frame, ds = DATASETS["employment"]
    series, unit, notes = stats.select_series(frame, ds, "employed", {"industry": "ict"})
    assert series.tolist() == [200.0, 220.0, 240.0, 260.0, 280.0] and unit == "persons"
    assert notes == ["added up across sex"]
    frame, ds = DATASETS["retrenchment"]
    with pytest.raises(stats.AnalysisError, match="'industry' has 2 values and retrench cannot be added across it"):
        stats.select_series(frame, ds, "retrench")


def test_errors_tell_the_agent_how_to_fix_the_call():
    frame, ds = DATASETS["employment"]
    with pytest.raises(stats.AnalysisError, match="measures: \\['employed'\\]"):
        stats.select_series(frame, ds, "jobs")
    with pytest.raises(stats.AnalysisError, match="values include: \\['ict', 'retail'\\]"):
        stats.select_series(frame, ds, "employed", {"industry": "finance"})


def test_period_ranges_compare_on_prefixes():
    frame, ds = DATASETS["retrenchment"]
    series, _, _ = stats.select_series(frame, ds, "retrench", {"industry": "ict"}, start="2024", end="2024")
    assert list(series.index) == ["2024-Q1", "2024-Q2", "2024-Q3", "2024-Q4"]


def test_trend_change_cagr_peak_and_year_on_year():
    t = stats.trend(pd.Series([100.0, 150.0, 121.0], index=["2020", "2021", "2022"]))
    assert t["change_pct"] == pytest.approx(21.0) and t["cagr_pct"] == pytest.approx(10.0)
    assert t["peak"] == ("2021", 150.0) and t["latest_yoy"] == ("2022", pytest.approx(-19.333, abs=1e-3))


def test_quarterly_year_on_year_compares_the_same_quarter():
    yoy = stats.year_on_year(pd.Series([10.0, 20, 30, 40, 5, 15, 25, 35], index=QUARTERS))
    assert yoy["2024-Q1"] == pytest.approx(-50.0) and np.isnan(yoy["2023-Q4"])


def test_to_annual_combines_by_the_measures_rule_and_drops_partial_years():
    series = pd.Series([1.0, 2, 3, 4, 5, 6], index=["2023-Q1", "2023-Q2", "2023-Q3", "2023-Q4", "2024-Q1", "2024-Q2"])
    assert stats.to_annual(series, "sum").to_dict() == {"2023": 10.0}
    assert stats.to_annual(series, "mean").to_dict() == {"2023": 2.5}
    assert stats.to_annual(series, "max").to_dict() == {"2023": 4.0}
    assert dataset("x", {"n": ("a",)}).rule_over_time("n") == "sum" and dataset("x").rule_over_time("rate") == "mean"


def test_months_make_one_season_a_year_by_each_measures_rule():
    frame, ds = DATASETS["psi_months"]
    seasons = stats.season(frame, [7, 8, 9, 10], ds.rule_over_time)
    south = seasons[seasons["region"] == "south"]
    highest = south[south["measure"] == "highest_psi"].set_index("period")["value"].to_dict()
    assert highest == {"2015": 100.0, "2016": 200.0, "2017": 300.0, "2018": 400.0}  # its highest month; 2019 is short
    assert set(south[south["measure"] == "unhealthy_days"]["value"]) == {10.0}  # 1 + 2 + 3 + 4 days, added up
    assert list(seasons.columns) == list(frame.columns)
    assert stats.months_label([7, 8, 9, 10]) == "Jul-Oct" and stats.months_label([1, 2, 12]) == "Jan, Feb, Dec"
    ws, t = tools()
    measures = call(t["load_dataset"], dataset_id="psi_months")["measures"]
    assert [measures[m]["periods_combine_as"] for m in ("highest_psi", "unhealthy_days")] == ["highest", "total"]
    with pytest.raises(stats.AnalysisError, match="only applies to monthly data"):
        stats.season(EMPLOYMENT, [7], ds.rule_over_time)


def test_a_season_lines_up_with_yearly_fire_counts():
    ws, t = tools()
    season = {"dataset_id": "psi_months", "measure": "highest_psi", "filters": {"region": "south"},
              "label": "Highest PSI", "months": [10, 7, 8, 9]}
    first = call(t["compute_trend"], series=season)["facts"][0]
    assert (first["label"], first["value"]) == ("Highest PSI (Jul-Oct, highest), 2015", 100.0)
    fires = {"dataset_id": "fire_years", "measure": "hotspots", "filters": {}, "label": "Hotspots"}
    out = call(t["compute_correlation"], a=fires, b=season)
    assert out["note"] == "aligned on matching periods" and "2015 to 2018 (n=4)" in out["facts"][0]["label"]
    with pytest.raises(stats.AnalysisError, match="only applies to monthly data"):
        call(t["compute_trend"], series={**fires, "months": [7]})
    with pytest.raises(ValueError, match="from 1 \\(January\\) to 12"):
        call(t["compute_trend"], series={**season, "months": [13]})


def test_correlation_separates_shared_trend_from_related_changes():
    years = [str(y) for y in range(2010, 2020)]
    trend = np.arange(10) * 10.0
    a = pd.Series(trend + [0, 3, 0, 3, 0, 3, 0, 3, 0, 3], index=years)
    b = pd.Series(trend + [3, 0, 3, 0, 3, 0, 3, 0, 3, 0], index=years)
    r = stats.correlation(a, b)
    assert r["r_levels"] > 0.95 and r["r_changes"] < 0.5 and r["n"] == 10
    with pytest.raises(stats.AnalysisError, match="need at least 4"):
        stats.correlation(a.iloc[:3], b.iloc[:3])


def test_round_value_by_unit():
    assert (round_value(24.6913, "%"), round_value(0.9234, "Pearson r"), round_value(111600.0, "persons"),
            round_value(765497.53, "S$ million")) == (24.7, 0.92, 111600, 765497.53)


# -- tools ------------------------------------------------------------------------------------------------------
def test_load_dataset_registers_a_source_once_with_its_warning():
    ws, t = tools()
    first = call(t["load_dataset"], dataset_id="employment")
    call(t["load_dataset"], dataset_id="employment")
    assert first["source_id"] == "S1" and len(ws.sources) == 1 and ws.sources[0]["warning"] == "source down"
    assert first["measures"]["employed"]["can_add_up_across"] == ["industry", "sex"]


def test_compute_trend_records_numbered_facts():
    ws, t = tools()
    out = call(t["compute_trend"], series=ICT, start="2020", end="2024")
    labels = [f["label"] for f in out["facts"]]
    assert labels[:5] == [f"ICT, {y}" for y in YEARS]
    assert "ICT, change 2020 to 2024" in labels and "ICT, compound annual growth 2020 to 2024" in labels
    change = next(f for f in out["facts"] if f["label"] == "ICT, change 2020 to 2024")
    assert change["value"] == 40.0 and change["unit"] == "%" and change["sources"] == ["S1"]
    assert [f["id"] for f in ws.facts] == [f"F{i}" for i in range(1, len(ws.facts) + 1)]
    # Each year's change is a fact too, so the agent does not need one call per pair of years.
    assert [lbl for lbl in labels if "year-on-year" in lbl] == [f"ICT, year-on-year change to {y}" for y in YEARS[1:]]


def test_rank_changes_compares_every_group_over_the_same_periods():
    ws, t = tools()
    out = call(t["rank_changes"], dataset_id="prices", measure="price", group_by="town", start="2020")
    assert (out["from"], out["to"], out["groups_compared"]) == ("2020-Q1", "2020-Q3", 2)
    assert [r["town"] for r in out["ranking"]] == ["A", "B"]  # +50% before +10%
    assert out["excluded_for_missing_data"] == ["C", "D"]  # C lacks 2020-Q3, D has nothing from 2020
    pct = {f["label"]: f["value"] for f in out["facts"] if f["unit"] == "%"}
    assert pct == {"percentage change in price, town = A, 2020-Q1 to 2020-Q3": 50.0,
                   "percentage change in price, town = B, 2020-Q1 to 2020-Q3": 10.0}
    by_amount = call(t["rank_changes"], dataset_id="prices", measure="price", group_by="town", start="2020",
                     by="absolute", lowest_first=True, top=1)
    assert [r["town"] for r in by_amount["ranking"]] == ["B"] and len(by_amount["facts"]) == 4
    # Summing rules still apply: industries are compared with sexes added up, as the dataset allows.
    jobs = call(t["rank_changes"], dataset_id="employment", measure="employed", group_by="industry", start="2020")
    assert [r["industry"] for r in jobs["ranking"]] == ["ict", "retail"]


def test_compare_groups_labels_say_what_was_filtered():
    _, t = tools()
    out = call(t["compare_groups"], dataset_id="employment", measure="employed", group_by="sex",
               filters={"industry": "ict"}, period="2024")
    assert out["facts"][0]["label"] == "employed (industry = ict), sex = female, 2024"


def test_compare_groups_gives_shares_that_add_to_100():
    _, t = tools()
    out = call(t["compare_groups"], dataset_id="employment", measure="employed", group_by="industry", period="2024")
    shares = [f["value"] for f in out["facts"] if f["unit"] == "%"]
    assert sum(shares) == pytest.approx(100, abs=0.2) and out["groups_total"] == 2


def test_correlation_tool_records_levels_and_changes():
    _, t = tools()
    out = call(t["compute_correlation"], a=ICT, b={"dataset_id": "employment", "measure": "employed",
                                                    "filters": {"industry": "retail"}, "label": "Retail"})
    assert out["facts"][0]["unit"] == "Pearson r" and out["facts"][0]["value"] == 1.0


def test_scatter_chart_plots_the_points_its_correlation_uses():
    ws, t = tools()
    gdp = {"dataset_id": "gdp", "measure": "gdp", "filters": {}, "label": "ICT GDP"}
    out = call(t["create_chart"], kind="scatter", title="Jobs and output", series=[ICT, gdp])
    chart = ws.charts[0]
    trace = chart["spec"]["data"][0]
    assert out["points"] == 5 and trace["x"] == [200.0, 220.0, 240.0, 260.0, 280.0] and trace["y"][0] == 50.0
    assert trace["text"] == YEARS and trace["mode"] == "markers+text"  # few points: each labelled with its year
    assert chart["spec"]["layout"]["xaxis"]["title"]["text"] == "ICT (persons)" and chart["sources"] == ["S1", "S2"]
    assert chart["table"]["rows"][-1] == ["2024", 280.0, 70.0]
    # A quarterly series against a yearly one is put on calendar years, as compute_correlation does.
    retrench = {"dataset_id": "retrenchment", "measure": "retrench", "filters": {"industry": "ict"}, "label": "Cuts"}
    call(t["create_chart"], kind="scatter", title="Jobs and cuts", series=[ICT, retrench])
    assert [row[0] for row in ws.charts[1]["table"]["rows"]] == ["2023", "2024"]
    with pytest.raises(stats.AnalysisError, match="fewer than two periods"):  # from 2024 on, they share one year
        t["create_chart"].invoke({"kind": "scatter", "title": "x", "series": [ICT, retrench], "start": "2024",
                                  "reason": "x"})
    with pytest.raises(stats.AnalysisError, match="exactly two series"):
        t["create_chart"].invoke({"kind": "scatter", "title": "x", "series": [ICT], "reason": "x"})


def test_pie_shows_parts_of_a_whole_only():
    ws, t = tools()
    employed = {"dataset_id": "employment", "measure": "employed", "filters": {"sex": "female"}, "label": "Women"}
    out = call(t["create_chart"], kind="pie", title="Women employed by industry", series=[employed],
               group_by="industry")
    pie = ws.charts[0]
    assert out["points"] == 2 and pie["spec"]["data"][0]["labels"] == ["retail", "ict"]
    assert pie["table"]["rows"] == [["retail", 340.0, 70.8], ["ict", 140.0, 29.2]]  # 2024: shares of 480
    price = {"dataset_id": "prices", "measure": "price", "filters": {}, "label": "Price"}
    with pytest.raises(stats.AnalysisError, match="cannot be added up across town"):  # prices are not parts
        t["create_chart"].invoke({"kind": "pie", "title": "x", "series": [price], "group_by": "town", "reason": "x"})


def test_pie_combines_the_smallest_slices_into_other():
    from app.analysis.charts import pie_chart
    spec, table = pie_chart(pd.Series({f"g{i}": float(10 - i) for i in range(10)}), "Ten groups", "persons")
    assert spec["data"][0]["labels"][-1] == "Other" and len(table["rows"]) == 8
    assert table["rows"][-1][1] == 3.0 + 2.0 + 1.0  # the three smallest


def test_distribution_records_quartiles_by_group_and_draws_box_and_dot_plots():
    ws, t = tools()
    price = {"dataset_id": "prices", "measure": "price", "filters": {}, "label": "Price"}
    out = call(t["compute_distribution"], series=price, group_by="town")
    medians = {f["label"]: f["value"] for f in out["facts"] if "median" in f["label"]}
    assert medians == {"Price, C: median of 2 values": 315.0, "Price, B: median of 3 values": 210.0,
                       "Price, A: median of 3 values": 110.0, "Price, D: median of 1 values": 90.0}
    call(t["create_chart"], kind="box", title="Price spread", series=[price], group_by="town")
    box = ws.charts[0]
    assert [tr["name"] for tr in box["spec"]["data"]] == ["C", "B", "A", "D"]  # highest median first
    assert box["table"]["rows"][2] == ["A", 3, 100.0, 105.0, 110.0, 130.0, 150.0]
    call(t["create_chart"], kind="dot", title="Every price", series=[price], group_by="town")
    dots = ws.charts[1]["spec"]["data"][2]
    assert dots["y"] == [100.0, 110.0, 150.0] and dots["boxpoints"] == "all"


def test_distribution_pools_open_dimensions_and_says_so():
    _, t = tools()
    employed = {"dataset_id": "employment", "measure": "employed", "filters": {"industry": "ict"}, "label": "ICT"}
    out = call(t["compute_distribution"], series=employed)
    assert out["pooled"] == ["sex"] and "median of 10 values" in out["facts"][0]["label"]  # 5 years x 2 sexes


def test_heatmap_rows_by_group_and_columns_by_period_or_a_second_dimension():
    ws, t = tools()
    employed = {"dataset_id": "employment", "measure": "employed", "filters": {}, "label": "Employed"}
    call(t["create_chart"], kind="heatmap", title="By industry and year", series=[employed], group_by="industry")
    heat = ws.charts[0]["spec"]["data"][0]
    assert heat["y"] == ["retail", "ict"] and heat["text"][0] == YEARS  # highest row first; columns in time order
    assert heat["x"][0] == "2020-07-01" and ws.charts[0]["spec"]["layout"]["xaxis"]["type"] == "date"
    assert heat["z"][1] == [200.0, 220.0, 240.0, 260.0, 280.0]  # ICT, both sexes added up (allowed)
    call(t["create_chart"], kind="heatmap", title="By industry and sex", series=[employed], group_by="industry",
         column_by="sex")
    assert ws.charts[1]["spec"]["data"][0]["z"] == [[340.0, 340.0], [140.0, 140.0]]  # 2024
    assert ws.charts[1]["spec"]["layout"]["xaxis"]["type"] == "category"  # columns that are not periods
    assert ws.charts[1]["table"]["rows"][0] == ["retail", "female", 340.0]


def test_grouped_and_stacked_bars_split_each_bar_by_a_second_dimension():
    ws, t = tools()
    employed = {"dataset_id": "employment", "measure": "employed", "filters": {}, "label": "Employed"}
    for kind, mode in (("grouped_bar", "group"), ("stacked_bar", "stack")):
        out = call(t["create_chart"], kind=kind, title="By industry and sex", series=[employed], group_by="industry",
                   column_by="sex")
        chart = ws.charts[-1]
        assert chart["spec"]["layout"]["barmode"] == mode
        assert [tr["name"] for tr in chart["spec"]["data"]] == ["female", "male"]
        assert chart["table"]["rows"] == [["retail", 340.0, 340.0], ["ict", 140.0, 140.0]]  # 2024, largest first
        assert out["note"] == "bars: industry; split by sex in 2024"
    strict = {**employed, "dataset_id": "employment_strict"}
    with pytest.raises(stats.AnalysisError, match="cannot be added up across sex: use grouped bars"):
        t["create_chart"].invoke({"kind": "stacked_bar", "title": "x", "series": [strict], "group_by": "industry",
                                  "column_by": "sex", "reason": "x"})
    with pytest.raises(stats.AnalysisError, match="needs column_by"):
        t["create_chart"].invoke({"kind": "grouped_bar", "title": "x", "series": [employed], "group_by": "industry",
                                  "reason": "x"})


def test_a_map_fills_towns_by_value_and_says_what_it_left_out():
    ws, t = tools()
    price = {"dataset_id": "towns", "measure": "price", "filters": {}, "label": "Price"}
    out = call(t["create_chart"], kind="map", title="Prices by town", series=[price], group_by="town")
    traces = ws.charts[0]["spec"]["data"]
    filled = {tr["name"].split(":")[0]: tr["fillcolor"] for tr in traces if tr.get("name")}
    assert set(filled) == {"Central Area", "Kallang/Whampoa", "Tampines", "Bedok"}  # Atlantis is not a place
    assert filled["Central Area"] == "#800026" and filled["Bedok"] == "#ffffcc"  # darkest is highest
    assert "not on the map: Atlantis" in out["note"] and "Central Area planning areas" in out["note"]
    assert ws.charts[0]["spec"]["layout"]["yaxis"]["scaleanchor"] == "x"  # longitude and latitude to scale
    assert traces[-1]["marker"]["showscale"] and traces[-1]["marker"]["cmax"] == 900.0


def test_nea_regions_are_mapped_as_points_and_other_labels_get_no_map():
    ws, t = tools()
    psi = {"dataset_id": "psi", "measure": "highest_psi", "filters": {}, "label": "Highest PSI"}
    call(t["create_chart"], kind="map", title="Haze, September 2015", series=[psi], group_by="region")
    points = next(tr for tr in ws.charts[0]["spec"]["data"] if tr.get("mode") == "markers+text")
    assert points["text"] == ["south", "west", "north", "central", "east"] and points["x"][0] == 103.82
    employed = {"dataset_id": "employment", "measure": "employed", "filters": {"sex": "male"}, "label": "Men"}
    with pytest.raises(stats.AnalysisError, match="no map for industry"):
        t["create_chart"].invoke({"kind": "map", "title": "x", "series": [employed], "group_by": "industry",
                                  "reason": "x"})


def test_a_map_of_the_region_shows_countries_and_marks_singapore():
    ws, t = tools()
    fires = {"dataset_id": "fires", "measure": "hotspots", "filters": {}, "label": "Hotspots"}
    call(t["create_chart"], kind="map", title="Fire hotspots, 2015 season", series=[fires], group_by="country")
    traces = ws.charts[0]["spec"]["data"]
    assert {tr["name"].split(":")[0] for tr in traces if tr.get("name")} == {"Indonesia", "Malaysia", "Brunei"}
    star = next(tr for tr in traces if (tr.get("marker") or {}).get("symbol") == "star")
    assert star["text"] == ["Singapore"] and star["x"] == [103.82]
    assert min(x for x in traces[0]["x"] if x is not None) < 100  # the outline is the region, not Singapore


def test_list_records_refuses_time_series_and_counts_tables():
    _, t = tools()
    with pytest.raises(stats.AnalysisError, match="time series"):
        t["list_records"].invoke({"dataset_id": "employment", "reason": "x"})
    out = call(t["list_records"], dataset_id="stations", filters={"status": "planned"})
    assert out["count"] == 2 and [r["station"] for r in out["records"]] == ["B", "C"]


def test_line_chart_with_two_units_gets_two_axes_and_a_data_table():
    ws, t = tools()
    gdp = {"dataset_id": "gdp", "measure": "gdp", "filters": {}, "label": "GDP"}
    out = call(t["create_chart"], kind="line", title="ICT", series=[ICT, gdp])
    chart = ws.charts[0]
    assert out["chart_id"] == "C1" and chart["spec"]["data"][0]["y"] == [200.0, 220.0, 240.0, 260.0, 280.0]
    assert chart["spec"]["data"][1]["yaxis"] == "y2"
    assert chart["spec"]["layout"]["yaxis2"]["title"]["text"] == "S$ million"
    assert chart["table"]["rows"][0] == ["2020", 200.0, 50.0] and chart["sources"] == ["S1", "S2"]
    assert chart["spec"]["layout"]["xaxis"]["type"] == "date" and chart["spec"]["data"][0]["text"][0] == "2020"


def test_periods_sit_in_the_middle_so_yearly_and_monthly_lines_line_up():
    assert [period_date(p) for p in ["2024", "2024-Q1", "2024-Q4", "2024-03", "FY2024", "Ang Mo Kio"]] == [
        "2024-07-01", "2024-02-15", "2024-11-15", "2024-03-15", "2024-10-01", None]
    yearly = pd.Series([153267.0], index=["2015"])
    monthly = pd.Series([88.0, 322.0], index=["2015-08", "2015-09"])
    spec, table = line_chart([("Hotspots", yearly, "hotspots"), ("PSI", monthly, "PSI")], "Haze")
    assert [t["x"] for t in spec["data"]] == [["2015-07-01"], ["2015-08-15", "2015-09-15"]]
    assert spec["layout"]["yaxis2"]["tickmode"] == "auto" and spec["layout"]["legend"]["yanchor"] == "bottom"
    assert [row[0] for row in table["rows"]] == ["2015", "2015-08", "2015-09"]  # the table keeps the periods
    spec, _ = line_chart([("Price", pd.Series([1.0, 2.0], index=["North", "South"]), "S$")], "Not periods")
    assert spec["layout"]["xaxis"]["type"] == "category" and spec["data"][0]["x"] == ["North", "South"]


def test_bar_chart_ranks_groups_in_one_period_and_skips_groups_without_a_value():
    ws, t = tools()
    price = {"dataset_id": "prices", "measure": "price", "filters": {}, "label": "Price"}
    out = call(t["create_chart"], kind="bar", title="Prices by town", series=[price], group_by="town")
    latest = ws.charts[0]  # the latest period, 2020-Q3: C has no value then and D none at all, so both are left out
    assert out == {"chart_id": "C1", "title": "Prices by town", "points": 2}
    assert latest["spec"]["data"][0]["x"] == ["B", "A"] and latest["spec"]["data"][0]["y"] == [220.0, 150.0]
    assert latest["table"] == {"columns": ["group", "S$"], "rows": [["B", 220.0], ["A", 150.0]]}
    call(t["create_chart"], kind="bar", title="Prices, 2020-Q2", series=[price], group_by="town", period="2020-Q2")
    assert ws.charts[1]["spec"]["data"][0]["x"] == ["C", "B", "A"] and ws.charts[1]["sources"] == ["S1"]


def test_bar_chart_needs_a_group():
    _, t = tools()
    with pytest.raises(stats.AnalysisError, match="group_by must be one of"):
        t["create_chart"].invoke({"kind": "bar", "title": "x", "series": [ICT], "reason": "x"})


# -- the ReAct loop and the agents --------------------------------------------------------------------------------
def factory(model):
    return lambda tier, *, tools=None, schema=None: model


def test_tool_loop_feeds_results_and_errors_back_until_the_model_answers():
    ws, t = tools()
    model = ScriptedChatModel(script=[
        reply(tool_calls=[{"name": "compute_trend", "args": {"series": {**ICT, "measure": "jobs"}, "reason": "try"}}]),
        reply(tool_calls=[{"name": "compute_trend", "args": {"series": ICT, "reason": "fixed"}}]),
        reply("Computed the ICT trend [F1-F8]."),
    ])
    answer = run_tool_loop(factory(model), node="analytics", system="s", task="t", tools=list(t.values()))
    assert answer == "Computed the ICT trend [F1-F8]."
    observations = [m.content for m in model.requests[-1] if m.type == "tool"]
    assert observations[0].startswith("ERROR: unknown measure 'jobs'") and '"facts"' in observations[1]
    assert len(ws.facts) > 0


def test_tool_loop_stops_at_the_step_limit():
    _, t = tools()
    looping = ScriptedChatModel(script=[reply(tool_calls=[{"name": "load_dataset",
                                                            "args": {"dataset_id": "employment", "reason": "x"}}])] * 3)
    answer = run_tool_loop(factory(looping), node="extraction", system="s", task="t", tools=list(t.values()),
                           max_steps=3)
    assert answer.startswith("Stopped after 3 tool steps")


def test_agents_extract_then_analyse_with_shared_source_ids():
    model = ScriptedChatModel(script=[
        reply(tool_calls=[{"name": "load_dataset", "args": {"dataset_id": "employment", "reason": "needed"}}]),
        reply("Loaded employment (S1)."),
        reply(tool_calls=[{"name": "compute_trend", "args": {"series": ICT, "reason": "main trend"}}]),
        reply("Computed the trend."),
    ])
    workers = AgentWorkers(factory(model), store=FakeStore())
    extraction = workers.extract("load employment", "ICT employment?")
    assert extraction["summary"] == "Loaded employment (S1)." and extraction["sources"][0]["id"] == "S1"
    assert extraction["datasets"][0]["dataset_id"] == "employment"
    analytics = workers.analyse("trend", "ICT employment?", extraction)
    assert analytics["facts"][0]["sources"] == ["S1"] and len(analytics["sources"]) == 1


def test_a_second_extraction_keeps_what_the_first_loaded():
    model = ScriptedChatModel(script=[
        reply(tool_calls=[{"name": "load_dataset", "args": {"dataset_id": "employment", "reason": "needed"}}]),
        reply("Loaded employment (S1)."),
        reply("Found nothing else."),  # the retry loads nothing new
        reply(tool_calls=[{"name": "load_dataset", "args": {"dataset_id": "gdp", "reason": "more"}}]),
        reply("Loaded GDP (S2)."),
    ])
    workers = AgentWorkers(factory(model), store=FakeStore())
    first = workers.extract("load employment", "q")
    retry = workers.extract("find more", "q", first)
    assert [d["source_id"] for d in retry["datasets"]] == ["S1"] and retry["sources"] == first["sources"]
    assert "Already loaded (no need to load again): S1" in model.requests[2][1].content
    third = workers.extract("add GDP", "q", retry)
    assert [(d["source_id"], d["dataset_id"]) for d in third["datasets"]] == [("S1", "employment"), ("S2", "gdp")]


def test_a_second_analysis_keeps_the_first_facts_and_charts():
    model = ScriptedChatModel(script=[
        reply(tool_calls=[{"name": "compute_trend", "args": {"series": ICT, "reason": "main trend"}}]),
        reply("Computed the trend."),
        reply("Nothing more to compute."),  # the reviewer's follow-up adds nothing
    ])
    workers = AgentWorkers(factory(model), store=FakeStore())
    extraction = {"datasets": [{"dataset_id": "employment"}], "sources": []}
    first = workers.analyse("trend", "q", extraction)
    second = workers.analyse("more", "q", extraction, first)
    assert second["facts"] == first["facts"] and len(first["facts"]) > 3
    assert f"facts F1-F{len(first['facts'])}" in model.requests[2][1].content


def test_analysis_without_data_computes_nothing():
    workers = AgentWorkers(factory(ScriptedChatModel(script=[])), store=FakeStore())
    assert workers.analyse("x", "q", {"datasets": []})["facts"] == []
