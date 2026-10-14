"""Agent trace events and error boundaries for graph nodes."""
import functools
from collections.abc import Callable

from langgraph.config import get_stream_writer
from langgraph.errors import GraphBubbleUp


def emit(node: str, kind: str, content: str, **data) -> None:
    """Stream one trace event: kind is reasoning, action, observation or error.
    Outside a graph run (e.g. calling a node directly in a test) there is no stream, so nothing is sent."""
    try:
        writer = get_stream_writer()
    except RuntimeError:
        return
    writer({"node": node, "kind": kind, "content": content, **data})


def guarded(name: str) -> Callable:
    """Record an exception in a node instead of crashing the run, so routing can send the run to the
    deterministic fallback report. Interrupts (GraphBubbleUp) must pass through untouched."""
    def wrap(fn: Callable) -> Callable:
        @functools.wraps(fn)
        def node(state, *args, **kwargs):
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
