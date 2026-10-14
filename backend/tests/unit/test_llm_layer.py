"""LLM layer: pricing, provider fallback and the call ledger. No network."""
import pytest
from langchain_core.messages import HumanMessage, SystemMessage, ToolMessage
from langchain_core.tools import tool
from pydantic import BaseModel

from app.config import Settings
from app.llm.fake import ScriptedChatModel, reply, structured
from app.llm.ledger import Ledger, context_breakdown
from app.llm.pricing import PRICES, TokenUsage, cost_usd
from app.llm.providers import ForcedFailureChatModel, ProviderUnavailable, build_models, chat


# -- pricing ---------------------------------------------------------------
def test_usage_splits_cache_out_of_total_input():
    usage = TokenUsage.from_metadata({"input_tokens": 1000, "output_tokens": 50, "total_tokens": 1050,
                                      "input_token_details": {"cache_read": 600, "cache_creation": 100}})
    assert (usage.input_uncached, usage.cache_read, usage.cache_write, usage.output) == (300, 600, 100, 50)
    assert usage.input_total == 1000


def test_cost_uses_each_token_class_price():
    usage = TokenUsage(input_uncached=1_000_000, cache_read=1_000_000, cache_write=1_000_000, output=1_000_000)
    p = PRICES["claude-sonnet-5-5"]
    assert cost_usd("claude-sonnet-5-5", usage) == pytest.approx(p.input + p.cache_read + p.cache_write + p.output)


def test_unknown_model_is_unpriced_not_guessed():
    assert cost_usd("some-new-model", TokenUsage(output=10)) is None


# -- providers ---------------------------------------------------------------
def test_fallback_answers_when_primary_fails():
    primary = ScriptedChatModel(script=[RuntimeError("primary down")], model_name="p", provider="openai")
    fallback = ScriptedChatModel(script=[reply("from fallback")], model_name="f", provider="anthropic")
    assert chat("fast", models=(primary, fallback)).invoke("hi").content == "from fallback"


def test_force_primary_fail_swaps_in_failing_model():
    s = Settings(_env_file=None, force_primary_fail=True, openai_api_key="x", anthropic_api_key="y")
    primary, fallback = build_models("quality", s)
    assert isinstance(primary, ForcedFailureChatModel) and primary.model_name == s.llm_quality_primary
    with pytest.raises(ProviderUnavailable):
        primary.invoke("hi")


def test_base_urls_ignore_ambient_shell_variables(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "http://127.0.0.1:9")
    monkeypatch.setenv("OPENAI_BASE_URL", "http://127.0.0.1:9")
    primary, fallback = build_models("fast", Settings(_env_file=None, openai_api_key="x", anthropic_api_key="y"))
    assert str(primary.openai_api_base) == "https://api.openai.com/v1"
    assert fallback.anthropic_api_url == "https://api.anthropic.com"


class Verdict(BaseModel):
    on_topic: bool
    reason: str


def test_structured_output_falls_back_too():
    primary = ScriptedChatModel(script=[TimeoutError("slow")], provider="openai")
    fallback = ScriptedChatModel(script=[structured(Verdict(on_topic=True, reason="policy question"))])
    assert chat("fast", schema=Verdict, models=(primary, fallback)).invoke("q") == Verdict(
        on_topic=True, reason="policy question")


# -- ledger ------------------------------------------------------------------
@tool
def search_documents(query: str) -> str:
    """Search policy documents."""
    return "passage about employment"


def test_ledger_records_failed_attempt_then_fallback_with_usage_and_cost():
    events = []
    ledger = Ledger(on_record=lambda kind, rec: events.append(kind))
    primary = ScriptedChatModel(script=[RuntimeError("down")], model_name="gpt-6.1-sol", provider="openai")
    fallback = ScriptedChatModel(script=[reply("ok", input_tokens=1000, output_tokens=200, cache_read=400)],
                                 model_name="claude-sonnet-5-5", provider="anthropic")
    chat("quality", models=(primary, fallback)).invoke("hi", config={"callbacks": [ledger],
                                                                     "metadata": {"agent": "coordinator"}})
    failed, ok = ledger.calls
    assert (failed.ok, failed.provider, failed.model) == (False, "openai", "gpt-6.1-sol")
    assert "RuntimeError" in failed.error
    assert (ok.ok, ok.provider, ok.agent) == (True, "anthropic", "coordinator")
    assert (ok.usage.input_uncached, ok.usage.cache_read, ok.usage.output) == (600, 400, 200)
    assert ok.cost_usd == pytest.approx(cost_usd("claude-sonnet-5-5", ok.usage))
    assert ledger.totals()["failed_llm_calls"] == 1 and events == ["llm_call", "llm_call"]


def test_ledger_records_requested_tools_and_tool_runs():
    ledger = Ledger()
    model = ScriptedChatModel(script=[reply(tool_calls=[{"name": "search_documents", "args": {"query": "x"}}])])
    chat("fast", tools=[search_documents], models=(model, model)).invoke("q", config={"callbacks": [ledger]})
    search_documents.invoke({"query": "x"}, config={"callbacks": [ledger]})
    assert ledger.calls[0].tool_calls == ["search_documents"]
    assert (ledger.tools[0].name, ledger.tools[0].ok, ledger.tools[0].output_chars) == ("search_documents", True, 24)


def test_context_breakdown_separates_retrieved_passages_from_other_tool_output():
    counts = context_breakdown([
        SystemMessage("You are an analyst."),
        HumanMessage("Analyse tech employment"),
        ToolMessage("passage " * 50, tool_call_id="1", name="search_documents"),
        ToolMessage("42", tool_call_id="2", name="compute_stats"),
    ], tools=[{"name": "search_documents"}])
    assert counts["retrieved"] > counts["tool_results"] > 0
    assert counts["system"] > 0 and counts["user"] > 0 and counts["tool_schemas"] > 0
    assert counts["assistant"] == 0
