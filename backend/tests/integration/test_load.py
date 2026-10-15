"""Concurrency: many runs at once through the real API, run manager, graph and SQLite. The language models are
scripted, so this measures the system's own overhead and checks that nothing is lost under load (no cost)."""
import time
from concurrent.futures import ThreadPoolExecutor

from langchain_core.runnables import RunnableLambda

from app.graph.state import CoordinatorStep
from tests.integration.test_api import QUESTION, client_for, wait
from tests.unit.test_graph import GOOD, IN_SCOPE, PASS

RUNS = 20


class ProgressAwareLLM:
    """Scripted replies that suit any interleaving of concurrent runs: the coordinator's next step follows from
    the progress shown in its own prompt, not from the order in which runs happen to ask."""

    def __call__(self, tier, *, tools=None, schema=None):
        def reply(messages):
            if schema is CoordinatorStep:
                progress = messages[0].content
                action = ("extract" if "extraction result" not in progress
                          else "analyse" if "analytics result" not in progress else "report")
                return CoordinatorStep(thought="next", action=action, instructions=f"please {action}")
            return {"TriageDecision": IN_SCOPE, "Report": GOOD, "Review": PASS}[schema.__name__]
        return RunnableLambda(reply)


def test_concurrent_runs_complete_with_every_event_in_order(tmp_path):
    with client_for({}, tmp_path, llm=ProgressAwareLLM()) as client:
        threads = [client.post("/api/threads").json()["id"] for _ in range(RUNS)]
        started = time.perf_counter()
        with ThreadPoolExecutor(max_workers=RUNS) as pool:  # all messages arrive at (nearly) the same moment
            run_ids = list(pool.map(lambda t: client.post(f"/api/threads/{t}/messages",
                                                          json={"text": QUESTION}).json()["run_id"], threads))
        finished = [wait(client, run_id, timeout=60) for run_id in run_ids]
        elapsed = time.perf_counter() - started
        assert all((r["status"], r["outcome"]) == ("completed", "answered") for r in finished)
        for run_id in run_ids:  # every run's events are complete and numbered without gaps
            events = client.get(f"/api/runs/{run_id}/events").json()
            assert [e["seq"] for e in events] == list(range(1, len(events) + 1))
            assert events[-1]["kind"] == "end" and events[-1]["status"] == "completed"
        with client.websocket_connect(f"/api/runs/{run_ids[-1]}/stream") as ws:  # replay still works
            assert ws.receive_json()["seq"] == 1
        print(f"\n{RUNS} concurrent runs in {elapsed:.1f}s ({elapsed / RUNS * 1000:.0f} ms per run on average)")
        assert elapsed < 30
