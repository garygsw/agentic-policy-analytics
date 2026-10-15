"""The API with the real graph, database and run manager; LLMs are scripted chat models (no network)."""
import threading
import time

import pytest
from fastapi.testclient import TestClient
from langgraph.checkpoint.memory import InMemorySaver
from sqlalchemy import func, select

from app.admin import deleted_threads, restore
from app.api.db import LLMCallRecord, Run, RunEvent, make_session_factory
from app.api.main import create_app, retitle_old_threads
from app.api.runs import RESTART_NOTE, draft_view
from app.api.titles import Title, short_question, write_title
from app.graph.build import Deps, build_graph
from app.graph.checkpoint import serializer
from app.graph.nodes.intake import llm_decider
from app.graph.state import StandaloneQuestion, TriageDecision
from app.llm.fake import ScriptedChatModel, reply, structured
from app.llm.providers import chat
from app.rag.retriever import Retriever
from tests.unit.test_graph import GOOD, IN_SCOPE, PASS, QUESTION, WHICH_AREA, FakeWorkers, steps
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


def client_for(replies: dict, tmp_path, llm=None, workers=None, store=None) -> TestClient:
    llm = llm or Script(replies)
    graph = build_graph(Deps(llm=llm, workers=workers or FakeWorkers(), decide=llm_decider(llm)),
                        checkpointer=InMemorySaver(serde=serializer()))
    index = make_index(tmp_path)
    add(index, "https://mom.gov.sg/a", "Tech workforce", "Information and communications employment grew in 2024.")
    retriever = Retriever(index, HashEmbedder(), OverlapReranker())
    app = create_app(graph=graph, sessions=make_session_factory(f"sqlite:///{tmp_path / 'app.sqlite'}"),
                     retriever=retriever, store=store, preload_models=False, llm=llm)
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
            "Report": [GOOD], "Review": [PASS], "Title": [Title(title="Tech employment trends")]}


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
    deadline = time.time() + 5  # the title is written in the background, alongside the run
    while (detail := client.get(f"/api/threads/{thread}").json())["title"] != "Tech employment trends":
        assert time.time() < deadline, detail["title"]
        time.sleep(0.05)  # written by the fast model, not the whole question
    assert [m["role"] for m in detail["messages"]] == ["user", "assistant"]


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
    pdf = client.get(f"/api/runs/{run_id}/report.pdf")
    assert pdf.headers["content-type"] == "application/pdf" and pdf.content.startswith(b"%PDF")
    assert "attachment" in pdf.headers["content-disposition"] and client.get(f"/api/runs/{run_id}").json()["has_pdf"]
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
               "Clarification": [WHICH_AREA],
               "StandaloneQuestion": [StandaloneQuestion(question=QUESTION)], **{k: v for k, v in ANSWERED.items()
                                                                                 if k != "TriageDecision"}}
    with client_for(replies, tmp_path) as client:
        thread = client.post("/api/threads").json()["id"]
        run_id = client.post(f"/api/threads/{thread}/messages", json={"text": "Tell me something"}).json()["run_id"]
        paused = wait(client, run_id)
        assert paused["status"] == "awaiting_input"
        assert paused["pending"] == {"kind": "clarification", "question": "Which area: employment or housing?",
                                     "choices": ["Employment", "Housing"]}
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
               "Clarification": [WHICH_AREA], "StandaloneQuestion": [StandaloneQuestion(question=QUESTION)],
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


def test_an_older_database_gains_the_new_columns(tmp_path):
    import sqlite3
    path = tmp_path / "old.sqlite"
    with sqlite3.connect(path) as old:  # tables from before the report downloads and pinning existed
        old.execute("create table runs (id varchar(32) primary key, thread_id varchar(32), question text, "
                    "status varchar(20))")
        old.execute("create table threads (id varchar(32) primary key, title varchar(200))")
        old.execute("insert into threads values ('t1', 'COE premiums')")
    make_session_factory(f"sqlite:///{path}")
    with sqlite3.connect(path) as db:
        columns = {row[1] for row in db.execute("pragma table_info(runs)")}
        thread = db.execute("select pinned, renamed from threads").fetchone()
    assert {"report_markdown", "report_pdf", "report_html", "totals"} <= columns
    assert thread == (0, 0)  # existing rows get the column's default


def test_conversations_can_be_renamed_pinned_and_deleted(answered):
    client, thread, run_id, _ = answered
    other = client.post("/api/threads").json()["id"]  # newer, so listed first until the older one is pinned

    def listed():
        return [(t["id"], t["title"], t["pinned"]) for t in client.get("/api/threads").json()]
    assert listed()[0][0] == other
    name = "My own name for the conversation about tech employment trends"  # longer than written titles
    assert client.patch(f"/api/threads/{thread}", json={"title": f"  {name} "}).json()["title"] == name
    assert client.patch(f"/api/threads/{thread}", json={"pinned": True}).json()["pinned"] is True
    assert listed()[0] == (thread, name, True)
    retitle_old_threads(client.app.state.sessions, Script({"Title": [Title(title="Other")]}))
    assert listed()[0][1] == name  # a name the user chose is never replaced by a written one
    assert client.patch(f"/api/threads/{thread}", json={"title": "  "}).status_code == 422
    assert client.patch(f"/api/threads/{thread}", json={"title": "Call S1234567D"}).status_code == 400
    assert client.patch("/api/threads/nope", json={"pinned": True}).status_code == 404

    assert client.delete(f"/api/threads/{thread}").json() == {"id": thread, "deleted": True}
    assert [t[0] for t in listed()] == [other]  # gone from the history list, search and every route
    assert client.get("/api/threads", params={"q": "employment"}).json() == []
    assert client.get(f"/api/threads/{thread}").status_code == 404
    assert client.get(f"/api/runs/{run_id}").status_code == 404
    assert client.post(f"/api/threads/{thread}/messages", json={"text": QUESTION}).status_code == 404
    assert client.delete(f"/api/threads/{thread}").status_code == 404

    # A soft delete: the trace, LLM call log and checkpoints are kept, and the backend can restore it.
    sessions = client.app.state.sessions
    with sessions() as session:
        for table in (RunEvent, LLMCallRecord):
            assert session.scalar(select(func.count()).select_from(table).where(table.run_id == run_id)) > 0
    assert client.app.state.graph.get_state({"configurable": {"thread_id": thread}}).values["messages"]
    assert [(t["id"], t["title"], t["runs"]) for t in deleted_threads(sessions)] == [(thread, name, 1)]
    assert restore(sessions, thread) and not restore(sessions, thread) and not restore(sessions, "nope")
    assert listed()[0] == (thread, name, True) and deleted_threads(sessions) == []
    assert client.get(f"/api/runs/{run_id}").json()["outcome"] == "answered"


def test_conversations_are_found_by_title_or_question(answered):
    client, thread, _, _ = answered
    client.patch(f"/api/threads/{thread}", json={"title": "Tech jobs"})
    client.post("/api/threads")  # an empty conversation matches nothing

    def found(q):
        return [t["id"] for t in client.get("/api/threads", params={"q": q}).json()]
    assert found("tech jobs") == [thread]  # the title, in any letter case
    assert found("SECTOR FROM 2020") == [thread]  # words of the question asked
    assert found("housing") == [] and found("%") == []  # % is a character to find, not a wildcard
    assert len(found("  ")) == 2  # a blank search lists everything


def test_a_conversation_with_a_running_analysis_is_not_deleted(tmp_path):
    with client_for({}, tmp_path) as client:
        thread = client.post("/api/threads").json()["id"]
        with client.app.state.sessions() as session:
            session.add(Run(id="r1", thread_id=thread, question="q", status="running"))
            session.commit()
        assert client.delete(f"/api/threads/{thread}").status_code == 409
        assert client.delete("/api/threads/nope").status_code == 404


def test_titles_fall_back_to_the_question_cut_at_a_word():
    question = "How has employment in the information and communications sector changed since 2019?"
    assert short_question(question) == "How has employment in the information and…"
    assert short_question("COE premiums") == "COE premiums"

    def failing(tier, schema=None):
        raise RuntimeError("provider down")
    assert write_title(question, failing) == short_question(question)


class Killed(BaseException):
    """Stands in for the server stopping: not an Exception, so nothing in the run catches it."""


class DyingWorkers(FakeWorkers):
    """Extraction is cut off the first `deaths` times it runs."""

    def __init__(self, deaths: int):
        super().__init__()
        self.deaths = deaths

    def extract(self, *args, **kwargs):
        if self.deaths:
            self.deaths -= 1
            self.calls.append("cut off")
            raise Killed()
        return super().extract(*args, **kwargs)


@pytest.mark.parametrize("deaths", [1, 2])
def test_a_run_cut_off_by_a_restart_continues_from_its_last_step(tmp_path, deaths):
    llm, workers = Script(ANSWERED), DyingWorkers(deaths)
    graph = build_graph(Deps(llm=llm, workers=workers, decide=llm_decider(llm)),
                        checkpointer=InMemorySaver(serde=serializer()))
    sessions = make_session_factory(f"sqlite:///{tmp_path / 'app.sqlite'}")

    def server() -> TestClient:  # a new API process on the same databases
        return TestClient(create_app(graph=graph, sessions=sessions, preload_models=False, llm=llm))

    def until_cut_off(client: TestClient, run_id: str) -> None:
        deadline = time.time() + 15
        while run_id in client.app.state.manager.tasks and time.time() < deadline:
            time.sleep(0.05)
        assert client.get(f"/api/runs/{run_id}").json()["status"] == "running"  # its worker is gone

    with server() as client:
        thread = client.post("/api/threads").json()["id"]
        run_id = client.post(f"/api/threads/{thread}/messages", json={"text": QUESTION}).json()["run_id"]
        until_cut_off(client, run_id)
    for _ in range(deaths - 1):  # continued after a restart, then cut off again
        with server() as client:
            until_cut_off(client, run_id)
    with server() as client:
        run = wait(client, run_id)
        events = [e for e in client.get(f"/api/runs/{run_id}/events").json() if e["node"] == "run"]
    if deaths == 1:  # continued from the step that was cut off; the steps before it did not run again
        assert (run["status"], run["outcome"]) == ("completed", "answered")
        assert workers.calls == ["cut off", "extract", "analyse"]
        assert [e["content"] for e in events if e.get("restart")] == [RESTART_NOTE]
    else:  # cut off again after continuing once: it ends instead of trying forever
        assert run["status"] == "failed" and workers.calls == ["cut off", "cut off"]
        assert events[-1]["content"].startswith("The server restarted while this analysis was running")


def test_stop_closes_a_run_whose_worker_is_gone(answered):
    client, _, run_id, _ = answered
    with client.app.state.sessions() as session:
        session.get(Run, run_id).status = "running"  # as if its worker had stopped without closing it
        session.commit()
    assert client.post(f"/api/runs/{run_id}/cancel").json()["status"] == "cancelled"
    assert client.get(f"/api/runs/{run_id}").json()["status"] == "cancelled"


class BlockingWorkers(FakeWorkers):
    """Extraction waits until the test lets it finish, so the run is still working when it is stopped."""

    def __init__(self):
        super().__init__()
        self.started, self.go_on = threading.Event(), threading.Event()

    def extract(self, *args, **kwargs):
        self.started.set()
        self.go_on.wait(10)
        return super().extract(*args, **kwargs)


def test_a_working_run_stops_after_its_current_step(tmp_path):
    workers = BlockingWorkers()
    with client_for(ANSWERED, tmp_path, workers=workers) as client:
        thread = client.post("/api/threads").json()["id"]
        run_id = client.post(f"/api/threads/{thread}/messages", json={"text": QUESTION}).json()["run_id"]
        assert workers.started.wait(10)
        assert client.post(f"/api/runs/{run_id}/cancel").json()["status"] == "cancelling"
        workers.go_on.set()
        run = wait(client, run_id)
        assert (run["status"], run["outcome"], workers.calls) == ("cancelled", None, ["extract"])
        assert client.get(f"/api/runs/{run_id}/events").json()[-1]["content"] == "Cancelled by the user"
        assert client.post(f"/api/runs/{run_id}/cancel").status_code == 409  # already over


def test_readiness_reports_each_dependency(tmp_path, monkeypatch):
    from app.config import Settings

    def settings(**keys):
        monkeypatch.setattr("app.api.main.get_settings", lambda: Settings(_env_file=None, **keys))
    with client_for({}, tmp_path) as client:
        settings(openai_api_key="k", anthropic_api_key="k")
        ready = client.get("/api/health/ready")
        assert ready.status_code == 200
        assert (ready.json()["database"], ready.json()["llm_keys"], ready.json()["web_search"]) == (
            "ok", "ok", "not configured")
        settings(openai_api_key="k", tavily_api_key="k")  # one provider key missing: not ready
        missing = client.get("/api/health/ready")
        assert missing.status_code == 503 and missing.json()["detail"]["llm_keys"] == "missing"


def test_a_dataset_profile_shows_its_quality_and_provenance(tmp_path):
    from app.config import Settings
    from app.data.store import DataStore
    offline = DataStore(Settings(_env_file=None, force_source_fail=True), database=f"sqlite:///{tmp_path / 'd.db'}")
    with client_for({}, tmp_path, store=offline) as client:
        profile = client.get("/api/datasets/ict_manpower").json()
        assert profile["quality"]["rows_out"] > 0 and profile["provenance"]["origin"] == "snapshot"
        assert "Live source unavailable" in profile["provenance"]["warning"]
        assert client.get("/api/datasets/not_a_dataset").status_code == 404

