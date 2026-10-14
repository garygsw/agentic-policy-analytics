"""Real provider calls (costs a fraction of a cent). Run with: uv run pytest -m live"""
import pytest

from app.config import Settings
from app.llm.ledger import Ledger
from app.llm.providers import build_models, chat

pytestmark = pytest.mark.live


@pytest.mark.parametrize("tier", ["fast", "quality"])
def test_each_provider_answers(tier):
    for model in build_models(tier):
        ledger = Ledger()
        answer = model.invoke("Reply with the single word: ready", config={"callbacks": [ledger]})
        call = ledger.calls[0]
        print(f"\n{call.provider}:{call.model} -> {answer.content!r} {call.latency_ms}ms "
              f"in={call.usage.input_total} out={call.usage.output} cost=${call.cost_usd:.6f}")
        assert call.ok and "ready" in str(answer.content).lower() and call.cost_usd > 0


def test_forced_primary_failure_falls_back_to_anthropic():
    ledger = Ledger()
    models = build_models("fast", Settings(force_primary_fail=True))
    answer = chat("fast", models=models).invoke("Reply with the single word: ready", config={"callbacks": [ledger]})
    print("\n" + " -> ".join(f"{c.provider}:{c.model}({'ok' if c.ok else 'failed'})" for c in ledger.calls))
    assert [c.ok for c in ledger.calls] == [False, True] and ledger.calls[1].provider == "anthropic"
    assert "ready" in str(answer.content).lower()
