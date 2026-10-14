"""Report writing and checking, the deterministic fallback, and the final answer in the user's language."""
import json
from collections.abc import Callable

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from app.graph import prompts
from app.graph.events import emit, guarded
from app.graph.state import AgentState, Report, Review
from app.guardrails.language import LANGUAGE_NAMES
from app.quality.grounding import numbers_in, numbers_preserved, ungrounded

MAX_REVISIONS = 2  # shared by grounding fixes and reviewer feedback


def facts_of(state: AgentState) -> list[dict]:
    return ((state.get("findings") or {}).get("analytics") or {}).get("facts", [])


def sources_of(state: AgentState) -> list[dict]:
    """Analytics may load more datasets than extraction did, so its source list is the complete one."""
    findings = state.get("findings") or {}
    return (findings.get("analytics") or {}).get("sources") or (findings.get("extraction") or {}).get("sources", [])


def charts_of(state: AgentState) -> list[dict]:
    return ((state.get("findings") or {}).get("analytics") or {}).get("charts", [])


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
    """Deterministic checks: every number must match a computed fact (or the question), and every finding
    must cite known facts or sources."""
    report: Report = state["report"]
    facts = facts_of(state)
    allowed = [f["value"] for f in facts if isinstance(f.get("value"), int | float)]
    allowed += numbers_in(" ".join(str(f.get("label", "")) for f in facts)) + numbers_in(state["question"])
    known_ids = {f["id"] for f in facts} | {s["id"] for s in sources_of(state)}
    issues = [f"summary: {n:g} does not match any computed fact" for n in ungrounded(report.summary, allowed)]
    for i, claim in enumerate(report.findings, 1):
        issues += [f"finding {i}: {n:g} does not match any computed fact" for n in ungrounded(claim.text, allowed)]
        unknown = [c for c in claim.citations if c not in known_ids]
        if unknown:
            issues.append(f"finding {i}: cites unknown IDs {unknown}")
        if not claim.citations:
            issues.append(f"finding {i}: has no citation")
    unknown_charts = [c for c in report.charts if c not in {chart["id"] for chart in charts_of(state)}]
    if unknown_charts:
        issues.append(f"charts: unknown IDs {unknown_charts}")
    emit("validate", "observation", "Grounded: every number matches a computed fact" if not issues
         else f"{len(issues)} problem(s): " + "; ".join(issues))
    return {"grounding_issues": issues}


def make_reviewer(llm) -> Callable:
    @guarded("reviewer")
    def reviewer(state: AgentState) -> dict:
        analytics = (state.get("findings") or {}).get("analytics") or {}
        content = (f"Question: {state['question']}\n\nAnalysis performed: {analytics.get('summary', '')}\n\n"
                   f"Report:\n{state['report'].model_dump_json()}")
        review = llm("quality", schema=Review).invoke([SystemMessage(prompts.REVIEW), HumanMessage(content)])
        emit("reviewer", "reasoning", f"{review.verdict}: {review.reason}")
        return {"review": review}
    return reviewer


def render(state: AgentState) -> dict:
    report: Report = state["report"]
    lines = [f"# {report.title}", "", report.summary, "", "## Findings"]
    lines += [f"- {c.text} [{', '.join(c.citations)}]" for c in report.findings]
    caveats = list(report.caveats)
    review: Review | None = state.get("review")
    if review and review.verdict != "pass":
        caveats.append(f"Reviewer note: {review.reason}")
    if caveats:
        lines += ["", "## Caveats", *[f"- {c}" for c in caveats]]
    charts = {c["id"]: c for c in charts_of(state)}
    if report.charts:
        lines += ["", "## Charts", *[f"- [{cid}] {charts[cid]['title']}" for cid in report.charts if cid in charts]]
    if sources_of(state):
        lines += ["", "## Sources", *[f"- [{s['id']}] {s.get('title', '')} ({s.get('agency', '')}) {s.get('url', '')}"
                                      .rstrip() for s in sources_of(state)]]
    return {"answer": "\n".join(lines), "outcome": "answered"}


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
    return {"answer": "\n".join(lines), "outcome": "fallback"}


def cancel(state: AgentState) -> dict:
    return {"answer": "The analysis was cancelled at your request.", "outcome": "cancelled"}


def make_localize(llm) -> Callable:
    def localize(state: AgentState) -> dict:
        """Translate the English answer into the user's language, unless a number changed on the way."""
        if state["language"] == "en":
            return {}
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
