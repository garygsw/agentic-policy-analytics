"""Records every LLM call and tool call made during a run.

Attach a Ledger as a LangChain callback. For each LLM call it records the agent (graph node), the provider
and model, latency, token usage split into uncached input / cache read / cache write / output, cost, the
tools the model asked for, and an estimated breakdown of what filled the context window. Failed calls are
recorded too, which is how a provider fallback becomes visible.
"""
import json
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from typing import Any
from uuid import UUID

import tiktoken
from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.outputs import LLMResult

from app.llm.pricing import TokenUsage, cost_usd

# Tool results from these tools count as "retrieved" context rather than plain tool output.
RETRIEVAL_TOOLS = {"search_documents", "web_search"}
CONTEXT_CATEGORIES = ("system", "user", "assistant", "tool_results", "retrieved", "tool_schemas")

# One tokenizer for every provider: the breakdown is an estimate, the provider's own count is authoritative.
_ENCODING = tiktoken.get_encoding("o200k_base")


def count_tokens(text: str) -> int:
    return len(_ENCODING.encode(text, disallowed_special=()))


def _text(message: BaseMessage) -> str:
    parts = [message.content if isinstance(message.content, str) else json.dumps(message.content)]
    if isinstance(message, AIMessage) and message.tool_calls:
        parts.append(json.dumps([{"name": c["name"], "args": c["args"]} for c in message.tool_calls]))
    return "\n".join(parts)


def context_breakdown(messages: list[BaseMessage], tools: list[Any] | None = None) -> dict[str, int]:
    """Estimated tokens per category of what is sent to the model."""
    counts = dict.fromkeys(CONTEXT_CATEGORIES, 0)
    for message in messages:
        if isinstance(message, SystemMessage):
            category = "system"
        elif isinstance(message, HumanMessage):
            category = "user"
        elif isinstance(message, ToolMessage):
            category = "retrieved" if message.name in RETRIEVAL_TOOLS else "tool_results"
        else:
            category = "assistant"
        counts[category] += count_tokens(_text(message))
    if tools:
        counts["tool_schemas"] = count_tokens(json.dumps(tools, default=str))
    return counts


@dataclass
class LLMCall:
    agent: str | None
    provider: str
    model: str
    ok: bool = True
    error: str | None = None
    latency_ms: int = 0
    usage: TokenUsage = field(default_factory=TokenUsage)
    cost_usd: float | None = None
    tool_calls: list[str] = field(default_factory=list)
    context_estimate: dict[str, int] = field(default_factory=dict)


@dataclass
class ToolCall:
    agent: str | None
    name: str
    ok: bool = True
    error: str | None = None
    latency_ms: int = 0
    output_chars: int = 0


class Ledger(BaseCallbackHandler):
    run_inline = True  # keep records in call order, also under asyncio

    def __init__(self, on_record: Callable[[str, dict], None] | None = None):
        self.calls: list[LLMCall] = []
        self.tools: list[ToolCall] = []
        self._open: dict[UUID, tuple[float, Any]] = {}
        self._on_record = on_record

    # -- LLM calls ---------------------------------------------------------
    def on_chat_model_start(self, serialized, messages, *, run_id, metadata=None, **kwargs):
        metadata = metadata or {}
        params = kwargs.get("invocation_params") or {}
        call = LLMCall(
            agent=metadata.get("langgraph_node") or metadata.get("agent"),
            provider=metadata.get("ls_provider") or "unknown",
            model=metadata.get("ls_model_name") or params.get("model") or params.get("model_name") or "unknown",
            context_estimate=context_breakdown(messages[0] if messages else [], params.get("tools")),
        )
        self._open[run_id] = (time.perf_counter(), call)

    def on_llm_end(self, response: LLMResult, *, run_id, **kwargs):
        started, call = self._open.pop(run_id, (None, None))
        if call is None:
            return
        message = response.generations[0][0].message if response.generations and response.generations[0] else None
        if isinstance(message, AIMessage):
            call.usage = TokenUsage.from_metadata(message.usage_metadata)
            call.tool_calls = [c["name"] for c in message.tool_calls]
        call.cost_usd = cost_usd(call.model, call.usage)
        self._close(started, call, self.calls, "llm_call")

    def on_llm_error(self, error: BaseException, *, run_id, **kwargs):
        started, call = self._open.pop(run_id, (None, None))
        if call is None:
            return
        call.ok, call.error = False, f"{type(error).__name__}: {str(error)[:200]}"
        self._close(started, call, self.calls, "llm_call")

    # -- tool calls --------------------------------------------------------
    def on_tool_start(self, serialized, input_str, *, run_id, metadata=None, **kwargs):
        metadata = metadata or {}
        name = (serialized or {}).get("name") or kwargs.get("name") or "unknown"
        self._open[run_id] = (time.perf_counter(), ToolCall(agent=metadata.get("langgraph_node"), name=name))

    def on_tool_end(self, output, *, run_id, **kwargs):
        started, call = self._open.pop(run_id, (None, None))
        if call is None:
            return
        call.output_chars = len(output.content if isinstance(output, ToolMessage) else str(output))
        self._close(started, call, self.tools, "tool_call")

    def on_tool_error(self, error: BaseException, *, run_id, **kwargs):
        started, call = self._open.pop(run_id, (None, None))
        if call is None:
            return
        call.ok, call.error = False, f"{type(error).__name__}: {str(error)[:200]}"
        self._close(started, call, self.tools, "tool_call")

    def _close(self, started: float, record, records: list, kind: str) -> None:
        record.latency_ms = int((time.perf_counter() - started) * 1000)
        records.append(record)
        if self._on_record:
            self._on_record(kind, asdict(record))

    # -- summary -----------------------------------------------------------
    def totals(self) -> dict[str, Any]:
        ok = [c for c in self.calls if c.ok]
        priced = [c.cost_usd for c in ok if c.cost_usd is not None]
        return {
            "llm_calls": len(self.calls),
            "failed_llm_calls": len(self.calls) - len(ok),
            "tool_calls": len(self.tools),
            "input_uncached": sum(c.usage.input_uncached for c in ok),
            "cache_read": sum(c.usage.cache_read for c in ok),
            "cache_write": sum(c.usage.cache_write for c in ok),
            "output": sum(c.usage.output for c in ok),
            "cost_usd": round(sum(priced), 6),
            "unpriced_calls": len(ok) - len(priced),
            "models": sorted({f"{c.provider}:{c.model}" for c in ok}),
        }
