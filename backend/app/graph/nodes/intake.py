"""Intake: language, a standalone English question, triage, clarifying questions and off-topic redirects."""
from collections.abc import Callable

from langchain_core.messages import AIMessage, AnyMessage, HumanMessage, SystemMessage
from langgraph.types import interrupt

from app.graph import prompts
from app.graph.events import emit, guarded
from app.graph.state import TURN_RESET, AgentState, StandaloneQuestion, TriageDecision, TriageResult
from app.guardrails.language import LANGUAGE_NAMES, detect_language

MAX_CLARIFICATIONS = 2
HISTORY_MESSAGES = 10  # recent messages given to the model to resolve follow-up questions

Decider = Callable[[str], TriageResult]


def user_messages(messages: list[AnyMessage]) -> list[str]:
    return [m.content for m in messages if isinstance(m, HumanMessage)]


def detect_language_node(state: AgentState) -> dict:
    """First node of every turn: detect the language and clear the previous turn's working state."""
    language = detect_language(user_messages(state["messages"])[-1], state.get("language"))
    emit("detect_language", "observation", f"Language: {LANGUAGE_NAMES[language]}")
    return {"language": language, **TURN_RESET}


def make_understand(llm) -> Callable:
    @guarded("understand")
    def understand(state: AgentState) -> dict:
        texts = user_messages(state["messages"])
        if state["language"] == "en" and len(texts) == 1:
            question = texts[0]  # a first English message needs no rewriting
        else:
            result = llm("fast", schema=StandaloneQuestion).invoke(
                [SystemMessage(prompts.UNDERSTAND), *state["messages"][-HISTORY_MESSAGES:]])
            question = result.question
        emit("understand", "observation", f"Question: {question}")
        return {"question": question}
    return understand


def llm_decider(llm) -> Decider:
    def decide(question: str) -> TriageResult:
        decision = llm("fast", schema=TriageDecision).invoke([SystemMessage(prompts.TRIAGE), HumanMessage(question)])
        return TriageResult(**decision.model_dump(), decided_by="llm")
    return decide


def make_triage(decide: Decider) -> Callable:
    @guarded("triage")
    def triage(state: AgentState) -> dict:
        result = decide(state["question"])
        emit("triage", "reasoning", f"{result.category} ({result.confidence:.2f}, by {result.decided_by}): "
                                    f"{result.reason}")
        return {"triage": result}
    return triage


def make_clarify(llm) -> Callable:
    """Writes the clarifying question. Kept apart from ask_user, because a node that pauses is re-run when
    the conversation resumes, and the question should not be generated twice."""
    @guarded("clarify")
    def clarify(state: AgentState) -> dict:
        steps = state.get("coordinator_steps") or []
        unclear = steps[-1].instructions if steps and steps[-1].action == "clarify" else state["triage"].reason
        system = prompts.CLARIFY.format(scope=prompts.SCOPE, language=LANGUAGE_NAMES[state["language"]])
        text = llm("fast").invoke([SystemMessage(system),
                                   HumanMessage(f"Question: {state['question']}\nWhat is unclear: {unclear}")]).content
        emit("clarify", "action", f"Asking the user: {text}")
        return {"pending_question": text}
    return clarify


def ask_user(state: AgentState) -> dict:
    """Pause the run until the user answers; the API or CLI resumes it with the answer."""
    answer = interrupt({"kind": "clarification", "question": state["pending_question"]})
    return {
        "messages": [AIMessage(state["pending_question"]), HumanMessage(answer)],
        "clarifications": state.get("clarifications", 0) + 1,
        "language": detect_language(answer, state.get("language")),
    }


def redirect(state: AgentState) -> dict:
    emit("redirect", "action", "Off-topic: pointing the user to what the platform covers")
    return {"answer": prompts.REDIRECT[state["language"]], "outcome": "off_topic"}
