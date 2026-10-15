"""The API with the real graph, database and run manager; LLMs are scripted chat models (no network)."""
import time

import pytest
from fastapi.testclient import TestClient
from langgraph.checkpoint.memory import InMemorySaver

from app.api.db import make_session_factory
from app.api.main import create_app
from app.api.runs import draft_view
from app.graph.build import Deps, build_graph
from app.graph.checkpoint import serializer
from app.graph.nodes.intake import llm_decider
from app.graph.state import StandaloneQuestion, TriageDecision
from app.llm.fake import ScriptedChatModel, reply, structured
from app.llm.providers import chat
from app.rag.retriever import Retriever
from tests.unit.test_graph import GOOD, IN_SCOPE, PASS, QUESTION, FakeWorkers, steps
from tests.unit.test_rag import HashEmbedder, OverlapReranker, add, make_index


class Script:
    """Scripted replies per output schema, served through the real provider wiring (so calls are logged)."""

    def __init__(self, replies: dict[str, list]):
        self.replies = {k: list(v) for k, v in replies.items()}

    def __call__(self, tier, *, tools=None, schema=None):
        key = schema.__name__ if schema else "text"
        item = self.replies[key].pop(0)
        message = structured(item) if schema else reply(item)
        model = ScriptedChatModel(script=[message], model_name="gpt-6.1-sol", provider="openai")
        return chat(tier, schema=schema, models=(model, model))


def client_for(replies: dict, tmp_path, llm=None) -> TestClient:
    llm = llm or Script(replies)
    graph = build_graph(Deps(llm=llm, workers=FakeWorkers(), decide=llm_decider(llm)),
                        checkpointer=InMemorySaver(serde=serializer()))
    index = make_index(tmp_path)
    add(index, "https://mom.gov.sg/a", "Tech workforce", "Information and communications employment grew in 2024.")
    retriever = Retriever(index, HashEmbedder(), OverlapReranker())
    app = create_app(graph=graph, sessions=make_session_factory(f"sqlite:///{tmp_path / 'app.sqlite'}"),
                     retriever=retriever,
                     preload_models=False)
    return TestClient(app)


def wait(client: TestClient, run_id: str, timeout: float = 15) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        run = client.get(f"/api/runs/{run_id}").json()
        if run["status"] != "running":
            return run
        time.sleep(0.05)
    raise AssertionError(f"run {run_id} still running")


ANSWERED = {"TriageDecision": [IN_SCOPE], "CoordinatorStep": steps("extract", "analyse", "report"),
            "Report": [GOOD], "Review": [PASS]}


@pytest.fixture
def answered(tmp_path):
    with client_for(ANSWERED, tmp_path) as client:
        thread = client.post("/api/threads").json()["id"]
        run_id = client.post(f"/api/threads/{thread}/messages", json={"text": QUESTION}).json()["run_id"]
        yield client, thread, run_id, wait(client, run_id)


def test_health_and_options(tmp_path):
    with client_for({}, tmp_path) as client:
        assert client.get("/api/health").json() == {"status": "ok"}
        options = client.get("/api/options").json()
        assert [(m["id"], m["name"]) for m in options["models"]] == [("claude-sonnet-5-5", "Claude Sonnet 5.5"),
                                                                      ("gpt-6-luna", "GPT 6 Luna")]
        assert options["models"][0]["fallback_name"] == "GPT 6.1 Sol" and "web_scopes" not in options
        assert [v["name"] for v in options["verbosity"]] == ["normal", "thinking", "verbose"]
        assert options["defaults"]["reasoning_effort"] == "low"


def test_message_runs_in_the_background_to_an_answer(answered):
    client, thread, run_id, run = answered
    assert (run["status"], run["outcome"]) == ("completed", "answered")
    assert run["has_report"] and run["totals"]["llm_calls"] == 6
    assert [c["agent"] for c in run["llm_calls"]][:2] == ["triage", "coordinator"]  # the LLM call log is stored
    detail = client.get(f"/api/threads/{thread}").json()
    assert detail["title"] == QUESTION and [m["role"] for m in detail["messages"]] == ["user", "assistant"]


def test_events_are_numbered_and_end_with_the_outcome(answered):
    client, _, run_id, _ = answered
    events = client.get(f"/api/runs/{run_id}/events").json()
    assert [e["seq"] for e in events] == list(range(1, len(events) + 1))
    assert {"status", "action", "reasoning", "usage", "context"} <= {e["kind"] for e in events}
    assert events[-1]["kind"] == "end" and events[-1]["status"] == "completed"
    later = client.get(f"/api/runs/{run_id}/events", params={"after": len(events) - 2}).json()
    assert [e["seq"] for e in later] == [len(events) - 1, len(events)]


def test_websocket_replays_a_finished_run(answered):
    client, _, run_id, _ = answered
    expected = client.get(f"/api/runs/{run_id}/events").json()
    with client.websocket_connect(f"/api/runs/{run_id}/stream") as ws:
        received = [ws.receive_json() for _ in expected]
    assert [e["seq"] for e in received] == [e["seq"] for e in expected]


def test_reports_are_served_with_a_restrictive_policy(answered):
    client, _, run_id, _ = answered
    html = client.get(f"/api/runs/{run_id}/report.html")
    assert html.status_code == 200 and "Tech employment grew" in html.text
    assert "default-src 'none'" in html.headers["content-security-policy"]
    markdown = client.get(f"/api/runs/{run_id}/report.md")
    assert markdown.text.startswith("# Tech employment grew") and "attachment" in markdown.headers[
        "content-disposition"]


def test_personal_data_is_rejected_before_anything_runs(tmp_path):
    with client_for({}, tmp_path) as client:
        thread = client.post("/api/threads").json()["id"]
        response = client.post(f"/api/threads/{thread}/messages", json={"text": "我的身份证是 S1234567D，请分析"})
        assert response.status_code == 400
        detail = response.json()["detail"]
        assert detail["code"] == "personal_data" and "S1234567D" not in detail["message"]
        assert "个人信息" in detail["message"]  # in the user's language
        assert client.get(f"/api/threads/{thread}").json()["runs"] == []


def test_clarification_pauses_then_resumes(tmp_path):
    replies = {"TriageDecision": [TriageDecision(category="vague", reason="no topic"), IN_SCOPE],
               "text": ["Which area: employment or housing?"],
               "StandaloneQuestion": [StandaloneQuestion(question=QUESTION)], **{k: v for k, v in ANSWERED.items()
                                                                                 if k != "TriageDecision"}}
    with client_for(replies, tmp_path) as client:
        thread = client.post("/api/threads").json()["id"]
        run_id = client.post(f"/api/threads/{thread}/messages", json={"text": "Tell me something"}).json()["run_id"]
        paused = wait(client, run_id)
        assert paused["status"] == "awaiting_input"
        assert paused["pending"] == {"kind": "clarification", "question": "Which area: employment or housing?"}
        busy = client.post(f"/api/threads/{thread}/messages", json={"text": "another question"})
        assert busy.status_code == 409 and busy.json()["detail"]["run_id"] == run_id
        assert client.post(f"/api/runs/{run_id}/resume", json={"answer": "S1234567D"}).status_code == 400
        assert client.post(f"/api/runs/{run_id}/resume", json={"answer": "employment in tech"}).status_code == 202
        done = wait(client, run_id)
        assert (done["status"], done["outcome"]) == ("completed", "answered")
        events = client.get(f"/api/runs/{run_id}/events").json()
        assert [e["status"] for e in events if e["kind"] == "end"] == ["awaiting_input", "completed"]
        assert [e["content"] for e in events if e["kind"] == "reply"] == ["employment in tech"]  # replays show it


def test_a_paused_run_can_be_cancelled_and_the_conversation_goes_on(tmp_path):
    replies = {"TriageDecision": [TriageDecision(category="vague", reason="no topic"), *ANSWERED["TriageDecision"]],
               "text": ["Which area?"], "StandaloneQuestion": [StandaloneQuestion(question=QUESTION)],
               **{k: v for k, v in ANSWERED.items() if k != "TriageDecision"}}
    with client_for(replies, tmp_path) as client:
        thread = client.post("/api/threads").json()["id"]
        run_id = client.post(f"/api/threads/{thread}/messages", json={"text": "Tell me something"}).json()["run_id"]
        wait(client, run_id)
        assert client.post(f"/api/runs/{run_id}/cancel").json()["status"] == "cancelled"
        assert client.post(f"/api/runs/{run_id}/resume", json={"answer": "x"}).status_code == 409
        last = client.get(f"/api/runs/{run_id}/events").json()[-1]
        assert (last["kind"], last["status"]) == ("end", "cancelled")  # a replay shows the cancellation
        # The next message starts afresh, not inside the abandoned clarification.
        next_run = client.post(f"/api/threads/{thread}/messages", json={"text": QUESTION}).json()["run_id"]
        assert wait(client, next_run)["outcome"] == "answered"


def test_unknown_things_and_invalid_options(tmp_path):
    with client_for({}, tmp_path) as client:
        assert client.get("/api/runs/nope").status_code == 404
        assert client.post("/api/threads/nope/messages", json={"text": "q"}).status_code == 404
        thread = client.post("/api/threads").json()["id"]
        bad = client.post(f"/api/threads/{thread}/messages", json={"text": "q", "options": {"model": "gpt-99"}})
        assert bad.status_code == 422


def test_search_and_dataset_endpoints(tmp_path):
    with client_for({}, tmp_path) as client:
        hits = client.get("/api/search", params={"q": "information and communications employment"}).json()
        assert hits[0]["url"] == "https://mom.gov.sg/a" and "rerank" not in hits[0]["ranks"]  # off by default
        reranked = client.get("/api/search", params={"q": "information and communications employment", "rerank": True})
        assert "rerank" in reranked.json()[0]["ranks"]
        assert client.get("/api/search", params={"q": "x", "mode": "magic"}).status_code == 422
        datasets = client.get("/api/datasets").json()
        assert len(datasets) == 19 and next(d for d in datasets if d["key"] == "coe_bidding_results")["coverage"]


def test_streamed_report_json_becomes_readable_text():
    raw = ('{"title": "Tech jobs grew", "summary": "ICT employment rose 26.2%.", "findings": '
           '[{"text": "From 102,800 to 129,700.", "citations": ["F1"]}, {"text": "Fas')
    view = draft_view(raw)
    assert view["findings"] == ["From 102,800 to 129,700.", "Fas"]
    assert view["content"] == "# Tech jobs grew\n\nICT employment rose 26.2%.\n\n- From 102,800 to 129,700.\n- Fas"
    assert draft_view('{"ti')["content"] == "" and draft_view("")["content"] == ""

