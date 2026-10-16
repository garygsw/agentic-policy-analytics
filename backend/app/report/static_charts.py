"""Charts as PNG images, for the Markdown and PDF downloads (which cannot run Plotly's JavaScript).

They are drawn with matplotlib from the same Plotly specs the HTML report uses, so the two always show the same
data. matplotlib's Figure API is used directly (not pyplot), because runs finish in parallel worker threads and
pyplot keeps global state.
"""
import io
from datetime import date

import numpy as np
from matplotlib.cm import ScalarMappable
from matplotlib.collections import PolyCollection
from matplotlib.colors import LinearSegmentedColormap, Normalize
from matplotlib.dates import AutoDateLocator, ConciseDateFormatter
from matplotlib.figure import Figure
from matplotlib.ticker import FuncFormatter

from app.analysis.charts import SEQUENTIAL

SEQUENTIAL_MAP = LinearSegmentedColormap.from_list("sequential", [(stop, colour) for stop, colour in SEQUENTIAL])

MAX_X_LABELS = 12  # longer category axes show every nth label


def _axis_title(layout: dict, axis: str) -> str:
    return ((layout.get(axis) or {}).get("title") or {}).get("text", "")


def _number(value: float, _position=None) -> str:
    if abs(value) >= 100 or value == int(value):
        return f"{value:,.0f}"
    return f"{value:,.2g}" if abs(value) < 1 else f"{value:,.1f}"


def _points_png(ax, trace: dict, layout: dict) -> None:
    """A scatter chart: one point per period, labelled with the period when Plotly labels it too."""
    ax.scatter(trace["x"], trace["y"], s=18, color=(trace.get("marker") or {}).get("color"))
    if "text" in trace.get("mode", ""):
        for x, y, label in zip(trace["x"], trace["y"], trace.get("text", []), strict=False):
            ax.annotate(str(label), (x, y), textcoords="offset points", xytext=(0, 5), ha="center", fontsize=6)
    ax.set_xlabel(_axis_title(layout, "xaxis"), fontsize=8)
    ax.xaxis.set_major_formatter(FuncFormatter(_number))
    ax.tick_params(axis="x", labelsize=7)


def _pie_png(ax, trace: dict) -> None:
    colours = (trace.get("marker") or {}).get("colors")
    ax.pie(trace["values"], labels=trace["labels"], colors=colours[:len(trace["values"])] if colours else None,
           autopct="%1.0f%%", startangle=90, counterclock=False, textprops={"fontsize": 7})
    ax.set_aspect("equal")


def _boxes_png(ax, traces: list[dict], layout: dict) -> None:
    """Box plots drawn from their quartiles, or columns of dots when the trace holds every value."""
    names = [t["name"] for t in traces]
    if "q1" in traces[0]:
        ax.bxp([{"label": t["name"], "q1": t["q1"][0], "med": t["median"][0], "q3": t["q3"][0],
                 "whislo": t["lowerfence"][0], "whishi": t["upperfence"][0]} for t in traces],
               showfliers=False, patch_artist=True, boxprops={"facecolor": "#f6e7e0", "edgecolor": "#c96442"},
               medianprops={"color": "#c96442"})
    else:
        rng = np.random.default_rng(0)  # the same spread every time, so the image is reproducible
        for i, t in enumerate(traces, start=1):
            ax.scatter(i + rng.uniform(-0.18, 0.18, len(t["y"])), t["y"], s=9, alpha=0.75,
                       color=(t.get("marker") or {}).get("color"))
        ax.set_xticks(range(1, len(names) + 1), names)
    ax.tick_params(axis="x", labelsize=7, rotation=45)
    ax.set_ylabel(_axis_title(layout, "yaxis"), fontsize=8)
    ax.yaxis.set_major_formatter(FuncFormatter(_number))
    ax.tick_params(axis="y", labelsize=7)
    ax.grid(axis="y", alpha=0.3)


def _heatmap_png(figure, ax, trace: dict) -> None:
    z = np.array([[np.nan if v is None else v for v in row] for row in trace["z"]], dtype=float)
    image = ax.imshow(z, aspect="auto", cmap=SEQUENTIAL_MAP, norm=Normalize(np.nanmin(z), np.nanmax(z)),
                      interpolation="nearest")
    columns = [str(c) for c in trace.get("text", [trace["x"]])[0]]  # periods are kept as text on a time axis
    if "text" in trace and len(columns) > MAX_X_LABELS:  # many periods: each year labelled once, not rotated
        years = [str(date)[:4] for date in trace["x"]]
        starts = [i for i, year in enumerate(years) if i == 0 or year != years[i - 1]]
        starts = starts[::max(1, len(starts) // MAX_X_LABELS)]
        ax.set_xticks(starts, [years[i] for i in starts], fontsize=7)
    else:
        step = max(1, len(columns) // MAX_X_LABELS)
        ax.set_xticks(range(0, len(columns), step), columns[::step], rotation=45, ha="right", fontsize=7)
    ax.set_yticks(range(len(trace["y"])), trace["y"], fontsize=7)
    bar = figure.colorbar(image, ax=ax)
    bar.set_label(((trace.get("colorbar") or {}).get("title") or {}).get("text", ""), fontsize=8)
    bar.ax.tick_params(labelsize=7)


def _rings(trace: dict) -> list:
    """A filled outline trace's rings: its points split at each None."""
    rings, ring = [], []
    for x, y in zip(trace["x"], trace["y"], strict=True):
        if x is None:
            rings, ring = rings + [ring], []
        else:
            ring.append((x, y))
    return rings + ([ring] if ring else [])


def _map_png(figure, ax, traces: list[dict]) -> None:
    """A map: filled areas (the grey outline first), labelled points, and the colour bar's range."""
    for trace in traces:
        marker = trace.get("marker") or {}
        if trace.get("fill") == "toself":
            ax.add_collection(PolyCollection(_rings(trace), facecolors=trace["fillcolor"], edgecolors="white",
                                             linewidths=0.4))
        elif marker.get("showscale"):
            scale = ScalarMappable(Normalize(marker["cmin"], marker["cmax"]), SEQUENTIAL_MAP)
            bar = figure.colorbar(scale, ax=ax, shrink=0.8, format=FuncFormatter(_number))
            bar.set_label(((marker.get("colorbar") or {}).get("title") or {}).get("text", ""), fontsize=8)
            bar.ax.tick_params(labelsize=7)
        elif "markers" in trace.get("mode", ""):  # points, labelled when the chart labels them
            star = marker.get("symbol") == "star"
            ax.scatter(trace["x"], trace["y"], s=[size ** 2 / 2 for size in marker["size"]], c=marker["color"],
                       marker="*" if star else "o", edgecolors="white" if star else "#57534e", linewidths=0.8,
                       zorder=4 if star else 3, alpha=1 if star else 0.85)
            if "text" in trace.get("mode", ""):
                for x, y, label, size in zip(trace["x"], trace["y"], trace["text"], marker["size"], strict=True):
                    offset = (size / 2 + 3, 0) if star else (0, size / 2.6 + 3)  # beside a star, above a dot
                    ax.annotate(label, (x, y), textcoords="offset points", xytext=offset, va="center" if star else
                                "bottom", ha="left" if star else "center", fontsize=7)
    ax.autoscale_view()
    ax.set_aspect("equal")
    ax.axis("off")


def _bars_png(ax, bars: list[dict], stacked: bool) -> list[str]:
    """Bars from one or more traces: side by side, or stacked. Returns the categories along the x-axis."""
    categories = [str(v) for v in bars[0]["x"]]
    width = 0.8 if stacked or len(bars) == 1 else 0.8 / len(bars)
    bottom = np.zeros(len(categories))
    for i, trace in enumerate(bars):
        y = np.array([np.nan if v is None else v for v in trace["y"]], dtype=float)
        offset = 0 if stacked or len(bars) == 1 else (i - (len(bars) - 1) / 2) * width
        ax.bar(np.arange(len(categories)) + offset, y, width=width, bottom=bottom if stacked else None,
               color=(trace.get("marker") or {}).get("color"), label=trace.get("name"))
        if stacked:
            bottom = bottom + np.nan_to_num(y)
    return categories


def chart_png(spec: dict, title: str) -> bytes:
    """One chart (a Plotly spec: line, bar, scatter, pie, box, dot, heatmap or map) as a PNG image."""
    figure = Figure(figsize=(7.5, 3.6), dpi=150)
    ax = figure.subplots()
    traces, layout = spec.get("data", []), spec.get("layout", {})
    kind = traces[0].get("type") if traces else None
    if (layout.get("yaxis") or {}).get("scaleanchor") == "x":  # a map: longitude and latitude to the same scale
        figure.set_size_inches(7.5, 4.6)
        _map_png(figure, ax, traces)
        ax.set_title(title, loc="left", fontsize=10)
        return _png(figure)
    if kind in ("pie", "box", "heatmap"):
        if kind == "pie":
            _pie_png(ax, traces[0])
        elif kind == "box":
            _boxes_png(ax, traces, layout)
        else:
            _heatmap_png(figure, ax, traces[0])
        ax.set_title(title, loc="left", fontsize=10)
        return _png(figure)
    second_axis = None
    layout = spec.get("layout", {})
    categories: list[str] = []
    points = [t for t in spec.get("data", []) if t.get("type") == "scatter" and "lines" not in t.get("mode", "lines")]
    for trace in points:
        _points_png(ax, trace, layout)
    bars = [t for t in traces if t.get("type") == "bar"]
    if bars:
        categories = _bars_png(ax, bars, stacked=layout.get("barmode") == "stack")
    on_dates = (layout.get("xaxis") or {}).get("type") == "date"
    for trace in [t for t in traces if t not in points and t not in bars]:
        x = [date.fromisoformat(v) for v in trace["x"]] if on_dates else [str(v) for v in trace.get("x", [])]
        y = [float("nan") if v is None else v for v in trace.get("y", [])]
        categories = categories or ([] if on_dates else x)
        color = (trace.get("line") or trace.get("marker") or {}).get("color")
        target = ax
        if trace.get("yaxis") == "y2":
            second_axis = second_axis or ax.twinx()
            target = second_axis
        target.plot(x, y, marker="o", markersize=2.5, linewidth=1.6, color=color, label=trace.get("name"))

    ax.set_title(title, loc="left", fontsize=10)
    ax.set_ylabel(_axis_title(layout, "yaxis"), fontsize=8)
    ax.yaxis.set_major_formatter(FuncFormatter(_number))
    if second_axis is not None:
        second_axis.set_ylabel(_axis_title(layout, "yaxis2"), fontsize=8)
        second_axis.yaxis.set_major_formatter(FuncFormatter(_number))
        second_axis.tick_params(axis="y", labelsize=7)
    if on_dates:  # a time axis: a few short labels (years, or months within a year), never rotated
        locator = AutoDateLocator(maxticks=MAX_X_LABELS)
        ax.xaxis.set_major_locator(locator)
        ax.xaxis.set_major_formatter(ConciseDateFormatter(locator))
        ax.tick_params(axis="x", labelsize=7)
    elif categories:  # groups along the x-axis (a scatter chart's x-axis is a measure instead)
        step = max(1, len(categories) // MAX_X_LABELS)
        ax.set_xticks(range(0, len(categories), step), [categories[i] for i in range(0, len(categories), step)],
                      rotation=45, ha="right", fontsize=7)
    ax.tick_params(axis="y", labelsize=7)
    ax.grid(axis="both" if points else "y", alpha=0.3)
    ax.spines[["top", "right"]].set_visible(second_axis is not None)
    handles = [h for a in (ax, second_axis) if a is not None for h in a.get_legend_handles_labels()[0]]
    if len(handles) > 1:
        ax.legend(handles=handles, fontsize=7, frameon=False)
    return _png(figure)


def _png(figure) -> bytes:
    figure.tight_layout()
    buffer = io.BytesIO()
    figure.savefig(buffer, format="png")
    return buffer.getvalue()
