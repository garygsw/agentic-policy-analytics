"""Report writing and checking, the deterministic fallback, and the final answer in the user's language."""
import json
from collections.abc import Callable

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from app.analysis.charts import plain
from app.graph import prompts
from app.graph.events import STATUS, emit, guarded
from app.graph.state import AgentState, Report, ReportTexts, Review
from app.guardrails.language import LANGUAGE_NAMES
from app.quality.grounding import numbers_in, numbers_preserved, ungrounded
from app.report.html import render_html

MAX_REVISIONS = 2  # shared by grounding fixes and reviewer feedback
MAX_ANALYSIS_REWORKS = 1  # the reviewer may send the analysis back once; after that its objection is shown


def facts_of(state: AgentState) -> list[dict]:
    return ((state.get("findings") or {}).get("analytics") or {}).get("facts", [])


def sources_of(state: AgentState) -> list[dict]:
    """Analytics may load more datasets than extraction did, so its source list is the complete one."""
    findings = state.get("findings") or {}
    return (findings.get("analytics") or {}).get("sources") or (findings.get("extraction") or {}).get("sources", [])


def charts_of(state: AgentState) -> list[dict]:
    return ((state.get("findings") or {}).get("analytics") or {}).get("charts", [])


def _show(number: float) -> str:
    """How an unmatched number is quoted back to the writer: 3000000000.0 -> '3,000,000,000', 31.5 -> '31.5'."""
    return f"{int(number):,}" if float(number).is_integer() else f"{number:,.6g}"


def make_report_writer(llm) -> Callable:
    @guarded("report_writer")
    def report_writer(state: AgentState) -> dict:
        revising = state.get("report") is not None
        feedback = ""
        if state.get("grounding_issues"):
            feedback = "Fix these problems in your previous draft:\n- " + "\n- ".join(state["grounding_issues"])
        elif state.get("review") and state["review"].verdict == "poor_report":
            feedback = f"Reviewer feedback on your previous draft: {state['review'].reason}"
        charts = [{"id": c["id"], "title": c["title"]} for c in charts_of(state)]
        content = (f"Question: {state['question']}\n\nFacts:\n{json.dumps(facts_of(state), ensure_ascii=False)}\n\n"
                   f"Charts:\n{json.dumps(charts, ensure_ascii=False)}\n\n"
                   f"Sources:\n{json.dumps(sources_of(state), ensure_ascii=False)}\n\n{feedback}").strip()
        report = llm("quality", schema=Report).invoke([SystemMessage(prompts.REPORT), HumanMessage(content)])
        emit("report_writer", "action", f"{'Revised' if revising else 'Wrote'} report: {report.title}")
        return {"report": report, "revisions": state.get("revisions", 0) + int(revising)}
    return report_writer


def validate(state: AgentState) -> dict:
    """Deterministic checks: every number must match a computed fact (or the question), or appear in a document
    passage the same finding cites; and every finding must cite known facts or sources."""
    emit("validate", "status", STATUS["validate"])
    report: Report = state["report"]
    facts = facts_of(state)
    allowed = [f["value"] for f in facts if isinstance(f.get("value"), int | float)]
    allowed += numbers_in(" ".join(str(f.get("label", "")) for f in facts)) + numbers_in(state["question"])
    known_ids = {f["id"] for f in facts} | {s["id"] for s in sources_of(state)}
    passages = {s["id"]: s.get("text", "") for s in sources_of(state) if s.get("kind") in ("document", "web")}

    def quoted(citations: list[str]) -> list[float]:
        return numbers_in(" ".join(passages[c] for c in citations if c in passages))

    cited_anywhere = [c for claim in report.findings for c in claim.citations]
    issues = [f"summary: {_show(n)} does not match any computed fact or cited passage"
              for n in ungrounded(report.summary, allowed + quoted(cited_anywhere))]
    for i, claim in enumerate(report.findings, 1):
        issues += [f"finding {i}: {_show(n)} does not match any computed fact or a passage this finding cites"
                   for n in ungrounded(claim.text, allowed + quoted(claim.citations))]
        unknown = [c for c in claim.citations if c not in known_ids]
        if unknown:
            issues.append(f"finding {i}: cites unknown IDs {unknown}")
        if not claim.citations:
            issues.append(f"finding {i}: has no citation")
    unknown_charts = [c for c in report.charts if c not in {chart["id"] for chart in charts_of(state)}]
    if unknown_charts:
        issues.append(f"charts: unknown IDs {unknown_charts}")
    emit("validate", "observation", "Grounded: every number matches a computed fact" if not issues
         else f"{len(issues)} problem(s): " + "; ".join(issues), passed=not issues, issues=issues)
    return {"grounding_issues": issues}


def make_reviewer(llm) -> Callable:
    @guarded("reviewer")
    def reviewer(state: AgentState) -> dict:
        analytics = (state.get("findings") or {}).get("analytics") or {}
        content = (f"Question: {state['question']}\n\nAnalysis performed: {analytics.get('summary', '')}\n\n"
                   f"Report:\n{state['report'].model_dump_json()}")
        review = llm("quality", schema=Review).invoke([SystemMessage(prompts.REVIEW), HumanMessage(content)])
        emit("reviewer", "reasoning", f"{review.verdict}: {review.reason}")
        reworks = state.get("analysis_reworks", 0) + int(review.verdict == "wrong_analysis")
        return {"review": review, "analysis_reworks": reworks}
    return reviewer


def make_localize_report(llm) -> Callable:
    """Translate a checked report field by field (citations and chart IDs are kept), before rendering.
    If any translated text changes a number, the whole report stays in English."""
    def localize_report(state: AgentState) -> dict:
        report: Report = state["report"]
        charts = charts_of(state)
        english = {"report_display": report, "chart_titles": {c["id"]: c["title"] for c in charts}}
        if state["language"] == "en":
            return english
        emit("localize", "status", STATUS["localize"])
        source = ReportTexts(title=report.title, summary=report.summary, findings=[f.text for f in report.findings],
                             caveats=report.caveats, chart_titles=[c["title"] for c in charts])
        try:
            system = prompts.TRANSLATE_FIELDS.format(language=LANGUAGE_NAMES[state["language"]])
            texts = llm("fast", schema=ReportTexts).invoke([SystemMessage(system),
                                                            HumanMessage(source.model_dump_json())])
        except Exception as error:  # noqa: BLE001 - an English report beats no report
            emit("localize", "error", f"Translation failed ({type(error).__name__}); showing the report in English")
            return english
        pairs = ([(source.title, texts.title), (source.summary, texts.summary)]
                 + list(zip(source.findings, texts.findings, strict=False))
                 + list(zip(source.caveats, texts.caveats, strict=False)))
        same_shape = (len(texts.findings), len(texts.caveats), len(texts.chart_titles)) == (
            len(source.findings), len(source.caveats), len(source.chart_titles))
        if not same_shape or not all(numbers_preserved(a, b) for a, b in pairs):
            emit("localize", "error", "Translation changed a number or dropped a line; keeping the English report")
            return english
        emit("localize", "action", f"Translated the report into {LANGUAGE_NAMES[state['language']]}")
        findings = [f.model_copy(update={"text": t}) for f, t in zip(report.findings, texts.findings, strict=True)]
        translated = report.model_copy(update={"title": texts.title, "summary": texts.summary,
                                               "caveats": texts.caveats, "findings": findings})
        titles = {c["id"]: t for c, t in zip(charts, texts.chart_titles, strict=True)}
        return {"report_display": translated, "chart_titles": titles}
    return localize_report


def _charts_for_display(state: AgentState) -> list[dict]:
    titles = state.get("chart_titles") or {}
    shown = []
    for chart in charts_of(state):
        title = titles.get(chart["id"], chart["title"])
        spec = {**chart["spec"], "layout": {**chart["spec"]["layout"], "title": {"text": plain(title)}}}
        shown.append({**chart, "title": title, "spec": spec})
    return shown


def render(state: AgentState) -> dict:
    """The answer for the conversation (markdown) and the full report page (HTML), in the user's language."""
    report: Report = state.get("report_display") or state["report"]
    review: Review | None = state.get("review")
    review_note = review.reason if review and review.verdict != "pass" else None
    charts = _charts_for_display(state)
    sources = shown_sources(sources_of(state), report)
    lines = [f"# {report.title}", "", report.summary, "", "## Findings"]
    lines += [f"- {c.text} [{', '.join(c.citations)}]" for c in report.findings]
    caveats = list(report.caveats) + ([f"Reviewer note: {review_note}"] if review_note else [])
    if caveats:
        lines += ["", "## Caveats", *[f"- {c}" for c in caveats]]
    by_id = {c["id"]: c for c in charts}
    if report.charts:
        lines += ["", "## Charts", *[f"- [{cid}] {by_id[cid]['title']}" for cid in report.charts if cid in by_id]]
    if sources:
        lines += ["", "## Sources", *[f"- [{s['id']}] {s.get('title', '')} ({s.get('agency', '')}) {s.get('url', '')}"
                                      .rstrip() for s in sources]]
    html = render_html(question=state["question"], language=state["language"], report=report,
                       facts=facts_of(state), sources=sources, charts=charts, review_note=review_note)
    # "partial": the report is grounded, but the reviewer's last check found it does not fully answer the question.
    return {"answer": "\n".join(lines), "report_html": html, "outcome": "partial" if review_note else "answered"}


def shown_sources(sources: list[dict], report: Report | None) -> list[dict]:
    """Datasets are always listed (facts and charts cite them); document and web passages only when a finding
    cites them, since the agents read more passages than the report uses."""
    cited = {c for finding in (report.findings if report else []) for c in finding.citations}
    return [s for s in sources if s.get("kind", "dataset") == "dataset" or s["id"] in cited]


def fallback_report(state: AgentState) -> dict:
    """Last resort when the language models fail or the report cannot be grounded: state the computed
    figures as they are, with no generated prose."""
    facts = facts_of(state)
    if facts:
        lines = ["# Automated summary",
                 "The report could not be written by the language model, so these are the computed figures "
                 "without commentary.", ""]
        lines += [f"- {f.get('label')}: {f.get('value')} {f.get('unit', '')} [{f['id']}]".rstrip() for f in facts]
    else:
        lines = ["The analysis could not be completed. Please try again shortly."]
    if state.get("errors"):
        lines += ["", "Details: " + "; ".join(state["errors"])]
    emit("fallback_report", "action", "Using the deterministic template report")
    sources = shown_sources(sources_of(state), None)
    html = render_html(question=state.get("question", ""), language=state.get("language", "en"), report=None,
                       facts=facts, sources=sources, charts=charts_of(state)) if facts else ""
    return {"answer": "\n".join(lines), "report_html": html, "outcome": "fallback"}


def cancel(state: AgentState) -> dict:
    return {"answer": "The analysis was cancelled at your request.", "outcome": "cancelled"}


def make_localize(llm) -> Callable:
    def localize(state: AgentState) -> dict:
        """Translate a plain-text answer (decline, fallback, cancel) unless a number changed on the way.
        Reports are translated field by field in localize_report instead."""
        if state["language"] == "en":
            return {}
        emit("localize", "status", STATUS["localize"])
        answer = state["answer"]
        try:
            system = prompts.TRANSLATE.format(language=LANGUAGE_NAMES[state["language"]])
            translated = llm("fast").invoke([SystemMessage(system), HumanMessage(answer)]).content
        except Exception as error:  # noqa: BLE001 - an untranslated answer beats no answer
            emit("localize", "error", f"Translation failed: {type(error).__name__}")
            return {"answer": answer + "\n\n(Translation unavailable, so this answer is shown in English.)"}
        if not numbers_preserved(answer, translated):
            emit("localize", "error", "Translation changed a number; keeping the English answer")
            return {"answer": answer + "\n\n(The translation altered some numbers, so this answer is shown in "
                                       "English.)"}
        emit("localize", "action", f"Translated into {LANGUAGE_NAMES[state['language']]}")
        return {"answer": translated}
    return localize


def respond(state: AgentState) -> dict:
    return {"messages": [AIMessage(state["answer"])]}
