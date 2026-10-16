"""Plotly chart specs, built from computed series (never from numbers an LLM wrote): lines over time, bars for
groups in one period, scatter plots of two measures against each other, pies for shares of a whole, box and dot
plots for distributions, and heatmaps for a measure across two categories (or a category over time).

A spec is plain JSON ({"data": [...], "layout": {...}}) that plotly.js renders in the browser and in the HTML
report. Each chart also keeps its data as a table, shown under the chart for accessibility and checking.
"""
import pandas as pd

PALETTE = ["#c96442", "#3b6ea8", "#5b8a5a", "#9a6fb0", "#b58a2b", "#6b7280"]
# Light to dark, for heatmaps and maps. Spelled out, because Plotly's and matplotlib's "YlOrRd" run in opposite
# directions; the PNG downloads use the same list.
SEQUENTIAL = [[0.0, "#ffffcc"], [0.25, "#fed976"], [0.5, "#fd8d3c"], [0.75, "#e31a1c"], [1.0, "#800026"]]
MAX_SLICES = 8  # a pie with more slices is unreadable: the smallest are combined into "Other"


def plain(text: str) -> str:
    """Plotly renders some HTML tags in titles and labels; model-written text must stay plain."""
    return text.replace("<", "").replace(">", "")


def line_chart(series: list[tuple[str, pd.Series, str]], title: str) -> tuple[dict, dict]:
    """Lines over time. Two units get two y-axes; more than two units cannot share a chart."""
    units = list(dict.fromkeys(unit for _, _, unit in series))
    if len(units) > 2:
        raise ValueError(f"a chart can show at most two units; got {units}")
    traces = []
    for i, (label, values, unit) in enumerate(series):
        trace = {"type": "scatter", "mode": "lines+markers", "name": plain(label), "x": list(values.index),
                 "y": [round(float(v), 4) for v in values], "line": {"color": PALETTE[i % len(PALETTE)]}}
        if unit != units[0]:
            trace["yaxis"] = "y2"
        traces.append(trace)
    layout = {"title": {"text": plain(title)}, "xaxis": {"type": "category"}, "yaxis": {"title": {"text": units[0]}},
              "legend": {"orientation": "h"}, "margin": {"t": 60, "r": 60}}
    if len(units) == 2:
        layout["yaxis2"] = {"title": {"text": units[1]}, "overlaying": "y", "side": "right"}
    periods = sorted({p for _, values, _ in series for p in values.index})
    table = {"columns": ["period", *[f"{label} ({unit})" for label, _, unit in series]],
             "rows": [[p, *[_cell(values, p) for _, values, _ in series]] for p in periods]}
    return {"data": traces, "layout": layout}, table


def scatter_chart(x: tuple[str, pd.Series, str], y: tuple[str, pd.Series, str], title: str,
                  max_point_labels: int = 12) -> tuple[dict, dict]:
    """One point per period, for two measures: how they moved together. These are the points a correlation is
    computed from. Points are labelled with their period when there are few enough to read."""
    (x_label, x_values, x_unit), (y_label, y_values, y_unit) = x, y
    both = pd.concat([x_values, y_values], axis=1, join="inner").dropna()
    periods = [str(p) for p in both.index]
    trace = {"type": "scatter", "mode": "markers+text" if len(periods) <= max_point_labels else "markers",
             "x": [round(float(v), 4) for v in both.iloc[:, 0]], "y": [round(float(v), 4) for v in both.iloc[:, 1]],
             "text": periods, "textposition": "top center", "marker": {"color": PALETTE[0], "size": 9}}
    layout = {"title": {"text": plain(title)}, "xaxis": {"title": {"text": plain(f"{x_label} ({x_unit})")}},
              "yaxis": {"title": {"text": plain(f"{y_label} ({y_unit})")}}, "margin": {"t": 60}}
    table = {"columns": ["period", f"{x_label} ({x_unit})", f"{y_label} ({y_unit})"],
             "rows": [[p, a, b] for p, a, b in zip(periods, trace["x"], trace["y"], strict=True)]}
    return {"data": [trace], "layout": layout}, table


def bar_chart(values: pd.Series, title: str, unit: str) -> tuple[dict, dict]:
    spec = {"data": [{"type": "bar", "x": list(values.index), "y": [round(float(v), 4) for v in values],
                      "marker": {"color": PALETTE[0]}}],
            "layout": {"title": {"text": plain(title)}, "yaxis": {"title": {"text": unit}}, "margin": {"t": 60}}}
    table = {"columns": ["group", unit], "rows": [[k, round(float(v), 4)] for k, v in values.items()]}
    return spec, table


def _cell(values: pd.Series, period: str):
    return round(float(values[period]), 4) if period in values.index else None


def pie_chart(values: pd.Series, title: str, unit: str) -> tuple[dict, dict]:
    """Shares of a whole, largest first. Beyond MAX_SLICES, the smallest groups are combined into "Other"."""
    values = values.sort_values(ascending=False)
    if len(values) > MAX_SLICES:
        values = pd.concat([values.iloc[:MAX_SLICES - 1], pd.Series({"Other": values.iloc[MAX_SLICES - 1:].sum()})])
    total = float(values.sum())
    spec = {"data": [{"type": "pie", "labels": [plain(str(k)) for k in values.index],
                      "values": [round(float(v), 4) for v in values], "sort": False, "direction": "clockwise",
                      "textinfo": "label+percent", "automargin": True,
                      "marker": {"colors": PALETTE + ["#a8a29e", "#d6d3d1"]}}],
            "layout": {"title": {"text": plain(title)}, "showlegend": False, "margin": {"t": 60}}}
    table = {"columns": ["group", unit, "share (%)"],
             "rows": [[k, round(float(v), 4), round(float(v) / total * 100, 1)] for k, v in values.items()]}
    return spec, table


def box_chart(stats: dict[str, dict], title: str, unit: str) -> tuple[dict, dict]:
    """One box per group, from quartiles computed in pandas (the box), whiskers to the furthest values within
    1.5 times the interquartile range, and the minimum and maximum in the data table."""
    traces = [{"type": "box", "name": plain(group), "x": [plain(group)], "q1": [round(d["q1"], 4)],
               "median": [round(d["median"], 4)], "q3": [round(d["q3"], 4)],
               "lowerfence": [round(d["lower_whisker"], 4)], "upperfence": [round(d["upper_whisker"], 4)],
               "marker": {"color": PALETTE[0]}, "showlegend": False} for group, d in stats.items()]
    layout = {"title": {"text": plain(title)}, "yaxis": {"title": {"text": unit}},
              "xaxis": {"type": "category", "automargin": True}, "margin": {"t": 60}}
    table = {"columns": ["group", "values", "min", "lower quartile", "median", "upper quartile", "max"],
             "rows": [[g, d["n"], *(round(d[k], 4) for k in ("min", "q1", "median", "q3", "max"))]
                      for g, d in stats.items()]}
    return {"data": traces, "layout": layout}, table


def dot_chart(groups: dict[str, pd.Series], title: str, unit: str) -> tuple[dict, dict]:
    """Every value as a dot, one column per group (spread sideways so dots do not hide each other)."""
    traces = [{"type": "box", "name": plain(group), "y": [round(float(v), 4) for v in values], "boxpoints": "all",
               "jitter": 0.5, "pointpos": 0, "fillcolor": "rgba(0,0,0,0)", "line": {"color": "rgba(0,0,0,0)"},
               "marker": {"color": PALETTE[i % len(PALETTE)], "size": 6, "opacity": 0.75}, "showlegend": False}
              for i, (group, values) in enumerate(groups.items())]
    layout = {"title": {"text": plain(title)}, "yaxis": {"title": {"text": unit}},
              "xaxis": {"type": "category", "automargin": True}, "margin": {"t": 60}}
    table = {"columns": ["group", "values", "min", "median", "max"],
             "rows": [[g, len(v), round(float(v.min()), 4), round(float(v.median()), 4), round(float(v.max()), 4)]
                      for g, v in groups.items()]}
    return {"data": traces, "layout": layout}, table


def heatmap_chart(matrix: pd.DataFrame, title: str, unit: str) -> tuple[dict, dict]:
    """A measure across two categories: rows are one dimension, columns another (or periods). Darker is higher;
    an empty cell has no value."""
    z = [[None if pd.isna(v) else round(float(v), 4) for v in row] for row in matrix.to_numpy()]
    spec = {"data": [{"type": "heatmap", "z": z, "x": [plain(str(c)) for c in matrix.columns],
                      "y": [plain(str(r)) for r in matrix.index], "colorscale": SEQUENTIAL, "hoverongaps": False,
                      "colorbar": {"title": {"text": unit}}}],
            "layout": {"title": {"text": plain(title)}, "xaxis": {"type": "category", "automargin": True},
                       "yaxis": {"type": "category", "autorange": "reversed", "automargin": True}}}
    rows = [[r, str(c), cell] for r, row in zip(matrix.index, z, strict=True)
            for c, cell in zip(matrix.columns, row, strict=True) if cell is not None]
    return spec, {"columns": ["row", "column", unit], "rows": rows}

