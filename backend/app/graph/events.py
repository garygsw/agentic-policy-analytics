"""Agent trace events and error boundaries for graph nodes.

Every event the UI and CLI receive has this shape:
    {"node": str, "kind": str, "level": 1|2|3, "content": str, "ts": epoch seconds, ...extra fields}

kind     level  meaning
status     1    one-line description of what is happening now (the live status line)
action     1    what an agent decided to do (a tool call, a coordinator choice)
error      1    a failure that was handled (fallback, retry, refused request)
usage      1    running totals after each LLM call: calls, tokens, cost (for the header counters)
reasoning  2    why: the coordinator's thought, a reviewer's verdict, a triage decision
observation 2   what a tool returned (a preview; the full text is in `detail`)
thinking   3    the model's own thinking, when the provider returns it
context    3    what filled an agent's context window for one LLM call

Clients choose a verbosity (1 brief, 2 standard, 3 full) and show events up to that level. Everything is
always emitted, so the level can be changed while a run is in progress or when replaying it.
"""
import functools
import time
from collections.abc import Callable

from langgraph.config import get_stream_writer
from langgraph.errors import GraphBubbleUp

LEVELS = {"status": 1, "action": 1, "error": 1, "usage": 1, "reasoning": 2, "observation": 2, "thinking": 3,
          "context": 3}

# The status line shown when each step starts.
STATUS = {
    "understand": "Reading the question", "triage": "Checking the question is in scope",
    "clarify": "Writing a clarifying question", "coordinator": "Planning the next step",
    "extraction": "Finding and loading data", "analytics": "Computing statistics and charts",
    "report_writer": "Writing the report", "validate": "Checking every number against the computed facts",
    "reviewer": "Reviewing whether the report answers the question", "localize": "Translating the answer",
    "fallback_report": "Building a report from the computed figures only",
}


def emit(node: str, kind: str, content: str, **data) -> None:
    """Stream one trace event. Outside a graph run (a node called directly, a model called on its own) there is
    no stream and nothing is sent. Tracing must never break a run, so any lookup failure is ignored."""
    try:
        writer = get_stream_writer()
    except Exception:  # noqa: BLE001
        return
    writer({"node": node, "kind": kind, "level": LEVELS.get(kind, 2), "content": content, "ts": time.time(),
            **data})


def guarded(name: str) -> Callable:
    """Announce the step, and record an exception in it instead of crashing the run, so routing can send the
    run to the deterministic fallback report. Interrupts (GraphBubbleUp) must pass through untouched."""
    def wrap(fn: Callable) -> Callable:
        @functools.wraps(fn)
        def node(state, *args, **kwargs):
            emit(name, "status", STATUS.get(name, name))
            try:
                return fn(state, *args, **kwargs)
            except GraphBubbleUp:
                raise
            except Exception as error:  # noqa: BLE001 - any failure becomes a visible, recorded fallback
                message = f"{name}: {type(error).__name__}: {str(error)[:300]}"
                emit(name, "error", message)
                return {"failed_node": name, "errors": [*state.get("errors", []), message]}
        return node
    return wrap
