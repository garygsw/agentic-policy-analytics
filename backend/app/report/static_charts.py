"""Charts as PNG images, for the Markdown and PDF downloads (which cannot run Plotly's JavaScript).

They are drawn with matplotlib from the same Plotly specs the HTML report uses, so the two always show the same
data. matplotlib's Figure API is used directly (not pyplot), because runs finish in parallel worker threads and
pyplot keeps global state.
"""
import io

import numpy as np
from matplotlib.colors import LinearSegmentedColormap, Normalize
from matplotlib.figure import Figure
from matplotlib.ticker import FuncFormatter

from app.analysis.charts import SEQUENTIAL

SEQUENTIAL_MAP = LinearSegmentedColormap.from_list("sequential", [(stop, colour) for stop, colour in SEQUENTIAL])

MAX_X_LABELS = 12  # longer category axes show every nth label


def _axis_title(layout: dict, axis: str) -> str:
    return ((layout.get(axis) or {}).get("title") or {}).get("text", "")


def _number(value: float, _position=None) -> str:
    return f"{value:,.0f}" if abs(value) >= 100 else f"{value:,.2g}" if abs(value) < 1 else f"{value:,.1f}"


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
    columns = [str(c) for c in trace["x"]]
    step = max(1, len(columns) // MAX_X_LABELS)
    ax.set_xticks(range(0, len(columns), step), columns[::step], rotation=45, ha="right", fontsize=7)
    ax.set_yticks(range(len(trace["y"])), trace["y"], fontsize=7)
    bar = figure.colorbar(image, ax=ax)
    bar.set_label(((trace.get("colorbar") or {}).get("title") or {}).get("text", ""), fontsize=8)
    bar.ax.tick_params(labelsize=7)


def chart_png(spec: dict, title: str) -> bytes:
    """One chart (a Plotly spec: line, bar, scatter, pie, box, dot or heatmap) as a PNG image."""
    figure = Figure(figsize=(7.5, 3.6), dpi=150)
    ax = figure.subplots()
    traces, layout = spec.get("data", []), spec.get("layout", {})
    kind = traces[0].get("type") if traces else None
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
    for trace in [t for t in spec.get("data", []) if t not in points]:
        x = [str(v) for v in trace.get("x", [])]
        y = [float("nan") if v is None else v for v in trace.get("y", [])]
        categories = categories or x
        color = (trace.get("line") or trace.get("marker") or {}).get("color")
        if trace.get("type") == "bar":
            ax.bar(x, y, color=color)
            continue
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
    if categories:  # periods or groups along the x-axis (a scatter chart's x-axis is a measure instead)
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
