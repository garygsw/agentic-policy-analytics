"""The report as downloads: Markdown with the charts embedded as images, and a PDF.

Both are built from a run's final state, the same material as the HTML report: the report in the user's
language, the computed facts, the sources, the charts and any warnings. The charts are drawn as images
(static_charts.py) because neither format can run Plotly. Citations in the PDF are links: [F3] jumps to the
fact, [S1] to the source, and a source's address opens it.
"""
import base64
import datetime as dt
import io
from pathlib import Path

import matplotlib
from fpdf import FPDF

from app.graph.nodes.reporting import (
    charts_for_display,
    charts_of,
    facts_of,
    report_warnings,
    shown_sources,
    sources_of,
)
from app.report.html import LABELS, SGT
from app.report.static_charts import chart_png

FONTS = Path(matplotlib.get_data_path()) / "fonts" / "ttf"  # DejaVu, shipped with matplotlib
PDF_LANGUAGES = {"en", "ms"}  # DejaVu covers Latin script; Chinese and Tamil reports get an English PDF


def _charts(values: dict, report) -> list[tuple[dict, dict]]:
    """(chart as displayed, chart with its English title) for the charts the report shows, in its order."""
    shown, english = charts_for_display(values), {c["id"]: c for c in charts_of(values)}
    order = report.charts or [c["id"] for c in shown]
    by_id = {c["id"]: c for c in shown}
    return [(by_id[cid], english[cid]) for cid in order if cid in by_id]


def _value(value) -> str:
    """A fact's value as people read it: 18502.0 -> '18,502', 602.6 -> '602.6'."""
    if isinstance(value, bool) or not isinstance(value, int | float):
        return str(value)
    return f"{value:,.0f}" if float(value).is_integer() else f"{value:,}"


def _cites(ids: list[str]) -> str:
    return f" [{', '.join(ids)}]" if ids else ""


# -- Markdown ----------------------------------------------------------------------------------------------------
def export_markdown(values: dict) -> str:
    report = values.get("report_display") or values["report"]
    labels = LABELS.get(values.get("language", "en"), LABELS["en"])
    warnings = report_warnings(values)
    review = values.get("review")

    def warned(where: str) -> list[str]:
        return [f"  > ⚠ {labels['unverified']}: {w}" for w in warnings.get(where, [])]

    lines = [f"# {report.title}", "", f"*{labels['question']}: {values.get('question', '')}*", "",
             report.summary, *warned("summary"), "", f"## {labels['findings']}", ""]
    for i, claim in enumerate(report.findings, 1):
        lines += [f"{i}. {claim.text}{_cites(claim.citations)}", *warned(f"finding {i}")]
    charts = _charts(values, report)
    if charts:
        lines += ["", f"## {labels['charts']}"]
        for shown, english in charts:
            image = base64.b64encode(chart_png(english["spec"], english["title"])).decode()
            table = english["table"]
            lines += ["", f"**[{shown['id']}] {shown['title']}**", "",
                      f"![{shown['title']}](data:image/png;base64,{image})", "",
                      f"<details><summary>{labels['data']}</summary>", "",
                      "| " + " | ".join(table["columns"]) + " |", "|" + "---|" * len(table["columns"]),
                      *["| " + " | ".join("" if v is None else str(v) for v in row) + " |" for row in table["rows"]],
                      "", "</details>"]
    if report.caveats or (review and review.verdict != "pass"):
        lines += ["", f"## {labels['caveats']}", ""]
        for i, caveat in enumerate(report.caveats, 1):
            lines += [f"- {caveat.text}{_cites(caveat.citations)}", *warned(f"caveat {i}")]
        if review and review.verdict != "pass":
            lines.append(f"- {labels['reviewer']}: {review.reason}")
    sources = shown_sources(sources_of(values), report)
    if sources:
        lines += ["", f"## {labels['sources']}", ""]
        lines += [f"- **[{s['id']}]** {s.get('title', '')} ({s.get('agency', '')}). {s.get('url', '')}".rstrip()
                  for s in sources]
    facts = facts_of(values)
    if facts:
        lines += ["", f"## {labels['facts']}", "", f"| ID | {labels['fact']} | {labels['value']} | {labels['unit']} "
                  f"| {labels['source']} |", "|---|---|---|---|---|"]
        lines += [f"| {f['id']} | {f.get('label', '')} | {_value(f.get('value', ''))} | {f.get('unit', '')} | "
                  f"{', '.join(f.get('sources', []))} |" for f in facts]
    lines += ["", "---", "", labels["footer"], "", labels["attribution"]]
    return "\n".join(lines) + "\n"


# -- PDF -------------------------------------------------------------------------------------------------------------
class ReportPDF(FPDF):
    def footer(self) -> None:
        self.set_y(-12)
        self.set_font("DejaVu", size=7)
        self.set_text_color(120, 120, 120)
        self.cell(0, 6, f"Policy data report · {self.page_no()}", align="C")


def pdf_text(values: dict) -> tuple:
    """The report and labels the PDF uses: the user's language when the bundled font can show it, else English."""
    language = values.get("language", "en")
    if language not in PDF_LANGUAGES:
        return values["report"], LABELS["en"], True
    return values.get("report_display") or values["report"], LABELS.get(language, LABELS["en"]), False


def export_pdf(values: dict) -> bytes:
    report, labels, english_only = pdf_text(values)
    warnings = report_warnings(values)
    review = values.get("review")
    facts, sources = facts_of(values), shown_sources(sources_of(values), report)

    pdf = ReportPDF(format="A4")
    pdf.set_margins(18, 16, 18)
    pdf.set_auto_page_break(True, margin=18)
    pdf.add_font("DejaVu", "", FONTS / "DejaVuSans.ttf")
    pdf.add_font("DejaVu", "B", FONTS / "DejaVuSans-Bold.ttf")
    pdf.add_page()
    # One internal link per fact and source: a citation jumps to where it is listed.
    targets = {f["id"]: pdf.add_link() for f in facts} | {s["id"]: pdf.add_link() for s in sources}

    def text(size: float, content: str, bold: bool = False, color=(30, 30, 30), height: float = 5.5) -> None:
        pdf.set_font("DejaVu", "B" if bold else "", size)
        pdf.set_text_color(*color)
        pdf.multi_cell(0, height, content, align="L", new_x="LMARGIN", new_y="NEXT")

    def statement(prefix: str, content: str, citations: list[str], where: str) -> None:
        pdf.set_font("DejaVu", "", 10)
        pdf.set_text_color(30, 30, 30)
        pdf.write(5.5, f"{prefix}{content} ")
        pdf.set_text_color(176, 80, 50)
        for cid in citations:
            if pdf.get_x() + pdf.get_string_width(f"[{cid}]") > pdf.w - pdf.r_margin:
                pdf.ln(5.5)  # a citation never splits across lines
            pdf.write(5.5, f"[{cid}]", link=targets.get(cid, ""))
            pdf.write(5.5, " ")
        pdf.ln(6)
        for w in warnings.get(where, []):
            text(9, f"⚠ {labels['unverified']}: {w}", color=(138, 90, 18), height=5)
        pdf.ln(1.5)

    def heading(title: str, keep_with: float = 20) -> None:
        if pdf.get_y() + keep_with > pdf.h - pdf.b_margin:
            pdf.add_page()  # a heading never ends a page alone
        pdf.ln(3)
        text(13, title, bold=True, height=7)
        pdf.ln(1)

    generated = dt.datetime.now(SGT).strftime("%d %b %Y, %H:%M SGT")
    text(8, f"{labels['eyebrow'].upper()} · {labels['generated']} {generated}", color=(176, 80, 50))
    text(17, report.title, bold=True, height=8)
    text(9, f"{labels['question']}: {values.get('question', '')}", color=(100, 100, 100))
    text(9, f"⚠ {labels['some_unverified']}" if warnings else f"✓ {labels['checked']}",
         color=(138, 90, 18) if warnings else (47, 107, 69))
    if english_only:
        text(8, "This PDF is in English. The on-screen report shows the translation.", color=(100, 100, 100))
    pdf.ln(2)
    statement("", report.summary, [], "summary")

    heading(labels["findings"])
    for i, claim in enumerate(report.findings, 1):
        statement(f"{i}. ", claim.text, claim.citations, f"finding {i}")

    charts = _charts(values, report)
    if charts:
        chart_height = pdf.epw * 3.6 / 7.5  # the images' aspect ratio
        heading(labels["charts"], keep_with=chart_height + 12)
        for _, english in charts:
            if pdf.get_y() + chart_height > pdf.h - pdf.b_margin:
                pdf.add_page()
            pdf.image(_png(english), w=pdf.epw)
            pdf.ln(2)

    if report.caveats or (review and review.verdict != "pass"):
        heading(labels["caveats"])
        for i, caveat in enumerate(report.caveats, 1):
            statement("• ", caveat.text, caveat.citations, f"caveat {i}")
        if review and review.verdict != "pass":
            statement("• ", f"{labels['reviewer']}: {review.reason}", [], "")

    if sources:
        heading(labels["sources"])
        for s in sources:
            pdf.set_link(targets[s["id"]], y=pdf.get_y(), page=pdf.page_no())
            pdf.set_font("DejaVu", "B", 9)
            pdf.set_text_color(30, 30, 30)
            pdf.write(5, f"[{s['id']}] ")
            pdf.set_font("DejaVu", "", 9)
            pdf.write(5, f"{s.get('title', '')} ({s.get('agency', '')}) ")
            if s.get("url", "").startswith("http"):
                pdf.set_text_color(176, 80, 50)
                pdf.write(5, s["url"], link=s["url"])  # opens the source
            pdf.ln(7)

    if facts:
        heading(labels["facts"])
        for f in facts:
            pdf.set_link(targets[f["id"]], y=pdf.get_y(), page=pdf.page_no())
            text(8, f"{f['id']}  {f.get('label', '')}: {_value(f.get('value', ''))} {f.get('unit', '')}"
                    f"  [{', '.join(f.get('sources', []))}]", height=4.5)

    pdf.ln(4)
    text(7, f"{labels['footer']} {labels['attribution']}", color=(120, 120, 120), height=4)
    return bytes(pdf.output())


def _png(chart: dict) -> io.BytesIO:
    return io.BytesIO(chart_png(chart["spec"], chart["title"]))
