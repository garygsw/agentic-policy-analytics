"""The Extraction and Analytics agents, as graph nodes.

The agents themselves are injected (see Workers), so the graph can be tested with fakes. Both write their
results into state["findings"]:
    findings["extraction"] = {"summary": str, "datasets": [...], "sources": [{"id": "S1", ...}], ...}
    findings["analytics"]  = {"summary": str, "facts": [{"id": "F1", "label", "value", "unit", "source"}],
                              "charts": [...]}
"""
from collections.abc import Callable
from typing import Any, Protocol

from app.graph.events import emit, guarded
from app.graph.state import AgentState


class Workers(Protocol):
    def extract(self, instructions: str, question: str, previous: dict[str, Any] | None = None) -> dict[str, Any]:
        ...
    def analyse(self, instructions: str, question: str, extraction: dict[str, Any],
                previous: dict[str, Any] | None = None) -> dict[str, Any]: ...


class StubWorkers:
    """Placeholder until the data and analytics agents are built: reports that no data was loaded."""

    def extract(self, instructions: str, question: str, previous: dict[str, Any] | None = None) -> dict[str, Any]:
        return {"summary": "Data extraction is not implemented yet; no datasets loaded.", "datasets": [],
                "sources": []}

    def analyse(self, instructions: str, question: str, extraction: dict[str, Any],
                previous: dict[str, Any] | None = None) -> dict[str, Any]:
        return {"summary": "Analytics is not implemented yet; no facts computed.", "facts": [], "charts": []}


def make_extraction(workers: Workers) -> Callable:
    @guarded("extraction")
    def extraction(state: AgentState) -> dict:
        findings = state.get("findings") or {}
        # A second extraction step (a retry, or more data the reviewer asked for) builds on the first.
        result = workers.extract(state["coordinator_steps"][-1].instructions, state["question"],
                                 findings.get("extraction"))
        emit("extraction", "observation", result.get("summary", ""))
        return {"findings": {**findings, "extraction": result}}
    return extraction


def make_analytics(workers: Workers) -> Callable:
    @guarded("analytics")
    def analytics(state: AgentState) -> dict:
        findings = state.get("findings") or {}
        # A second analysis step (e.g. after the reviewer's feedback) keeps the facts the first one computed.
        result = workers.analyse(state["coordinator_steps"][-1].instructions, state["question"],
                                 findings.get("extraction", {}), findings.get("analytics"))
        emit("analytics", "observation", result.get("summary", ""))
        return {"findings": {**findings, "analytics": result}}
    return analytics
