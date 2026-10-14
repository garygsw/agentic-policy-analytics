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
STATIONS = pd.DataFrame([{"period": "", "station": s, "status": st, "measure": "exits", "unit": "exits", "value": v}
                         for s, st, v in (("A", "open", 3.0), ("B", "planned", 0.0), ("C", "planned", 0.0))])


def dataset(key, summable=None, frequency="annual"):
    return CuratedDataset(key, key.title(), "Agency", "topic", "desc", "datagovsg", (key,), frequency,
                          clean=lambda raw: None, summable=summable or {})


DATASETS = {
    "employment": (EMPLOYMENT, dataset("employment", {"employed": ("industry", "sex")})),
    "retrenchment": (RETRENCH, dataset("retrenchment", frequency="quarterly")),  # nothing may be added up
    "stations": (STATIONS, dataset("stations", {"exits": ("station", "status")}, "none")),
    "gdp": (GDP, dataset("gdp")),
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


def test_to_annual_sums_counts_averages_rates_and_drops_partial_years():
    series = pd.Series([1.0, 2, 3, 4, 5, 6], index=["2023-Q1", "2023-Q2", "2023-Q3", "2023-Q4", "2024-Q1", "2024-Q2"])
    assert stats.to_annual(series, summable=True).to_dict() == {"2023": 10.0}
    assert stats.to_annual(series, summable=False).to_dict() == {"2023": 2.5}


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


def test_bar_chart_needs_a_group():
    _, t = tools()
    with pytest.raises(stats.AnalysisError, match="needs group_by"):
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


def test_analysis_without_data_computes_nothing():
    workers = AgentWorkers(factory(ScriptedChatModel(script=[])), store=FakeStore())
    assert workers.analyse("x", "q", {"datasets": []})["facts"] == []
