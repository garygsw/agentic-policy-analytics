"""Wires the agents into one LangGraph StateGraph.

    detect_language -> understand -> triage --off_topic--> redirect ---------------------------> respond
                                       |--vague--> clarify -> ask_user (pause) -> understand
                                       '--in_scope--> coordinator <--------------------------------.
                                                        |-- extract --> [approve_plan] -> extraction -'
                                                        |-- analyse --> analytics --------------------'
                                                        |-- clarify --> clarify -> ask_user (pause)
                                                        |-- decline --> decline --------> localize -> respond
                                                        '-- report  --> report_writer -> validate -> reviewer
                                                                           ^     (fix)     |  (wrong analysis:
                                                                           '---------------'   back to coordinator)
                                                     -> localize_report -> render -> respond
                                                  (fallback_report, decline, cancel -> localize -> respond)
Any failing step routes to fallback_report.
"""
from dataclasses import dataclass

from langgraph.graph import END, START, StateGraph

from app.agents.workers import AgentWorkers
from app.graph.nodes import coordinator as coord
from app.graph.nodes import intake, reporting
from app.graph.nodes import workers as work
from app.graph.state import AgentState
from app.guardrails.triage import make_triage_decider
from app.llm.providers import chat


@dataclass
class Deps:
    llm: object = chat                      # factory: llm(tier, tools=..., schema=...) -> Runnable
    decide: intake.Decider | None = None    # triage decision; defaults to the fast LLM
    workers: work.Workers | None = None    # defaults to the tool-using Extraction and Analytics agents


def _failed(state: AgentState) -> bool:
    return bool(state.get("failed_node"))


def route_after(default: str):
    def route(state: AgentState) -> str:
        return "fallback_report" if _failed(state) else default
    return route


def route_triage(state: AgentState) -> str:
    if _failed(state):
        return "fallback_report"
    category = state["triage"].category
    if category == "off_topic":
        return "redirect"
    if category == "vague" and state.get("clarifications", 0) < intake.MAX_CLARIFICATIONS:
        return "clarify"
    return "coordinator"


def route_coordinator(state: AgentState) -> str:
    if _failed(state):
        return "fallback_report"
    action = state["coordinator_steps"][-1].action
    if action == "extract":
        needs_approval = state.get("require_plan_approval") and not state.get("plan_approved")
        return "approve_plan" if needs_approval else "extraction"
    if action == "clarify":
        return "clarify" if state.get("clarifications", 0) < intake.MAX_CLARIFICATIONS else "decline"
    return {"analyse": "analytics", "decline": "decline", "report": "report_writer"}[action]


def route_plan(state: AgentState) -> str:
    if state.get("plan_approved"):
        return "extraction"
    feedback = (state.get("findings") or {}).get("plan_feedback", "")
    return "cancel" if feedback == "rejected without feedback" else "coordinator"


def route_validate(state: AgentState) -> str:
    if not state.get("grounding_issues"):
        return "reviewer"
    # Out of fixes: nothing is dropped. The report goes on, and its unverified statements are flagged (D-034).
    return "report_writer" if state.get("revisions", 0) < reporting.MAX_REVISIONS else "reviewer"


def route_review(state: AgentState) -> str:
    review = state.get("review")
    if _failed(state) or review is None or review.verdict == "pass":
        return "localize_report"  # the report is already grounded; a failed review should not discard it
    if review.verdict == "wrong_analysis":
        # One more round of analysis at most; a second objection is shown with the report ("partial").
        again = state.get("analysis_reworks", 0) <= reporting.MAX_ANALYSIS_REWORKS
        return "coordinator" if again else "localize_report"
    return "report_writer" if state.get("revisions", 0) < reporting.MAX_REVISIONS else "localize_report"


def build_graph(deps: Deps | None = None, checkpointer=None):
    deps = deps or Deps()
    workers = deps.workers or AgentWorkers(deps.llm)
    g = StateGraph(AgentState)
    g.add_node("detect_language", intake.detect_language_node)
    g.add_node("understand", intake.make_understand(deps.llm))
    g.add_node("triage", intake.make_triage(deps.decide or make_triage_decider(deps.llm)))
    g.add_node("clarify", intake.make_clarify(deps.llm))
    g.add_node("ask_user", intake.ask_user)
    g.add_node("redirect", intake.redirect)
    g.add_node("coordinator", coord.make_coordinator(deps.llm))
    g.add_node("approve_plan", coord.approve_plan)
    g.add_node("decline", coord.decline)
    g.add_node("extraction", work.make_extraction(workers))
    g.add_node("analytics", work.make_analytics(workers))
    g.add_node("report_writer", reporting.make_report_writer(deps.llm))
    g.add_node("validate", reporting.validate)
    g.add_node("reviewer", reporting.make_reviewer(deps.llm))
    g.add_node("localize_report", reporting.make_localize_report(deps.llm))
    g.add_node("render", reporting.render)
    g.add_node("fallback_report", reporting.fallback_report)
    g.add_node("cancel", reporting.cancel)
    g.add_node("localize", reporting.make_localize(deps.llm))
    g.add_node("respond", reporting.respond)

    g.add_edge(START, "detect_language")
    g.add_edge("detect_language", "understand")
    g.add_conditional_edges("understand", route_after("triage"), ["triage", "fallback_report"])
    g.add_conditional_edges("triage", route_triage, ["redirect", "clarify", "coordinator", "fallback_report"])
    g.add_conditional_edges("clarify", route_after("ask_user"), ["ask_user", "fallback_report"])
    g.add_edge("ask_user", "understand")
    g.add_conditional_edges("coordinator", route_coordinator,
                            ["approve_plan", "extraction", "analytics", "clarify", "decline", "report_writer",
                             "fallback_report"])
    g.add_conditional_edges("approve_plan", route_plan, ["extraction", "coordinator", "cancel"])
    g.add_conditional_edges("extraction", route_after("coordinator"), ["coordinator", "fallback_report"])
    g.add_conditional_edges("analytics", route_after("coordinator"), ["coordinator", "fallback_report"])
    g.add_conditional_edges("report_writer", route_after("validate"), ["validate", "fallback_report"])
    g.add_conditional_edges("validate", route_validate, ["reviewer", "report_writer"])
    g.add_conditional_edges("reviewer", route_review, ["localize_report", "coordinator", "report_writer"])
    g.add_edge("localize_report", "render")
    g.add_edge("render", "respond")  # rendered in the user's language already
    for node in ("decline", "fallback_report", "cancel"):
        g.add_edge(node, "localize")
    g.add_edge("redirect", "respond")  # redirect replies are already written in every language
    g.add_edge("localize", "respond")
    g.add_edge("respond", END)
    return g.compile(checkpointer=checkpointer)
