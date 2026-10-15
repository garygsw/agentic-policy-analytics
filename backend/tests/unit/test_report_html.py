"""The HTML report: escaping, citations, charts, fallback page and languages."""
import json
import re

import pandas as pd

from app.analysis.charts import line_chart
from app.graph.state import Claim, Report
from app.report.html import PLOTLY_SRI, render_html

FACTS = [{"id": "F1", "label": "ICT employment, 2024", "value": 139200, "unit": "persons", "sources": ["S1"],
          "method": "added up across sex, age"},
         {"id": "F2", "label": "change 2020 to 2024", "value": 24.7, "unit": "%", "sources": ["S1"], "method": ""}]
SOURCES = [{"id": "S1", "title": "Employed Residents by Industry", "agency": "Ministry of Manpower",
            "url": "https://data.gov.sg/datasets/d_x/view", "licence": "Singapore Open Data Licence v1.0",
            "origin": "snapshot", "retrieved_at": "2026-10-14", "quality": "5,760 clean rows",
            "warning": "Live source unavailable (HTTP 503); using snapshot data retrieved 2026-10-14"}]
ICT_SERIES = pd.Series([111600.0, 139200.0], index=["2020", "2024"])
SPEC, TABLE = line_chart([("ICT", ICT_SERIES, "persons")], "ICT <b>jobs</b>")
CHARTS = [{"id": "C1", "title": "ICT jobs", "spec": SPEC, "table": TABLE, "sources": ["S1"]}]


def report(**overrides):
    base = dict(title="Tech employment grew", summary="Up 24.7% to 139,200.",
                findings=[Claim(text="Employment reached 139,200.", citations=["F1", "S1"])], charts=["C1"],
                caveats=[Claim(text="Residents only.", citations=["S1"])])
    return Report(**{**base, **overrides})


def page(**kwargs):
    args = dict(question="Analyse tech employment", language="en", report=report(), facts=FACTS, sources=SOURCES,
                charts=CHARTS)
    return render_html(**{**args, **kwargs})


def test_page_has_findings_citations_facts_sources_and_attribution():
    html = page()
    assert "Tech employment grew" in html and "✓ Every number checked" in html
    assert '<a class="cite" href="#F1">F1</a>' in html and '<tr id="F1">' in html and '<li id="S1">' in html
    assert "139,200" in html and "24.7" in html  # numbers formatted in the facts table
    assert "Live source unavailable (HTTP 503)" in html  # fallback warnings are visible
    assert "Singapore Open Data Licence" in html and f'integrity="{PLOTLY_SRI}"' in html


def test_model_written_text_is_escaped():
    html = page(report=report(title="<script>alert(1)</script>",
                              findings=[Claim(text='<img src=x onerror="alert(1)">', citations=["F1"])]))
    assert "<script>alert(1)</script>" not in html and "&lt;script&gt;" in html
    assert '<img src=x' not in html
    assert "<b>jobs</b>" not in SPEC["layout"]["title"]["text"]  # chart titles are kept plain for Plotly


def test_chart_specs_cannot_close_the_script_tag():
    nasty = json.loads(json.dumps(CHARTS))
    nasty[0]["spec"]["data"][0]["name"] = "</script><script>alert(1)</script>"
    html = page(charts=nasty)
    block = re.search(r'<script type="application/json" id="chart-specs">(.*?)</script>', html, re.S).group(1)
    assert "</script>" not in block and json.loads(block)["C1"]["data"][0]["name"].startswith("</script>")


def test_every_chart_keeps_its_data_table():
    html = page()
    assert "<summary>Show data</summary>" in html and "111,600" in html


def test_fallback_page_shows_facts_without_prose():
    html = page(report=None)
    assert "Automated summary: figures only" in html and '<tr id="F2">' in html
    assert "Key findings" not in html


def test_labels_follow_the_language():
    html = page(language="ta")
    assert '<html lang="ta">' in html and "முக்கியக் கண்டறிதல்கள்" in html


# -- downloads -----------------------------------------------------------------------------------------------------
def final_state(language="en", **extra):
    """A run's final state, as the exports receive it."""
    return {"question": "Analyse tech employment", "language": language, "report": report(),
            "findings": {"analytics": {"facts": FACTS, "sources": SOURCES, "charts": CHARTS}}, **extra}


def test_markdown_download_embeds_the_charts_and_their_data():
    from app.report.export import export_markdown
    md = export_markdown(final_state())
    assert md.startswith("# Tech employment grew") and "1. Employment reached 139,200. [F1, S1]" in md
    assert "![ICT jobs](data:image/png;base64," in md  # the chart, as an image
    assert "| period | ICT (persons) |" in md and "| 2024 | 139200.0 |" in md  # and its data
    assert "| F1 | ICT employment, 2024 | 139,200 | persons | S1 |" in md  # every cited fact is listed
    assert "- **[S1]** Employed Residents by Industry (Ministry of Manpower). https://data.gov.sg" in md


def test_markdown_download_keeps_flagged_statements_with_their_warning():
    from app.report.export import export_markdown
    flags = [{"where": "finding 1", "problem": "139,200 does not match", "message": "x"}]
    md = export_markdown(final_state(grounding_flags=flags))
    assert "1. Employment reached 139,200. [F1, S1]\n  > ⚠ Unverified: 139,200 does not match" in md


def test_pdf_download_links_citations_to_sources_and_sources_to_the_web():
    from app.report.export import export_pdf
    pdf = export_pdf(final_state())
    assert pdf.startswith(b"%PDF") and b"https://data.gov.sg/datasets/d_x/view" in pdf  # the source opens
    assert pdf.count(b"/Dest") >= 3  # [F1], [S1] in the finding and [S1] in the caveat jump inside the PDF
    # A Chinese report gets an English PDF (the bundled font has Latin script only); Malay keeps its language.
    from app.report.export import pdf_text
    translated = report(title="科技就业增长")
    assert pdf_text(final_state("zh", report_display=translated))[0].title == "Tech employment grew"
    malay = report(title="Pekerjaan teknologi")
    assert pdf_text(final_state("ms", report_display=malay))[0].title == "Pekerjaan teknologi"
    assert export_pdf(final_state("zh", report_display=translated)).startswith(b"%PDF")
