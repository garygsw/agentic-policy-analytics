"""Plotly chart specs, built from computed series (never from numbers an LLM wrote): lines over time; bars for
groups in one period, grouped or stacked when split by a second dimension; scatter plots of two measures; pies for
shares of a whole; box and dot plots for distributions; heatmaps for a measure across two categories (or a category
over time); and maps.

A spec is plain JSON ({"data": [...], "layout": {...}}) that plotly.js renders in the browser and in the HTML
report. Each chart also keeps its data as a table, shown under the chart for accessibility and checking.
"""
import re

import pandas as pd

PALETTE = ["#c96442", "#3b6ea8", "#5b8a5a", "#9a6fb0", "#b58a2b", "#6b7280"]
# Light to dark, for heatmaps and maps. Spelled out, because Plotly's and matplotlib's "YlOrRd" run in opposite
# directions; the PNG downloads use the same list.
SEQUENTIAL = [[0.0, "#ffffcc"], [0.25, "#fed976"], [0.5, "#fd8d3c"], [0.75, "#e31a1c"], [1.0, "#800026"]]
MAX_SLICES = 8  # a pie with more slices is unreadable: the smallest are combined into "Other"


def plain(text: str) -> str:
    """Plotly renders some HTML tags in titles and labels; model-written text must stay plain."""
    return text.replace("<", "").replace(">", "")


def period_date(period: str) -> str | None:
    """The middle of a period, as a date a time axis can place: "2024" is 1 July, "2024-Q1" 15 February, "2024-03"
    15 March and "FY2024" (April 2024 to March 2025) 1 October 2024. With every point in the middle of its period,
    series of different frequencies line up: a year's total sits among that year's months. None if not a period."""
    if match := re.fullmatch(r"(\d{4})", period):
        return f"{match[1]}-07-01"
    if match := re.fullmatch(r"(\d{4})-Q([1-4])", period):
        return f"{match[1]}-{3 * int(match[2]) - 1:02d}-15"
    if match := re.fullmatch(r"(\d{4})-(\d{2})", period):
        return f"{match[1]}-{match[2]}-15"
    if match := re.fullmatch(r"FY(\d{4})", period):
        return f"{match[1]}-10-01"
    return None


def line_chart(series: list[tuple[str, pd.Series, str]], title: str) -> tuple[dict, dict]:
    """Lines over time, on a time axis (short labels such as years, never rotated). Two units get two y-axes; more
    than two units cannot share a chart."""
    units = list(dict.fromkeys(unit for _, _, unit in series))
    if len(units) > 2:
        raise ValueError(f"a chart can show at most two units; got {units}")
    periods = sorted({p for _, values, _ in series for p in values.index})
    dates = {p: period_date(str(p)) for p in periods}
    on_time_axis = all(dates.values())
    traces = []
    for i, (label, values, unit) in enumerate(series):
        trace = {"type": "scatter", "mode": "lines+markers", "name": plain(label), "x": list(values.index),
                 "y": [round(float(v), 4) for v in values], "line": {"color": PALETTE[i % len(PALETTE)]}}
        if on_time_axis:  # placed by date; hovering still shows the period as the data has it
            trace |= {"x": [dates[p] for p in values.index], "text": [str(p) for p in values.index],
                      "hovertemplate": "%{text}: %{y:,}"}
        if unit != units[0]:
            trace["yaxis"] = "y2"
        traces.append(trace)
    layout = {"title": {"text": plain(title)},
              "xaxis": {"type": "date"} if on_time_axis else {"type": "category", "automargin": True},
              "yaxis": {"title": {"text": units[0]}}, "margin": {"t": 60, "r": 60},
              # above the plot, where the x-axis labels cannot run into it
              "legend": {"orientation": "h", "x": 0, "y": 1.02, "xanchor": "left", "yanchor": "bottom"}}
    if len(units) == 2:  # round ticks of its own (Plotly's default lines them up with the left axis, at odd values)
        layout["yaxis2"] = {"title": {"text": units[1]}, "overlaying": "y", "side": "right", "tickmode": "auto",
                            "showgrid": False}
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
    columns = [plain(str(c)) for c in matrix.columns]
    trace = {"type": "heatmap", "z": z, "x": columns, "y": [plain(str(r)) for r in matrix.index],
             "colorscale": SEQUENTIAL, "hoverongaps": False, "colorbar": {"title": {"text": unit}}}
    xaxis = {"type": "category", "automargin": True}
    dates = [period_date(c) for c in columns]
    if all(dates):  # columns are periods: a time axis, as for lines (short labels, never rotated)
        trace |= {"x": dates, "text": [columns] * len(z), "hovertemplate": "%{y}, %{text}: %{z:,}<extra></extra>"}
        xaxis = {"type": "date"}
    spec = {"data": [trace], "layout": {"title": {"text": plain(title)}, "xaxis": xaxis,
                                        "yaxis": {"type": "category", "autorange": "reversed", "automargin": True}}}
    rows = [[r, str(c), cell] for r, row in zip(matrix.index, z, strict=True)
            for c, cell in zip(matrix.columns, row, strict=True) if cell is not None]
    return spec, {"columns": ["row", "column", unit], "rows": rows}


def scale_colour(fraction: float) -> str:
    """The colour at a point (0 to 1) of the SEQUENTIAL scale, as #rrggbb."""
    fraction = min(1.0, max(0.0, fraction))
    for (low, start), (high, end) in zip(SEQUENTIAL, SEQUENTIAL[1:], strict=False):
        if fraction <= high:
            t = (fraction - low) / (high - low)
            a, b = (int(start[i:i + 2], 16) for i in (1, 3, 5)), (int(end[i:i + 2], 16) for i in (1, 3, 5))
            return "#" + "".join(f"{round(x + (y - x) * t):02x}" for x, y in zip(a, b, strict=True))
    return SEQUENTIAL[-1][1]


def _outline(rings: list) -> tuple[list, list]:
    """Rings as one line for Plotly: points of each ring, then None to start the next."""
    xs, ys = [], []
    for ring in rings:
        xs += [x for x, _ in ring] + [None]
        ys += [y for _, y in ring] + [None]
    return xs, ys


def map_chart(values: pd.Series, shapes: dict[str, list], points: dict[str, tuple], base: list, title: str,
              unit: str, landmark: tuple[str, tuple] | None = None, max_point_labels: int = 12) -> tuple[dict, dict]:
    """A map: areas filled from light (low) to dark (high), or points sized and coloured by value (labelled when
    there are few), over an outline (Singapore's planning areas, or the region's countries), with an optional
    landmark such as Singapore on a map of the region. Drawn as plain shapes on longitude and latitude, not with a
    map service, so it needs no network and draws the same in the PDF."""
    shown = values[[label in shapes or label in points for label in values.index]]
    low, high = float(shown.min()), float(shown.max())
    def position(v):
        return (float(v) - low) / (high - low) if high > low else 0.5
    base_x, base_y = _outline(base)
    traces = [{"type": "scatter", "x": base_x, "y": base_y, "mode": "lines", "fill": "toself",
               "fillcolor": "#e7e5e0", "line": {"color": "#ffffff", "width": 0.5}, "hoverinfo": "skip"}]
    for label, value in shown.sort_values().items():  # darkest last, so the highest values are drawn on top
        if label in shapes:
            xs, ys = _outline(shapes[label])
            traces.append({"type": "scatter", "x": xs, "y": ys, "mode": "lines", "fill": "toself",
                           "fillcolor": scale_colour(position(value)), "line": {"color": "#ffffff", "width": 0.6},
                           "name": plain(f"{label}: {value:,.4g} {unit}"), "hoveron": "fills", "hoverinfo": "name"})
    if points:
        labels = [label for label in shown.index if label in points]
        mode = "markers+text" if len(labels) <= max_point_labels else "markers"
        traces.append({"type": "scatter", "mode": mode, "x": [points[k][0] for k in labels],
                       "y": [points[k][1] for k in labels], "text": [plain(k) for k in labels],
                       "textposition": "top center",
                       "hovertext": [plain(f"{k}: {shown[k]:,.4g} {unit}") for k in labels],
                       "hoverinfo": "text", "marker": {"size": [14 + 26 * position(shown[k]) for k in labels],
                                                       "color": [scale_colour(position(shown[k])) for k in labels],
                                                       "line": {"color": "#57534e", "width": 1}}})
    if landmark:
        name, (x, y) = landmark
        traces.append({"type": "scatter", "mode": "markers+text", "x": [x], "y": [y], "text": [name],
                       "textposition": "middle right", "hoverinfo": "text",
                       "marker": {"symbol": "star", "size": [13], "color": ["#1d4ed8"],
                                  "line": {"color": "#ffffff", "width": 1}}})
    traces.append({"type": "scatter", "x": [base_x[0]], "y": [base_y[0]], "mode": "markers", "hoverinfo": "skip",
                   "marker": {"size": 0.1, "opacity": 0, "color": [low, high], "colorscale": SEQUENTIAL,
                              "cmin": low, "cmax": high, "showscale": True, "colorbar": {"title": {"text": unit}}}})
    layout = {"title": {"text": plain(title)}, "showlegend": False, "hovermode": "closest", "height": 460,
              "xaxis": {"visible": False}, "yaxis": {"visible": False, "scaleanchor": "x", "scaleratio": 1},
              "margin": {"t": 60}}
    table = {"columns": ["area", unit], "rows": [[k, round(float(v), 4)] for k, v in shown.items()]}
    return {"data": traces, "layout": layout}, table


def multi_bar_chart(matrix: pd.DataFrame, title: str, unit: str, stacked: bool) -> tuple[dict, dict]:
    """Bars split by a second dimension: rows are the bars, columns the parts. Grouped bars stand side by side,
    to compare the parts; stacked bars build up to each bar's total."""
    traces = [{"type": "bar", "name": plain(str(part)), "x": [plain(str(r)) for r in matrix.index],
               "y": [None if pd.isna(v) else round(float(v), 4) for v in matrix[part]],
               "marker": {"color": PALETTE[i % len(PALETTE)]}} for i, part in enumerate(matrix.columns)]
    layout = {"title": {"text": plain(title)}, "barmode": "stack" if stacked else "group",
              "yaxis": {"title": {"text": unit}}, "xaxis": {"type": "category", "automargin": True},
              "legend": {"orientation": "h"}, "margin": {"t": 60}}
    table = {"columns": ["group", *[str(c) for c in matrix.columns]],
             "rows": [[str(r), *[None if pd.isna(v) else round(float(v), 4) for v in row]]
                      for r, row in zip(matrix.index, matrix.to_numpy(), strict=True)]}
    return {"data": traces, "layout": layout}, table

