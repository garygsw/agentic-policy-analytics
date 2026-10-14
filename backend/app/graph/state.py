"""Graph state shared by all agents. It is checkpointed per conversation thread, so any API request can
resume a conversation from its last checkpoint."""
from typing import Annotated, Any, Literal, TypedDict

from langchain_core.messages import AnyMessage
from langgraph.graph.message import add_messages
from pydantic import BaseModel, Field, field_validator

from app.guardrails.language import LangCode


class StandaloneQuestion(BaseModel):
    """The user's latest message as a self-contained English question."""
    question: str = Field(description="The question in English, with references to earlier turns resolved.")


class TriageDecision(BaseModel):
    """What a classifier (local model, Jev or LLM) returns."""
    category: Literal["in_scope", "off_topic", "vague"]
    reason: str = Field(description="One sentence explaining the category.")
    confidence: float = Field(default=1.0, ge=0, le=1)


class TriageResult(TriageDecision):
    decided_by: str = "llm"  # set by our code, never by the model: local | jev | llm


class CoordinatorStep(BaseModel):
    """One ReAct step of the coordinator: reason about progress, then choose the next action."""
    headline: str = Field(default="", description="Five to eight words for a live status line, e.g. "
                                                  "'Loading tech employment and GDP data'.")
    thought: str = Field(description="Reasoning about what is known so far and what is missing.")
    action: Literal["extract", "analyse", "clarify", "decline", "report"]
    instructions: str = Field(description="Task for the chosen agent. For clarify: what is unclear. For decline: "
                                          "the explanation addressed to the user.")


class Claim(BaseModel):
    text: str = Field(description="One finding or caveat, stated with its numbers.")
    citations: list[str] = Field(default_factory=list, description="IDs of the facts or sources supporting it.")


class Report(BaseModel):
    title: str
    summary: str
    findings: list[Claim]
    charts: list[str] = Field(default_factory=list, description="IDs of charts to show, in reading order.")
    caveats: list[Claim] = Field(default_factory=list, description="Limitations, each citing what it is based on.")

    @field_validator("caveats", mode="before")
    @classmethod
    def plain_caveats(cls, value: list) -> list:
        """Reports saved before caveats had citations hold plain strings."""
        return [{"text": v, "citations": []} if isinstance(v, str) else v for v in value or []]


class ReportTexts(BaseModel):
    """The translatable texts of a report, in order. IDs and numbers stay as they are."""
    title: str
    summary: str
    findings: list[str]
    caveats: list[str]
    chart_titles: list[str]


class Review(BaseModel):
    verdict: Literal["pass", "wrong_analysis", "poor_report"]
    reason: str


class AgentState(TypedDict, total=False):
    # Kept across turns
    messages: Annotated[list[AnyMessage], add_messages]
    language: LangCode
    require_plan_approval: bool
    # Reset at the start of every turn (see TURN_RESET)
    question: str
    triage: TriageResult
    clarifications: int
    pending_question: str
    coordinator_steps: list[CoordinatorStep]
    plan_approved: bool
    findings: dict[str, Any]
    report: Report | None
    report_display: Report | None       # the checked report in the user's language
    chart_titles: dict[str, str]         # chart ID -> title in the user's language
    report_html: str
    grounding_issues: list[str]
    review: Review | None
    revisions: int
    analysis_reworks: int  # times the reviewer sent the analysis back this turn
    answer: str
    outcome: str
    failed_node: str | None
    errors: list[str]


TURN_RESET: dict[str, Any] = {
    "question": "", "clarifications": 0, "pending_question": "", "coordinator_steps": [], "plan_approved": False,
    "findings": {}, "report": None, "report_display": None, "chart_titles": {}, "report_html": "",
    "grounding_issues": [], "review": None, "revisions": 0, "analysis_reworks": 0, "answer": "",
    "outcome": "", "failed_node": None, "errors": [],
}
