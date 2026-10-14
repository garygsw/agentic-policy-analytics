"""Tools for the Extraction and Analytics agents.

Each tool takes a `reason` (why the agent is calling it), which is shown in the live agent trace. Tools that
compute register numbered facts and charts in the Workspace; the agent only ever chooses what to compute.
"""
import json
from typing import Literal

import pandas as pd
from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field

from app.analysis import stats
from app.analysis.charts import bar_chart, line_chart
from app.analysis.workspace import Workspace, profile
from app.data import catalogue

MAX_FACT_POINTS = 20  # series this short get one fact per point, so any year can be cited
MAX_GROUPS = 15
MAX_RECORDS = 60

REASON = Field(description="One short sentence: why this call helps answer the question.")


def _json(value) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)


class SeriesSpec(BaseModel):
    dataset_id: str = Field(description="Dataset key or data.gov.sg ID, as loaded")
    measure: str
    filters: dict[str, str] = Field(default_factory=dict,
                                    description="dimension -> value. Leave a dimension out to add it up "
                                                "(only allowed where the profile says the measure can be added up)")
    label: str = Field(description="Short readable name, e.g. 'Tech-sector employment'")


# -- extraction ------------------------------------------------------------------------------------------------
class SearchArgs(BaseModel):
    query: str = Field(description="Keywords describing the data needed")
    reason: str = REASON


class LoadArgs(BaseModel):
    dataset_id: str = Field(description="A curated dataset key, or a data.gov.sg dataset ID from search_datasets")
    reason: str = REASON


def extraction_tools(ws: Workspace) -> list[StructuredTool]:
    def search_datasets(query: str, reason: str) -> str:
        return _json(catalogue.search(query, 8))

    def load_dataset(dataset_id: str, reason: str) -> str:
        loaded, source_id = ws.dataset(dataset_id)
        return _json(profile(loaded, source_id))

    return [
        StructuredTool.from_function(search_datasets, name="search_datasets", args_schema=SearchArgs,
                                     description="Search all 4,655 data.gov.sg datasets by keywords. Use it only "
                                                 "when no curated dataset covers the topic."),
        StructuredTool.from_function(load_dataset, name="load_dataset", args_schema=LoadArgs,
                                     description="Load one dataset and return its profile: measures and units, "
                                                 "dimensions, coverage, data-quality report and caveats."),
    ]


# -- analytics -------------------------------------------------------------------------------------------------
class DescribeArgs(BaseModel):
    dataset_id: str
    dimension: str = Field(description="Dimension whose values to list (to choose filters)")
    reason: str = REASON


class TrendArgs(BaseModel):
    series: SeriesSpec
    start: str | None = Field(default=None, description="First period to include, e.g. '2020' or '2020-Q1'")
    end: str | None = Field(default=None, description="Last period to include")
    reason: str = REASON


class CompareArgs(BaseModel):
    dataset_id: str
    measure: str
    group_by: str = Field(description="Dimension to compare across, e.g. 'industry' or 'town'")
    filters: dict[str, str] = Field(default_factory=dict)
    period: str | None = Field(default=None, description="Period to compare; default: latest available")
    reason: str = REASON


class CorrelateArgs(BaseModel):
    a: SeriesSpec
    b: SeriesSpec
    start: str | None = None
    end: str | None = None
    reason: str = REASON


class RecordsArgs(BaseModel):
    dataset_id: str
    filters: dict[str, str] = Field(default_factory=dict)
    reason: str = REASON


class ChartArgs(BaseModel):
    kind: Literal["line", "bar"]
    title: str
    series: list[SeriesSpec] = Field(description="line: one to four series over time; bar: exactly one series")
    group_by: str | None = Field(default=None, description="bar only: the dimension shown as bars")
    period: str | None = Field(default=None, description="bar only: the period shown; default: latest")
    start: str | None = None
    end: str | None = None
    reason: str = REASON


def analytics_tools(ws: Workspace) -> list[StructuredTool]:
    def load(spec: SeriesSpec | dict):
        spec = SeriesSpec(**spec) if isinstance(spec, dict) else spec
        loaded, source_id = ws.dataset(spec.dataset_id)
        return spec, loaded, source_id

    def describe_dataset(dataset_id: str, dimension: str, reason: str) -> str:
        loaded, _ = ws.dataset(dataset_id)
        if dimension not in stats.dimensions(loaded.frame):
            raise stats.AnalysisError(f"unknown dimension '{dimension}'; dimensions: {stats.dimensions(loaded.frame)}")
        values = sorted(loaded.frame[dimension].unique())
        return _json({"dimension": dimension, "values": values[:100], "total_distinct": len(values)})

    def compute_trend(series: SeriesSpec, start: str | None = None, end: str | None = None, reason: str = "") -> str:
        spec, loaded, source_id = load(series)
        values, unit, notes = stats.select_series(loaded.frame, loaded.dataset, spec.measure, spec.filters, start, end)
        method = "; ".join(notes) or "as published"
        periods = list(values.index) if len(values) <= MAX_FACT_POINTS else [values.index[0], values.index[-1]]
        facts = [ws.add_fact(f"{spec.label}, {period}", float(values[period]), unit, [source_id], method)
                 for period in periods]
        t = stats.trend(values)
        span = f"{t['start'][0]} to {t['end'][0]}"
        if t["change_pct"] is not None:
            facts.append(ws.add_fact(f"{spec.label}, change {span}", t["change_pct"], "%", [source_id], method))
        if t["cagr_pct"] is not None:
            facts.append(ws.add_fact(f"{spec.label}, compound annual growth {span}", t["cagr_pct"], "%",
                                     [source_id], method))
        if t["peak"][0] != t["end"][0]:
            facts.append(ws.add_fact(f"{spec.label}, peak ({t['peak'][0]})", t["peak"][1], unit, [source_id], method))
        if t["latest_yoy"]:
            facts.append(ws.add_fact(f"{spec.label}, year-on-year change to {t['latest_yoy'][0]}", t["latest_yoy"][1],
                                     "%", [source_id], method))
        return _json({"facts": [f for f in ws.facts if f["id"] in facts], "periods": len(values), "notes": notes})

    def compare_groups(dataset_id: str, measure: str, group_by: str, filters: dict | None = None,
                       period: str | None = None, reason: str = "") -> str:
        loaded, source_id = ws.dataset(dataset_id)
        frame, filters = loaded.frame, dict(filters or {})
        if group_by not in stats.dimensions(frame):
            raise stats.AnalysisError(f"unknown dimension '{group_by}'")
        period = period or frame[frame["measure"] == measure]["period"].max()
        values = {}
        for group in sorted(frame[group_by].unique()):
            try:
                series, unit, _ = stats.select_series(frame, loaded.dataset, measure, {**filters, group_by: group},
                                                      period, period)
                values[group] = float(series.iloc[-1])
            except stats.AnalysisError as error:
                if "cannot be added" in str(error):
                    raise
        if not values:
            raise stats.AnalysisError(f"no {measure} values for period {period}")
        ranked = sorted(values.items(), key=lambda kv: kv[1], reverse=True)[:MAX_GROUPS]
        total = sum(values.values()) if group_by in loaded.dataset.summable.get(measure, ()) else None
        # The label carries every filter, so a fact read on its own says exactly what it counts.
        scope = f" ({', '.join(f'{k} = {v}' for k, v in filters.items())})" if filters else ""
        facts = []
        for group, value in ranked:
            facts.append(ws.add_fact(f"{measure}{scope}, {group_by} = {group}, {period}", value, unit, [source_id]))
            if total:
                facts.append(ws.add_fact(f"share of {measure}{scope}, {group_by} = {group}, {period}",
                                         value / total * 100, "%", [source_id], f"share of total across {group_by}"))
        return _json({"period": period, "facts": [f for f in ws.facts if f["id"] in facts],
                      "groups_shown": len(ranked), "groups_total": len(values)})

    def compute_correlation(a: SeriesSpec, b: SeriesSpec, start: str | None = None, end: str | None = None,
                            reason: str = "") -> str:
        series = []
        for spec in (a, b):
            spec, loaded, source_id = load(spec)
            values, unit, _ = stats.select_series(loaded.frame, loaded.dataset, spec.measure, spec.filters, start, end)
            summable = bool(loaded.dataset.summable.get(spec.measure))
            series.append((spec, source_id, values, stats.to_annual(values, summable)))
        needs_annual = stats.is_yearly(series[0][2]) != stats.is_yearly(series[1][2])
        x, y = ((series[0][3], series[1][3]) if needs_annual else (series[0][2], series[1][2]))
        r = stats.correlation(x, y)
        label = f"{series[0][0].label} vs {series[1][0].label}, {r['from']} to {r['to']} (n={r['n']})"
        sources = [series[0][1], series[1][1]]
        if r["r_levels"] is None:
            raise stats.AnalysisError("a series is constant over these periods, so correlation is undefined")
        facts = [ws.add_fact(f"correlation of levels: {label}", r["r_levels"], "Pearson r", sources)]
        if r["r_changes"] is not None:
            facts.append(ws.add_fact(f"correlation of period-to-period changes: {label}", r["r_changes"],
                                     "Pearson r", sources))
        note = "aligned on calendar years" if needs_annual else "aligned on matching periods"
        return _json({"facts": [f for f in ws.facts if f["id"] in facts], "note": note,
                      "caution": "Correlation shows co-movement, not cause and effect."})

    def list_records(dataset_id: str, filters: dict | None = None, reason: str = "") -> str:
        loaded, source_id = ws.dataset(dataset_id)
        if (loaded.frame["period"] != "").any():
            raise stats.AnalysisError(f"{dataset_id} is a time series: use compute_trend or compare_groups, which "
                                      "return values; list_records only counts rows of tables without periods")
        rows = loaded.frame
        for dim, value in (filters or {}).items():
            rows = rows[rows[dim].str.lower() == str(value).lower()]
        keys = stats.dimensions(loaded.frame)
        records = rows[keys].drop_duplicates()
        label = " and ".join(f"{k} = {v}" for k, v in (filters or {}).items()) or "all"
        fact = ws.add_fact(f"number of records in {loaded.dataset.title} where {label}", len(records), "records",
                           [source_id])
        return _json({"fact": fact, "count": len(records),
                      "records": records.head(MAX_RECORDS).to_dict(orient="records")})

    def create_chart(kind: str, title: str, series: list[SeriesSpec], group_by: str | None = None,
                     period: str | None = None, start: str | None = None, end: str | None = None,
                     reason: str = "") -> str:
        specs = [load(s) for s in series]
        sources = list(dict.fromkeys(source_id for _, _, source_id in specs))
        if kind == "line":
            lines = []
            for spec, loaded, _ in specs[:4]:
                values, unit, _ = stats.select_series(loaded.frame, loaded.dataset, spec.measure, spec.filters,
                                                      start, end)
                lines.append((spec.label, values, unit))
            chart, table = line_chart(lines, title)
        else:
            if not group_by:
                raise stats.AnalysisError("a bar chart needs group_by")
            spec, loaded, _ = specs[0]
            frame = loaded.frame
            period = period or frame[frame["measure"] == spec.measure]["period"].max()
            values, unit = {}, ""
            for group in sorted(frame[group_by].unique()):
                try:
                    s, unit, _ = stats.select_series(frame, loaded.dataset, spec.measure,
                                                     {**spec.filters, group_by: group}, period, period)
                    values[group] = float(s.iloc[-1])
                except stats.AnalysisError:
                    continue
            ranked = pd.Series(values).sort_values(ascending=False).head(MAX_GROUPS)
            chart, table = bar_chart(ranked, title, unit)
        chart_id = ws.add_chart(title, chart, table, sources)
        return _json({"chart_id": chart_id, "title": title, "points": len(table["rows"])})

    return [
        StructuredTool.from_function(describe_dataset, name="describe_dataset", args_schema=DescribeArgs,
                                     description="List every value of one dimension, to pick exact filter values."),
        StructuredTool.from_function(compute_trend, name="compute_trend", args_schema=TrendArgs,
                                     description="A series over time: its values, change, compound annual growth, "
                                                 "peak and latest year-on-year change, recorded as facts."),
        StructuredTool.from_function(compare_groups, name="compare_groups", args_schema=CompareArgs,
                                     description="Compare groups (e.g. industries, towns) in one period, with "
                                                 "shares of the total where the measure can be added up."),
        StructuredTool.from_function(compute_correlation, name="compute_correlation", args_schema=CorrelateArgs,
                                     description="Pearson correlation of two series (levels and year-on-year "
                                                 "changes), across datasets if needed."),
        StructuredTool.from_function(list_records, name="list_records", args_schema=RecordsArgs,
                                     description="Only for tables without a time dimension (e.g. rail stations): "
                                                 "list and count the records matching filters."),
        StructuredTool.from_function(create_chart, name="create_chart", args_schema=ChartArgs,
                                     description="Draw a line chart (series over time) or bar chart (groups in one "
                                                 "period) from the data. Returns a chart ID to cite."),
    ]
