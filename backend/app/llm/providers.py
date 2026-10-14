"""Chat models for each tier, with automatic fallback from OpenAI (primary) to Anthropic (fallback).

    model = chat("quality", tools=[...])         # tool-calling agent step
    model = chat("fast", schema=TriageResult)    # structured output

If the primary call fails (error, timeout, or malformed structured output), the same request goes to the
fallback. FORCE_PRIMARY_FAIL swaps the primary for a model that always fails, to demonstrate this.

The main model and the reasoning effort (low/medium/high) can be chosen per request (see graph/options.py).
They apply to the quality tier; the fast tier always uses its configured models at low effort. Choosing a
Claude model as the main model makes the OpenAI model at the same price its fallback, and vice versa.
"""
import warnings
from collections.abc import Sequence
from typing import Any, Literal

from langchain_anthropic import ChatAnthropic
from langchain_core.language_models import BaseChatModel
from langchain_core.language_models.base import LangSmithParams
from langchain_core.outputs import ChatResult
from langchain_core.runnables import Runnable
from langchain_openai import ChatOpenAI
from pydantic import BaseModel

from app.config import Settings, get_settings
from app.graph.options import FALLBACK_FOR, Effort, current_options

Tier = Literal["quality", "fast"]

# The OpenAI SDK types the `parsed` field of structured-output responses loosely, so pydantic warns whenever
# LangChain serialises one. The parsed result itself is correct; only this exact warning is silenced.
warnings.filterwarnings("ignore", message="Pydantic serializer warnings", category=UserWarning)


def provider_of(model: str) -> str:
    return "anthropic" if model.startswith("claude") else "openai"


class ProviderUnavailable(RuntimeError):
    """Raised by the stand-in primary model when FORCE_PRIMARY_FAIL is on."""


class ForcedFailureChatModel(BaseChatModel):
    """Fails every call before any network request, while reporting itself as the real primary model."""

    model_name: str
    provider: str = "openai"

    @property
    def _llm_type(self) -> str:
        return "forced-failure"

    def _get_ls_params(self, stop=None, **kwargs) -> LangSmithParams:
        return LangSmithParams(ls_provider=self.provider, ls_model_name=self.model_name, ls_model_type="chat")

    def _generate(self, messages, stop=None, run_manager=None, **kwargs) -> ChatResult:
        raise ProviderUnavailable(f"{self.model_name} forced to fail (FORCE_PRIMARY_FAIL)")

    def bind_tools(self, tools: Sequence[Any], **kwargs: Any) -> "ForcedFailureChatModel":
        return self


def make_model(name: str, effort: Effort, s: Settings) -> BaseChatModel:
    common = {"model": name, "timeout": s.llm_timeout_s, "max_retries": s.llm_max_retries,
              "max_tokens": s.llm_max_output_tokens, "reasoning_effort": effort}
    if provider_of(name) == "anthropic":
        return ChatAnthropic(api_key=s.anthropic_api_key, base_url=s.llm_anthropic_base_url, **common)
    return ChatOpenAI(api_key=s.openai_api_key, base_url=s.llm_openai_base_url, stream_usage=True, **common)


def build_models(tier: Tier, settings: Settings | None = None) -> tuple[BaseChatModel, BaseChatModel]:
    s = settings or get_settings()
    options = current_options()
    if tier == "quality":
        primary_name = options.model or s.llm_quality_primary
        fallback_name = FALLBACK_FOR.get(primary_name, s.llm_quality_fallback)
        effort: Effort = options.reasoning_effort or s.llm_reasoning_effort
    else:
        primary_name, fallback_name, effort = s.llm_fast_primary, s.llm_fast_fallback, "low"
    if s.force_primary_fail:
        primary: BaseChatModel = ForcedFailureChatModel(model_name=primary_name, provider=provider_of(primary_name))
    else:
        primary = make_model(primary_name, effort, s)
    return primary, make_model(fallback_name, effort, s)


def chat(
    tier: Tier,
    *,
    tools: Sequence[Any] | None = None,
    schema: type[BaseModel] | None = None,
    models: tuple[BaseChatModel, BaseChatModel] | None = None,
) -> Runnable:
    """A runnable that tries the primary model, then the fallback. Tools and schemas are bound to each
    model separately, because a fallback wrapper cannot bind them itself."""

    def prepare(model: BaseChatModel) -> Runnable:
        if tools:
            return model.bind_tools(tools)
        if schema:
            # Anthropic: native structured outputs (constrained decoding). LangChain's default, forced tool
            # calling, is not available on the newest Claude models and silently degrades to unforced calls.
            if isinstance(model, ChatAnthropic):
                return model.with_structured_output(schema, method="json_schema")
            return model.with_structured_output(schema)
        return model

    primary, fallback = models or build_models(tier)
    return prepare(primary).with_fallbacks([prepare(fallback)])
