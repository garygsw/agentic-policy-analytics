"""Options a user can change for each request: the main model, the reasoning effort and the enabled tools.

They travel in the graph run's config (`configurable.options`), not in checkpointed state, so each message in
a conversation can use different settings. Code running inside a graph step reads them with current_options().
"""
from typing import Literal

from pydantic import BaseModel, field_validator

from app.llm.pricing import CONTEXT_WINDOWS, PRICES

Effort = Literal["low", "medium", "high"]

# Tools that can be switched off; the agents can still answer without them. Data loading and the core
# statistics are always on, because nothing can be answered without them.
OPTIONAL_TOOLS: dict[str, str] = {
    "search_datasets": "Search all 4,655 data.gov.sg datasets beyond the curated set",
    "search_documents": "Search agency press releases, speeches and parliamentary replies",
    "web_search": "Search the web (government sites only, unless widened)",
    "compute_correlation": "Correlate two series, across datasets",
    "list_records": "List and count records in tables that are not time series",
    "create_chart": "Draw charts for the report",
}
REQUIRED_TOOLS: dict[str, str] = {
    "load_dataset": "Load a dataset with its quality report",
    "describe_dataset": "List a dimension's values",
    "compute_trend": "Trend, growth and year-on-year change of a series",
    "compare_groups": "Compare groups in one period, with shares",
}

# When a model is chosen as the main model, the fallback is the other provider's model at the same price.
FALLBACK_FOR = {"gpt-6.1-sol": "claude-sonnet-5-5", "claude-sonnet-5-5": "gpt-6.1-sol",
                "gpt-6-luna": "claude-haiku-5-5", "claude-haiku-5-5": "gpt-6-luna"}


class RunOptions(BaseModel):
    model: str | None = None              # main model for the coordinator, agents, writer and reviewer
    reasoning_effort: Effort | None = None
    tools: list[str] | None = None        # enabled optional tools; None means all
    web_scope: Literal["gov", "any"] = "gov"  # web search: Singapore government sites only, or the whole web

    @field_validator("model")
    @classmethod
    def known_model(cls, value: str | None) -> str | None:
        if value is not None and value not in FALLBACK_FOR:
            raise ValueError(f"unknown model '{value}'; choose one of {sorted(FALLBACK_FOR)}")
        return value

    @field_validator("tools")
    @classmethod
    def known_tools(cls, value: list[str] | None) -> list[str] | None:
        unknown = [t for t in value or [] if t not in OPTIONAL_TOOLS and t not in REQUIRED_TOOLS]
        if unknown:
            raise ValueError(f"unknown tools {unknown}; optional tools are {sorted(OPTIONAL_TOOLS)}")
        return value

    def tool_enabled(self, name: str) -> bool:
        return name in REQUIRED_TOOLS or self.tools is None or name in self.tools


def current_options() -> RunOptions:
    """Options of the graph run in progress; defaults when called outside a run."""
    try:
        from langgraph.config import get_config
        raw = get_config().get("configurable", {}).get("options") or {}
    except Exception:  # noqa: BLE001 - outside a graph run
        raw = {}
    return raw if isinstance(raw, RunOptions) else RunOptions(**raw)


def available_models() -> list[dict]:
    """What the model dropdown offers: provider, prices and context window of each model."""
    return [{"id": model, "provider": "anthropic" if model.startswith("claude") else "openai",
             "fallback": FALLBACK_FOR[model], "context_window": CONTEXT_WINDOWS.get(model),
             "price_per_million": {"input": PRICES[model].input, "output": PRICES[model].output}}
            for model in FALLBACK_FOR]


def available_tools() -> list[dict]:
    return ([{"name": n, "description": d, "required": True} for n, d in REQUIRED_TOOLS.items()]
            + [{"name": n, "description": d, "required": False} for n, d in OPTIONAL_TOOLS.items()])
