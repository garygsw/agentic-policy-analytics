"""Report writing and checking, the deterministic fallback, and the final answer in the user's language."""
import json
from collections.abc import Callable

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from app.analysis.charts import plain
from app.graph import prompts
from app.graph.events import STATUS, emit, guarded
from app.graph.state import AgentState, Report, ReportTexts, Review
from app.guardrails.language import LANGUAGE_NAMES
from app.quality.grounding import fact_values, numbers_in, numbers_preserved, quote_around, ungrounded
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
        elif state.get("review") and state["review"].verdict != "wrong_analysis":  # a new analysis starts afresh
            review: Review = state["review"]
            notes = [f"Reviewer feedback on your previous draft: {review.reason}"] if review.verdict == "poor_report" \
                else []
            if review.unsupported:
                notes.append("These statements are not supported by what they cite. Correct them to say only what "
                             "the cited facts and sources show, or cite what supports them:\n- "
                             + "\n- ".join(f"{p.where}: {p.problem}" for p in review.unsupported))
            feedback = "\n\n".join(notes)
        charts = [{"id": c["id"], "title": c["title"]} for c in charts_of(state)]
        content = (f"Question: {state['question']}\n\nFacts:\n{json.dumps(facts_of(state), ensure_ascii=False)}\n\n"
                   f"Charts:\n{json.dumps(charts, ensure_ascii=False)}\n\n"
                   f"Sources:\n{json.dumps(sources_of(state), ensure_ascii=False)}\n\n{feedback}").strip()
        report = llm("quality", schema=Report).invoke([SystemMessage(prompts.REPORT), HumanMessage(content)])
        emit("report_writer", "action", f"{'Revised' if revising else 'Wrote'} report: {report.title}")
        return {"report": report, "revisions": state.get("revisions", 0) + int(revising)}
    return report_writer


def citable_text(sources: list[dict]) -> dict[str, str]:
    """What a finding or caveat may take numbers from when it cites a source: a passage's text, or a dataset's
    notes, quality report and dates (e.g. "5,546 values from 12,012 source rows", "retrieved 2026-10-14")."""
    texts = {}
    for s in sources:
        keys = (("text", "published") if s.get("kind") in ("document", "web")
                else ("notes", "quality", "warning", "retrieved_at"))
        texts[s["id"]] = " ".join(str(s.get(k) or "") for k in keys)
    return texts


def validate(state: AgentState) -> dict:
    """Deterministic checks: every number must match a computed fact (or the question), or appear in a source the
    same finding or caveat cites; and every finding and caveat must cite known facts or sources."""
    emit("validate", "status", STATUS["validate"])
    report: Report = state["report"]
    facts = facts_of(state)
    allowed = fact_values(facts)
    allowed += numbers_in(" ".join(str(f.get("label", "")) for f in facts)) + numbers_in(state["question"])
    known_ids = {f["id"] for f in facts} | {s["id"] for s in sources_of(state)} | {c["id"] for c in charts_of(state)}
    citable = citable_text(sources_of(state))

    def quoted(citations: list[str]) -> list[float]:
        return numbers_in(" ".join(citable[c] for c in citations if c in citable))

    cited_anywhere = [c for claim in report.findings for c in claim.citations]
    flags: list[dict] = []  # one per problem: which statement, and what could not be verified

    def flag(where: str, problem: str, text: str = "", number: float | None = None) -> None:
        quote = f' (in "{quote_around(text, number)}")' if number is not None else ""
        flags.append({"where": where, "problem": problem, "message": f"{where}: {problem}{quote}"})

    for n in ungrounded(report.summary, allowed + quoted(cited_anywhere)):
        flag("summary", f"{_show(n)} does not match any computed fact or cited passage", report.summary, n)
    # Findings and caveats are checked alike: numbers grounded, citations present and known.
    claims = [("finding", i, c) for i, c in enumerate(report.findings, 1)]
    claims += [("caveat", i, c) for i, c in enumerate(report.caveats, 1)]
    for kind, i, claim in claims:
        for n in ungrounded(claim.text, allowed + quoted(claim.citations)):
            flag(f"{kind} {i}", f"{_show(n)} does not match any computed fact or a source this {kind} cites",
                 claim.text, n)
        unknown = [c for c in claim.citations if c not in known_ids]
        if unknown:
            flag(f"{kind} {i}", f"cites unknown IDs {unknown}")
        if not claim.citations:
            flag(f"{kind} {i}", "has no citation")
    unknown_charts = [c for c in report.charts if c not in {chart["id"] for chart in charts_of(state)}]
    if unknown_charts:
        flag("charts", f"unknown IDs {unknown_charts}")
    issues = [f["message"] for f in flags]
    emit("validate", "observation", "Grounded: every number matches a computed fact" if not issues
         else f"{len(issues)} problem(s): " + "; ".join(issues), passed=not issues, issues=issues)
    return {"grounding_issues": issues, "grounding_flags": flags}


def make_reviewer(llm) -> Callable:
    @guarded("reviewer")
    def reviewer(state: AgentState) -> dict:
        analytics = (state.get("findings") or {}).get("analytics") or {}
        report: Report = state["report"]
        statements = [f"summary: {report.summary}"]
        statements += [f"finding {i}: {c.text} [{', '.join(c.citations)}]" for i, c in enumerate(report.findings, 1)]
        statements += [f"caveat {i}: {c.text} [{', '.join(c.citations)}]" for i, c in enumerate(report.caveats, 1)]
        material = {"facts": facts_of(state),
                    "sources": [{k: v for k, v in s.items() if k != "key"} for s in sources_of(state)]}
        content = (f"Question: {state['question']}\n\nAnalysis performed: {analytics.get('summary', '')}\n\n"
                   f"Report title: {report.title}\n\nStatements:\n" + "\n".join(statements)
                   + f"\n\nFacts and sources the writer had:\n{json.dumps(material, ensure_ascii=False)}")
        # The other provider's model checks, so the writer's model never checks its own work.
        review = llm("check", schema=Review).invoke([SystemMessage(prompts.REVIEW), HumanMessage(content)])
        emit("reviewer", "reasoning", f"{review.verdict}: {review.reason}")
        if review.unsupported:
            emit("reviewer", "observation", f"{len(review.unsupported)} statement(s) not supported by their citations: "
                 + "; ".join(f"{p.where}: {p.problem}" for p in review.unsupported))
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
                             caveats=[c.text for c in report.caveats], chart_titles=[c["title"] for c in charts])
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
        caveats = [c.model_copy(update={"text": t}) for c, t in zip(report.caveats, texts.caveats, strict=True)]
        translated = report.model_copy(update={"title": texts.title, "summary": texts.summary,
                                               "caveats": caveats, "findings": findings})
        titles = {c["id"]: t for c, t in zip(charts, texts.chart_titles, strict=True)}
        return {"report_display": translated, "chart_titles": titles}
    return localize_report


def charts_for_display(state: AgentState) -> list[dict]:
    titles = state.get("chart_titles") or {}
    shown = []
    for chart in charts_of(state):
        title = titles.get(chart["id"], chart["title"])
        spec = {**chart["spec"], "layout": {**chart["spec"]["layout"], "title": {"text": plain(title)}}}
        shown.append({**chart, "title": title, "spec": spec})
    return shown


def report_warnings(state: AgentState) -> dict[str, list[str]]:
    """What could not be verified, by statement ("summary", "finding 2", "caveat 1"). Nothing is dropped: such a
    statement is shown with a warning (D-034). Used by the HTML report and the Markdown and PDF downloads."""
    warnings: dict[str, list[str]] = {}
    for f in state.get("grounding_flags") or []:  # the number check, after the writer's fixes
        warnings.setdefault(f["where"], []).append(f["problem"])
    review: Review | None = state.get("review")
    for p in review.unsupported if review else []:  # the claim check, after the writer's fixes
        warnings.setdefault(p.where, []).append(p.problem)
    return warnings


def render(state: AgentState) -> dict:
    """The answer for the conversation (markdown) and the full report page (HTML), in the user's language."""
    report: Report = state.get("report_display") or state["report"]
    review: Review | None = state.get("review")
    review_note = review.reason if review and review.verdict != "pass" else None
    charts = charts_for_display(state)
    sources = shown_sources(sources_of(state), report)
    warnings = report_warnings(state)
    unsupported = review.unsupported if review else []

    def marked(where: str, text: str) -> str:
        return text + "".join(f" ⚠ Unverified: {w}" for w in warnings.get(where, []))

    lines = [f"# {report.title}", "", marked("summary", report.summary), "", "## Findings"]
    lines += [marked(f"finding {i}", f"- {c.text} [{', '.join(c.citations)}]")
              for i, c in enumerate(report.findings, 1)]
    caveats = [marked(f"caveat {i}", f"{c.text} [{', '.join(c.citations)}]") for i, c in enumerate(report.caveats, 1)]
    caveats += [f"Reviewer note: {review_note}"] if review_note else []
    if caveats:
        lines += ["", "## Caveats", *[f"- {c}" for c in caveats]]
    by_id = {c["id"]: c for c in charts}
    if report.charts:
        lines += ["", "## Charts", *[f"- [{cid}] {by_id[cid]['title']}" for cid in report.charts if cid in by_id]]
    if sources:
        lines += ["", "## Sources", *[f"- [{s['id']}] {s.get('title', '')} ({s.get('agency', '')}) {s.get('url', '')}"
                                      .rstrip() for s in sources]]
    html = render_html(question=state["question"], language=state["language"], report=report,
                       facts=facts_of(state), sources=sources, charts=charts, review_note=review_note,
                       warnings=warnings)
    # Every report is an answer; what could not be verified, and the reviewer's note, travel with it as flags.
    flags = [f["message"] for f in state.get("grounding_flags") or []]
    flags += [f"{p.where}: {p.problem} (not supported by its citations)" for p in unsupported]
    flags += [f"Reviewer note: {review_note}"] if review_note else []
    return {"answer": "\n".join(lines), "report_html": html, "outcome": "answered", "flags": flags}


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
                 "The language models failed before the report could be written, so these are the computed "
                 "figures without commentary.", ""]
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
