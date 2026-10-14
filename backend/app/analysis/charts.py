"""Plotly chart specs, built from computed series (never from numbers an LLM wrote).

A spec is plain JSON ({"data": [...], "layout": {...}}) that plotly.js renders in the browser and in the HTML
report. Each chart also keeps its data as a table, shown under the chart for accessibility and checking.
"""
import pandas as pd

PALETTE = ["#c96442", "#3b6ea8", "#5b8a5a", "#9a6fb0", "#b58a2b", "#6b7280"]


def line_chart(series: list[tuple[str, pd.Series, str]], title: str) -> tuple[dict, dict]:
    """Lines over time. Two units get two y-axes; more than two units cannot share a chart."""
    units = list(dict.fromkeys(unit for _, _, unit in series))
    if len(units) > 2:
        raise ValueError(f"a chart can show at most two units; got {units}")
    traces = []
    for i, (label, values, unit) in enumerate(series):
        trace = {"type": "scatter", "mode": "lines+markers", "name": label, "x": list(values.index),
                 "y": [round(float(v), 4) for v in values], "line": {"color": PALETTE[i % len(PALETTE)]}}
        if unit != units[0]:
            trace["yaxis"] = "y2"
        traces.append(trace)
    layout = {"title": {"text": title}, "xaxis": {"type": "category"}, "yaxis": {"title": {"text": units[0]}},
              "legend": {"orientation": "h"}, "margin": {"t": 60, "r": 60}}
    if len(units) == 2:
        layout["yaxis2"] = {"title": {"text": units[1]}, "overlaying": "y", "side": "right"}
    periods = sorted({p for _, values, _ in series for p in values.index})
    table = {"columns": ["period", *[f"{label} ({unit})" for label, _, unit in series]],
             "rows": [[p, *[_cell(values, p) for _, values, _ in series]] for p in periods]}
    return {"data": traces, "layout": layout}, table


def bar_chart(values: pd.Series, title: str, unit: str) -> tuple[dict, dict]:
    spec = {"data": [{"type": "bar", "x": list(values.index), "y": [round(float(v), 4) for v in values],
                      "marker": {"color": PALETTE[0]}}],
            "layout": {"title": {"text": title}, "yaxis": {"title": {"text": unit}}, "margin": {"t": 60}}}
    table = {"columns": ["group", unit], "rows": [[k, round(float(v), 4)] for k, v in values.items()]}
    return spec, table


def _cell(values: pd.Series, period: str):
    return round(float(values[period]), 4) if period in values.index else None
