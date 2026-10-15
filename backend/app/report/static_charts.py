"""Charts as PNG images, for the Markdown and PDF downloads (which cannot run Plotly's JavaScript).

They are drawn with matplotlib from the same Plotly specs the HTML report uses, so the two always show the same
data. matplotlib's Figure API is used directly (not pyplot), because runs finish in parallel worker threads and
pyplot keeps global state.
"""
import io

from matplotlib.figure import Figure
from matplotlib.ticker import FuncFormatter

MAX_X_LABELS = 12  # longer category axes show every nth label


def _axis_title(layout: dict, axis: str) -> str:
    return ((layout.get(axis) or {}).get("title") or {}).get("text", "")


def _number(value: float, _position=None) -> str:
    return f"{value:,.0f}" if abs(value) >= 100 else f"{value:,.2g}" if abs(value) < 1 else f"{value:,.1f}"


def chart_png(spec: dict, title: str) -> bytes:
    """One chart (a Plotly spec with line or bar traces) as a PNG image."""
    figure = Figure(figsize=(7.5, 3.6), dpi=150)
    ax = figure.subplots()
    second_axis = None
    layout = spec.get("layout", {})
    categories: list[str] = []
    for trace in spec.get("data", []):
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
    step = max(1, len(categories) // MAX_X_LABELS)
    ax.set_xticks(range(0, len(categories), step), [categories[i] for i in range(0, len(categories), step)],
                  rotation=45, ha="right", fontsize=7)
    ax.tick_params(axis="y", labelsize=7)
    ax.grid(axis="y", alpha=0.3)
    ax.spines[["top", "right"]].set_visible(second_axis is not None)
    handles = [h for a in (ax, second_axis) if a is not None for h in a.get_legend_handles_labels()[0]]
    if len(handles) > 1:
        ax.legend(handles=handles, fontsize=7, frameon=False)
    figure.tight_layout()
    buffer = io.BytesIO()
    figure.savefig(buffer, format="png")
    return buffer.getvalue()
