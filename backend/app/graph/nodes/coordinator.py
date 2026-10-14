"""The Coordinator: a ReAct agent that reasons about progress and chooses the next action."""
from collections.abc import Callable

from langchain_core.messages import HumanMessage, SystemMessage
from langgraph.types import interrupt

from app.analysis.workspace import curated_titles
from app.graph import prompts
from app.graph.events import emit, guarded
from app.graph.state import AgentState, CoordinatorStep

MAX_STEPS = 6


def describe_progress(state: AgentState) -> str:
    lines = [f"{i}. {s.action}: {s.instructions}" for i, s in enumerate(state.get("coordinator_steps") or [], 1)]
    findings = state.get("findings") or {}
    for agent in ("extraction", "analytics"):
        if agent in findings:
            lines.append(f"{agent} result: {findings[agent].get('summary', '')}")
    # Recorded by the system, so an agent's summary cannot hide what is actually available.
    loaded = [s for s in (findings.get("extraction") or {}).get("sources", []) if s.get("kind", "dataset") == "dataset"]
    if loaded:
        lines.append("Datasets loaded and ready to analyse: " + "; ".join(f"{s['id']} {s.get('title', '')}"
                                                                         for s in loaded))
    analytics = findings.get("analytics") or {}
    if "facts" in analytics:
        lines.append(f"Facts computed so far: {len(analytics['facts'])}; charts: {len(analytics.get('charts', []))}")
    if findings.get("plan_feedback"):
        lines.append(f"User feedback on the plan: {findings['plan_feedback']}")
    review = state.get("review")
    if review and review.verdict == "wrong_analysis":
        lines.append(f"Reviewer sent the analysis back: {review.reason}")
    return "\n".join(lines) or "Nothing yet."


def make_coordinator(llm) -> Callable:
    @guarded("coordinator")
    def coordinator(state: AgentState) -> dict:
        steps = state.get("coordinator_steps") or []
        if len(steps) >= MAX_STEPS:
            step = CoordinatorStep(thought="Step limit reached.", action="report",
                                   instructions="Report what has been found so far, and say what is missing.")
        else:
            system = prompts.COORDINATOR.format(max_steps=MAX_STEPS, progress=describe_progress(state),
                                                datasets=curated_titles())
            step = llm("quality", schema=CoordinatorStep).invoke([SystemMessage(system),
                                                                  HumanMessage(state["question"])])
        emit("coordinator", "status", step.headline or f"Next: {step.action}")
        emit("coordinator", "reasoning", step.thought)
        emit("coordinator", "action", f"{step.action}: {step.instructions}")
        return {"coordinator_steps": [*steps, step]}
    return coordinator


def approve_plan(state: AgentState) -> dict:
    """Optional human approval of the plan before any data is fetched (off by default)."""
    step = state["coordinator_steps"][-1]
    decision = interrupt({"kind": "plan_approval", "plan": step.instructions, "reasoning": step.thought})
    if not isinstance(decision, dict):
        decision = {"approved": str(decision).strip().lower() in ("y", "yes", "approve", "approved")}
    findings = dict(state.get("findings") or {})
    if decision.get("approved"):
        emit("approve_plan", "observation", "Plan approved by the user")
        return {"plan_approved": True}
    findings["plan_feedback"] = decision.get("feedback") or "rejected without feedback"
    emit("approve_plan", "observation", f"Plan rejected: {findings['plan_feedback']}")
    return {"plan_approved": False, "findings": findings}


def decline(state: AgentState) -> dict:
    reason = state["coordinator_steps"][-1].instructions
    emit("decline", "action", reason)
    return {"answer": f"This question cannot be answered with the available data. {reason}", "outcome": "declined"}
