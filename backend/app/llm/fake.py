"""A scripted chat model for tests: no network, fully deterministic.

Give it the replies (or exceptions) it should produce, in order. It records every request it receives.
"""
import itertools
from collections.abc import Sequence
from typing import Any

from langchain_core.language_models import BaseChatModel
from langchain_core.language_models.base import LangSmithParams
from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from pydantic import BaseModel, ConfigDict, Field


class ScriptedChatModel(BaseChatModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    script: list[Any]  # AIMessage replies or exceptions to raise; Any so pydantic does not coerce them
    model_name: str = "scripted"
    provider: str = "fake"
    requests: list[list[BaseMessage]] = Field(default_factory=list)
    bound_tools: list[Any] = Field(default_factory=list)
    _counter: Any = None

    @property
    def _llm_type(self) -> str:
        return "scripted"

    def _get_ls_params(self, stop=None, **kwargs) -> LangSmithParams:
        return LangSmithParams(ls_provider=self.provider, ls_model_name=self.model_name, ls_model_type="chat")

    def _generate(self, messages, stop=None, run_manager=None, **kwargs) -> ChatResult:
        if self._counter is None:
            self._counter = itertools.count()
        self.requests.append(list(messages))
        index = next(self._counter)
        if index >= len(self.script):
            raise AssertionError(f"{self.model_name}: script exhausted after {len(self.script)} replies")
        reply = self.script[index]
        if isinstance(reply, Exception):
            raise reply
        return ChatResult(generations=[ChatGeneration(message=reply)])

    def bind_tools(self, tools: Sequence[Any], **kwargs: Any) -> "ScriptedChatModel":
        self.bound_tools = list(tools)
        return self


def reply(text: str = "", *, tool_calls: list[dict] | None = None, input_tokens: int = 100,
          output_tokens: int = 20, cache_read: int = 0, cache_write: int = 0) -> AIMessage:
    """An assistant reply with usage metadata, as a provider would return it."""
    calls = [{"name": c["name"], "args": c.get("args", {}), "id": c.get("id", f"call_{i}"), "type": "tool_call"}
             for i, c in enumerate(tool_calls or [])]
    return AIMessage(content=text, tool_calls=calls, usage_metadata={
        "input_tokens": input_tokens, "output_tokens": output_tokens, "total_tokens": input_tokens + output_tokens,
        "input_token_details": {"cache_read": cache_read, "cache_creation": cache_write},
    })


def structured(obj: BaseModel, **usage: int) -> AIMessage:
    """A reply that LangChain's with_structured_output parses into `obj`."""
    return reply(tool_calls=[{"name": type(obj).__name__, "args": obj.model_dump()}], **usage)
