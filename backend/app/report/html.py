"""Renders a checked report as one self-contained HTML page.

Everything model-written is escaped by the template engine, so a report cannot inject markup. Chart specs are
embedded as JSON and drawn by plotly.js (pinned version, integrity-checked); each chart's data table is always
in the page, so the numbers stay readable when the CDN is unreachable.
"""
import datetime as dt
import json
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

from app.graph.state import Report

PLOTLY_URL = "https://cdnjs.cloudflare.com/ajax/libs/plotly.js/4.1.2/plotly.min.js"
PLOTLY_SRI = "sha512-Bl2PHPN4LtlyZe5999ZzrfZOVxEqJjir/Sa8trGcjybS8Mn1EfjOaF6j4V/Cyq5VZUacnI3va+/SCth4UPwHNg=="
SGT = dt.timezone(dt.timedelta(hours=8))

# Fixed page text in each supported language. Kept in a data file so translators can review it.
LABELS = json.loads((Path(__file__).parent / "labels.json").read_text(encoding="utf-8"))

_env = Environment(loader=FileSystemLoader(Path(__file__).parent / "templates"),
                   autoescape=select_autoescape(["html", "j2"]), trim_blocks=True, lstrip_blocks=True)


def _number(value) -> str:
    """A table cell: numbers with thousands separators (at most two decimals); text (a heatmap's column name, for
    example) as it is."""
    if isinstance(value, bool) or not isinstance(value, int | float):
        return str(value)
    return f"{value:,}" if isinstance(value, int) else f"{value:,.2f}".rstrip("0").rstrip(".")


_env.filters["number"] = _number


def citation_groups(ids: list[str]) -> list[tuple[str, str]]:
    """Collapse runs of consecutive IDs for display: F1, F2, F3, S1 -> ("F1", "F1–F3"), ("S1", "S1").
    Each tuple is (anchor to link to, label to show)."""
    groups: list[list[str]] = []
    for cid in ids:
        last = groups[-1][-1] if groups else ""
        follows = (last[:1] == cid[:1] and last[1:].isdigit() and cid[1:].isdigit()
                   and int(cid[1:]) == int(last[1:]) + 1)
        if follows:
            groups[-1].append(cid)
        else:
            groups.append([cid])
    return [(g[0], g[0] if len(g) == 1 else f"{g[0]}–{g[-1]}") for g in groups]


_env.filters["citation_groups"] = citation_groups


def render_html(*, question: str, language: str, report: Report | None, facts: list[dict], sources: list[dict],
                charts: list[dict], review_note: str | None = None, generated: dt.datetime | None = None,
                warnings: dict[str, list[str]] | None = None) -> str:
    """The report page. With report=None (the fallback path) it shows the computed facts without prose."""
    labels = LABELS.get(language, LABELS["en"])
    shown = [c for c in charts if report is None or c["id"] in report.charts] if charts else []
    if report is not None and not report.charts:
        shown = charts  # the writer listed none: show what was computed rather than nothing
    generated = (generated or dt.datetime.now(SGT)).astimezone(SGT)
    specs = {c["id"]: c["spec"] for c in shown}
    return _env.get_template("report.html.j2").render(
        lang=language, L=labels, question=question, report=report, facts=facts, sources=sources, charts=shown,
        chart_specs=specs, review_note=review_note, warnings=warnings or {},
        generated=generated.strftime("%d %b %Y, %H:%M SGT"),
        plotly_url=PLOTLY_URL, plotly_sri=PLOTLY_SRI)
