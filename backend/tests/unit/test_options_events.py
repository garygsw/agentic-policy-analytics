"""Per-request options (model, effort, tools) and the live trace events (levels, status, usage, context)."""
import pytest
from langchain_core.messages import HumanMessage
from langchain_core.runnables import RunnableLambda
from langgraph.checkpoint.memory import InMemorySaver
from pydantic import ValidationError

from app.agents.tools import analytics_tools, extraction_tools
from app.agents.workers import enabled
from app.cli import Display
from app.config import Settings
from app.graph.build import Deps, build_graph
from app.graph.nodes.intake import llm_decider
from app.graph.options import RunOptions, available_models, available_tools, current_options
from app.llm.fake import ScriptedChatModel, structured
from app.llm.ledger import Ledger
from app.llm.providers import ForcedFailureChatModel, build_models, chat
from tests.unit.test_analysis import FakeStore
from tests.unit.test_graph import GOOD, IN_SCOPE, PASS, QUESTION, FakeWorkers, steps

SETTINGS = Settings(_env_file=None, openai_api_key="x", anthropic_api_key="y")


def in_run(options: dict, fn):
    """Call fn inside a runnable whose config carries these options, as a graph run would."""
    return RunnableLambda(lambda _: fn()).invoke(None, {"configurable": {"options": options}})


def test_options_reject_unknown_models_and_tools():
    with pytest.raises(ValidationError, match="unknown model"):
        RunOptions(model="gpt-99")
    with pytest.raises(ValidationError, match="unknown tools"):
        RunOptions(tools=["teleport"])


def test_required_tools_cannot_be_switched_off():
    options = RunOptions(tools=["create_chart"])
    assert options.tool_enabled("compute_trend") and options.tool_enabled("create_chart")
    assert not options.tool_enabled("search_datasets")


def test_defaults_outside_a_run():
    assert current_options() == RunOptions()
    primary, fallback = build_models("quality", SETTINGS)
    assert (primary.model, primary.reasoning_effort) == ("claude-sonnet-5-5", "low")  # default effort is low
    assert (fallback.model_name, fallback.reasoning_effort) == ("gpt-6.1-sol", "low")


def test_effort_and_model_come_from_the_request():
    primary, fallback = in_run({"model": "claude-sonnet-5-5", "reasoning_effort": "high"},
                               lambda: build_models("quality", SETTINGS))
    assert (primary.model, primary.reasoning_effort) == ("claude-sonnet-5-5", "high")
    assert fallback.model_name == "gpt-6.1-sol"  # the other provider becomes the fallback
    fast, _ = in_run({"reasoning_effort": "high"}, lambda: build_models("fast", SETTINGS))
    assert fast.reasoning_effort == "low"  # triage and translation always stay fast


def test_forced_primary_failure_follows_the_chosen_model():
    s = Settings(_env_file=None, openai_api_key="x", anthropic_api_key="y", force_primary_fail=True)
    primary, _ = in_run({"model": "claude-haiku-5-5"}, lambda: build_models("quality", s))
    assert isinstance(primary, ForcedFailureChatModel) and primary.provider == "anthropic"


def test_disabled_tools_are_not_given_to_the_agents():
    from app.analysis.workspace import Workspace
    ws = Workspace(FakeStore())
    names = in_run({"tools": ["create_chart"]},
                   lambda: [t.name for t in enabled(extraction_tools(ws) + analytics_tools(ws))])
    assert "search_datasets" not in names and "compute_correlation" not in names
    assert {"load_dataset", "compute_trend", "create_chart"} <= set(names)


def test_option_lists_for_the_ui():
    models = {m["id"]: m for m in available_models()}
    assert models["claude-sonnet-5-5"]["fallback"] == "gpt-6.1-sol" and models["gpt-6-luna"]["context_window"]
    assert {t["name"] for t in available_tools() if t["required"]} >= {"load_dataset", "compute_trend"}


def test_run_emits_status_usage_and_context_events():
    script = {"TriageDecision": [structured(IN_SCOPE)], "CoordinatorStep": [structured(s) for s in steps(
        "extract", "analyse", "report")], "Report": [structured(GOOD)], "Review": [structured(PASS)]}

    def factory(tier, *, tools=None, schema=None):
        model = ScriptedChatModel(script=[script[schema.__name__].pop(0)], model_name="gpt-6.1-sol",
                                  provider="openai")
        return chat(tier, schema=schema, models=(model, model))

    graph = build_graph(Deps(llm=factory, workers=FakeWorkers(), decide=llm_decider(factory)),
                        checkpointer=InMemorySaver())
    config = {"configurable": {"thread_id": "e"}, "callbacks": [Ledger()]}
    events = list(graph.stream({"messages": [HumanMessage(QUESTION)]}, config, stream_mode="custom"))
    kinds = {e["kind"] for e in events}
    assert {"status", "action", "reasoning", "usage", "context"} <= kinds
    assert all(e["level"] in (1, 2, 3) and e["ts"] > 0 for e in events)
    statuses = [e["content"] for e in events if e["kind"] == "status"]
    assert "Checking every number against the computed facts" in statuses and "Writing the report" in statuses
    context = next(e for e in events if e["kind"] == "context" and e["node"] == "coordinator")
    assert context["window"] == 1_050_000 and context["messages"][0]["category"] == "system"
    usage = [e for e in events if e["kind"] == "usage"]
    assert [u["llm_calls"] for u in usage] == list(range(1, len(usage) + 1))  # running totals


def test_display_filters_by_verbosity_and_tracks_totals(capsys):
    display = Display("normal")
    display.show({"node": "x", "kind": "usage", "level": 1, "content": "", "ts": 0, "input_total": 1500,
                  "output": 500, "cost_usd": 0.0123})
    display.show({"node": "coordinator", "kind": "status", "level": 1, "content": "Planning", "ts": display.started})
    display.show({"node": "coordinator", "kind": "reasoning", "level": 2, "content": "hidden", "ts": 0})
    out = capsys.readouterr().out
    assert "2.0k tokens · $0.012] coordinator: Planning" in out and "hidden" not in out
