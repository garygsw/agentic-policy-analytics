"""HTTP and WebSocket API for the agents.

    uv run uvicorn app.api.main:app --port 8000          # docs at http://localhost:8000/docs

Requests are stateless: a conversation's state is in the LangGraph checkpointer and its runs, events and LLM
calls are in the application database, so any request (or any API process) can continue any conversation.
"""
import asyncio
import threading
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, PlainTextResponse, Response
from pydantic import BaseModel, Field
from sqlalchemy import delete, func, select, text

from app.api.db import LLMCallRecord, Run, RunEvent, Thread, make_session_factory, utcnow
from app.api.runs import RunManager, as_dict, event_rows, iso, new_id
from app.api.titles import MAX_CHARS, short_question, write_title
from app.config import REPO_ROOT, get_settings
from app.data.registry import CURATED
from app.data.store import SNAPSHOT_DIR, DataStore, DataUnavailable
from app.graph.build import build_graph
from app.graph.checkpoint import sqlite_checkpointer
from app.graph.options import RunOptions, available_models, available_tools
from app.guardrails.language import detect_language
from app.guardrails.pii import rejection_message, screen
from app.llm.providers import chat

CHECKPOINTS = REPO_ROOT / "var" / "checkpoints.sqlite"
# The report page runs only its own inline script and plotly.js from cdnjs; it can load nothing else.
REPORT_CSP = ("default-src 'none'; script-src 'unsafe-inline' https://cdnjs.cloudflare.com; "
              "style-src 'unsafe-inline'; img-src data: blob:; font-src data:; connect-src 'none'; base-uri 'none'")
VERBOSITY = [{"level": 1, "name": "normal", "shows": "status lines, actions and errors"},
             {"level": 2, "name": "thinking", "shows": "plus reasoning and tool results"},
             {"level": 3, "name": "verbose", "shows": "plus model thinking and each agent's context"}]


class MessageIn(BaseModel):
    text: str = Field(min_length=1, max_length=4000)
    options: RunOptions = Field(default_factory=RunOptions)
    require_plan_approval: bool = False


class ThreadUpdate(BaseModel):
    title: str | None = Field(default=None, max_length=100)  # a new name chosen by the user
    pinned: bool | None = None


class ResumeIn(BaseModel):
    answer: str | None = Field(default=None, max_length=4000)  # reply to a clarifying question
    approved: bool | None = None                               # reply to a plan approval
    feedback: str | None = Field(default=None, max_length=2000)
    options: RunOptions = Field(default_factory=RunOptions)


def reject_personal_data(*texts: str | None) -> None:
    """The PII screen: runs before anything reaches the agents, a provider or a checkpoint."""
    for value in texts:
        findings = screen(value or "")
        if findings:
            raise HTTPException(400, {"code": "personal_data", "kinds": sorted({f.kind for f in findings}),
                                      "message": rejection_message(findings, detect_language(value or ""))})


def warm_up() -> None:
    """Load the local models in the background, so the first question does not wait for them."""
    from app.guardrails.triage import production_local_classifier
    from app.rag.embed import get_reranker
    production_local_classifier()
    if get_settings().rag_rerank:
        get_reranker()


def retitle_old_threads(sessions, llm) -> None:
    """Conversations from before titles were written carry their whole first question: give them a short title."""
    with sessions() as session:
        old = session.scalars(select(Thread).where(func.length(Thread.title) > MAX_CHARS + 1,
                                                   Thread.renamed.is_not(True)).limit(100)).all()
        for thread in old:
            thread.title = write_title(thread.title, llm)
        session.commit()


def create_app(graph=None, sessions=None, retriever=None, store: DataStore | None = None,
               preload_models: bool = True, llm=None) -> FastAPI:
    title_llm = llm or chat  # the model factory that writes conversation titles
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.sessions = sessions or make_session_factory()
        app.state.graph = graph or build_graph(checkpointer=sqlite_checkpointer(CHECKPOINTS))
        app.state.manager = RunManager(app.state.graph, app.state.sessions)
        app.state.background = set()  # title tasks, kept referenced until they finish
        if preload_models:
            threading.Thread(target=warm_up, daemon=True).start()
            threading.Thread(target=retitle_old_threads, args=(app.state.sessions, title_llm), daemon=True).start()
        yield

    app = FastAPI(title="Agentic Policy Analytics API", version="0.1.0", lifespan=lifespan)
    app.add_middleware(CORSMiddleware, allow_origins=get_settings().api_cors_origins, allow_methods=["*"],
                       allow_headers=["*"])

    def db():
        return app.state.sessions()

    def name_thread(thread_id: str, question: str) -> None:
        title = write_title(question, title_llm)
        with db() as session:
            thread = session.get(Thread, thread_id)
            if thread is not None and not thread.renamed:  # a name the user chose in the meantime stays
                thread.title = title
                session.commit()

    def get_run(session, run_id: str) -> Run:
        run = session.get(Run, run_id)
        if run is None:
            raise HTTPException(404, "unknown run")
        return run

    # -- health and options ------------------------------------------------------------------------------------
    @app.get("/api/health")
    def health():
        return {"status": "ok"}

    @app.get("/api/health/ready")
    def ready():
        settings = get_settings()
        checks = {"database": "ok", "llm_keys": "ok" if settings.openai_api_key and settings.anthropic_api_key
                  else "missing"}
        try:
            with db() as session:
                session.execute(text("select 1"))
        except Exception as error:  # noqa: BLE001
            checks["database"] = f"error: {type(error).__name__}"
        from app.rag.index import INDEX_PATH
        checks["document_index"] = "ok" if INDEX_PATH.exists() else "not built (run the ingest service)"
        checks["web_search"] = "configured" if settings.tavily_api_key else "not configured"
        healthy = checks["database"] == "ok" and checks["llm_keys"] == "ok"
        if not healthy:
            raise HTTPException(503, checks)
        return checks

    @app.get("/api/options")
    def options():
        return {"models": available_models(), "tools": available_tools(), "efforts": ["low", "medium", "high"],
                "triage": ["local", "jev", "llm"], "verbosity": VERBOSITY,
                "defaults": RunOptions().model_dump() | {"reasoning_effort": get_settings().llm_reasoning_effort,
                                                         "model": get_settings().llm_quality_primary}}

    # -- conversations ----------------------------------------------------------------------------------------
    @app.post("/api/threads", status_code=201)
    def create_thread():
        with db() as session:
            thread = Thread(id=new_id())
            session.add(thread)
            session.commit()
            return {"id": thread.id}

    @app.get("/api/threads")
    def list_threads():
        with db() as session:
            threads = session.scalars(select(Thread).order_by(Thread.pinned.desc(), Thread.updated_at.desc())
                                      .limit(100)).all()
            result = []
            for thread in threads:
                last = session.scalars(select(Run).where(Run.thread_id == thread.id)
                                       .order_by(Run.started_at.desc()).limit(1)).first()
                count = session.scalar(select(func.count()).select_from(Run).where(Run.thread_id == thread.id))
                result.append({"id": thread.id, "title": thread.title, "language": thread.language,
                               "pinned": bool(thread.pinned), "updated_at": iso(thread.updated_at), "runs": count,
                               "last_status": last.status if last else None})
            return result

    @app.get("/api/threads/{thread_id}")
    def get_thread(thread_id: str):
        with db() as session:
            thread = session.get(Thread, thread_id)
            if thread is None:
                raise HTTPException(404, "unknown conversation")
            runs = session.scalars(select(Run).where(Run.thread_id == thread_id).order_by(Run.started_at)).all()
        state = app.state.graph.get_state({"configurable": {"thread_id": thread_id}})
        messages = [{"role": "user" if m.type == "human" else "assistant", "content": m.content}
                    for m in (state.values.get("messages") or [])]
        return {"id": thread.id, "title": thread.title, "language": thread.language, "messages": messages,
                "runs": [{"id": r.id, "question": r.question, "status": r.status, "outcome": r.outcome,
                          "pending": r.pending, "totals": r.totals, "started_at": iso(r.started_at),
                          "finished_at": iso(r.finished_at)} for r in runs]}

    @app.patch("/api/threads/{thread_id}")
    def update_thread(thread_id: str, body: ThreadUpdate):
        """Rename or pin a conversation. Neither changes its place among the recent ones."""
        title = " ".join((body.title or "").split())
        if body.title is not None and not title:
            raise HTTPException(422, "a title cannot be empty")
        reject_personal_data(title)
        with db() as session:
            thread = session.get(Thread, thread_id)
            if thread is None:
                raise HTTPException(404, "unknown conversation")
            if title:
                thread.title, thread.renamed = title, True
            if body.pinned is not None:
                thread.pinned = body.pinned
            session.commit()
            return {"id": thread.id, "title": thread.title, "pinned": thread.pinned}

    @app.delete("/api/threads/{thread_id}")
    def delete_thread(thread_id: str):
        """Delete a conversation for good: its runs, their events and LLM call log, and its checkpoints."""
        with db() as session:
            thread = session.get(Thread, thread_id)
            if thread is None:
                raise HTTPException(404, "unknown conversation")
            if session.scalars(select(Run).where(Run.thread_id == thread_id, Run.status == "running")).first():
                raise HTTPException(409, {"code": "busy", "message": "Stop the running analysis first."})
            runs = select(Run.id).where(Run.thread_id == thread_id)
            session.execute(delete(LLMCallRecord).where(LLMCallRecord.run_id.in_(runs)))
            session.execute(delete(RunEvent).where(RunEvent.run_id.in_(runs)))
            session.execute(delete(Run).where(Run.thread_id == thread_id))
            session.delete(thread)
            session.commit()
        app.state.graph.checkpointer.delete_thread(thread_id)
        return {"id": thread_id, "deleted": True}

    @app.post("/api/threads/{thread_id}/messages", status_code=202)
    async def post_message(thread_id: str, body: MessageIn):
        reject_personal_data(body.text)
        with db() as session:
            thread = session.get(Thread, thread_id)
            if thread is None:
                raise HTTPException(404, "unknown conversation")
            busy = session.scalars(select(Run).where(Run.thread_id == thread_id,
                                                     Run.status.in_(["running", "awaiting_input"]))).first()
            if busy:
                raise HTTPException(409, {"code": "busy", "run_id": busy.id, "status": busy.status,
                                          "message": "Answer or cancel the current run first."})
            run = Run(id=new_id(), thread_id=thread_id, question=body.text, status="running",
                      options=body.options.model_dump())
            needs_title = not thread.title
            if needs_title:  # shown at once; replaced by a written title when the fast model replies
                thread.title = short_question(body.text)
            thread.updated_at = utcnow()
            session.add(run)
            session.commit()
            run_id = run.id
        manager: RunManager = app.state.manager
        manager.start(run_id, thread_id, manager.message_payload(body.text, body.require_plan_approval),
                      body.options.model_dump())
        if needs_title:
            task = asyncio.create_task(asyncio.to_thread(name_thread, thread_id, body.text))
            app.state.background.add(task)
            task.add_done_callback(app.state.background.discard)
        return {"run_id": run_id, "thread_id": thread_id}

    # -- runs ---------------------------------------------------------------------------------------------------
    @app.post("/api/runs/{run_id}/resume", status_code=202)
    async def resume(run_id: str, body: ResumeIn):
        reject_personal_data(body.answer, body.feedback)
        with db() as session:
            run = get_run(session, run_id)
            if run.status != "awaiting_input":
                raise HTTPException(409, "this run is not waiting for an answer")
            kind = (run.pending or {}).get("kind")
            if kind == "clarification" and not body.answer:
                raise HTTPException(422, "an answer is required")
            value = body.answer if kind == "clarification" else {"approved": bool(body.approved),
                                                                 "feedback": body.feedback or ""}
            run.status, run.pending = "running", None
            session.commit()
            thread_id = run.thread_id
        manager: RunManager = app.state.manager
        # The user's reply is part of the run's record, so a replay shows the question and its answer.
        reply = body.answer if kind == "clarification" else (
            "Plan approved" if body.approved else f"Plan rejected{': ' + body.feedback if body.feedback else ''}")
        manager.append(run_id, {"node": "user", "kind": "reply", "level": 1, "content": reply, "ts": time.time()})
        manager.start(run_id, thread_id, manager.resume_payload(value), body.options.model_dump())
        return {"run_id": run_id, "thread_id": thread_id}

    @app.post("/api/runs/{run_id}/cancel", status_code=202)
    def cancel(run_id: str):
        with db() as session:
            run = get_run(session, run_id)
            if run.status == "awaiting_input":  # nothing is executing: close the run directly
                run.status, run.finished_at = "cancelled", utcnow()
                session.commit()
                app.state.manager.append(run_id, {"node": "run", "kind": "end", "level": 1, "status": "cancelled",
                                                  "content": "Cancelled by the user", "ts": time.time()})
                return {"run_id": run_id, "status": "cancelled"}
            if run.status != "running":
                raise HTTPException(409, f"run is already {run.status}")
        app.state.manager.cancel(run_id)
        return {"run_id": run_id, "status": "cancelling"}

    @app.get("/api/runs/{run_id}")
    def run_detail(run_id: str):
        with db() as session:
            run = get_run(session, run_id)
            calls = session.scalars(select(LLMCallRecord).where(LLMCallRecord.run_id == run_id)
                                    .order_by(LLMCallRecord.id)).all()
            large = ("report_html", "report_markdown", "report_pdf")  # served by their own endpoints
            detail = {k: v for k, v in as_dict(run).items() if k not in large}
            return detail | {"started_at": iso(run.started_at), "finished_at": iso(run.finished_at),
                             "has_report": bool(run.report_html), "has_pdf": bool(run.report_pdf),
                             "llm_calls": [as_dict(c) for c in calls]}

    @app.get("/api/runs/{run_id}/events")
    def run_events(run_id: str, after: int = 0):
        with db() as session:
            get_run(session, run_id)
            return [{"seq": e.seq, **e.payload} for e in event_rows(session, run_id, after)]

    @app.websocket("/api/runs/{run_id}/stream")
    async def stream(ws: WebSocket, run_id: str, after: int = 0):
        """Replays the run's events after `after`, then follows it live until it finishes or pauses."""
        await ws.accept()
        manager: RunManager = app.state.manager
        last, last_kind = after, None
        try:
            while True:
                signal = manager.signal(run_id)
                signal.clear()
                with db() as session:
                    run = session.get(Run, run_id)
                    rows = event_rows(session, run_id, last) if run else []
                if run is None:
                    await ws.send_json({"kind": "error", "content": "unknown run"})
                    break
                for row in rows:
                    await ws.send_json({"seq": row.seq, **row.payload})
                    last, last_kind = row.seq, row.kind
                if not rows and last_kind == "end" and run.status != "running":
                    break
                try:
                    await asyncio.wait_for(signal.wait(), timeout=1.0)
                except TimeoutError:
                    pass
        except WebSocketDisconnect:
            return
        try:
            await ws.close()
        except RuntimeError:
            pass  # the browser closed it first

    @app.get("/api/runs/{run_id}/report.html", response_class=HTMLResponse)
    def report_html(run_id: str):
        with db() as session:
            run = get_run(session, run_id)
            if not run.report_html:
                raise HTTPException(404, "this run has no report")
            return HTMLResponse(run.report_html, headers={"Content-Security-Policy": REPORT_CSP})

    @app.get("/api/runs/{run_id}/report.md", response_class=PlainTextResponse)
    def report_markdown(run_id: str):
        """The report with its charts embedded as images (runs from before downloads existed: the answer text)."""
        with db() as session:
            run = get_run(session, run_id)
            if not (run.report_markdown or run.answer):
                raise HTTPException(404, "this run has no answer")
            return PlainTextResponse(run.report_markdown or run.answer, media_type="text/markdown", headers={
                "Content-Disposition": f'attachment; filename="report-{run_id}.md"'})

    @app.get("/api/runs/{run_id}/report.pdf")
    def report_pdf(run_id: str):
        with db() as session:
            run = get_run(session, run_id)
            if not run.report_pdf:
                raise HTTPException(404, "this run has no PDF")
            return Response(run.report_pdf, media_type="application/pdf", headers={
                "Content-Disposition": f'attachment; filename="report-{run_id}.pdf"'})

    # -- search and data ---------------------------------------------------------------------------------------
    @app.get("/api/search")
    def search_documents(q: str, mode: str = "hybrid", k: int = 5, source: str | None = None,
                         rerank: bool = False, datasets: bool = False):
        if mode not in ("hybrid", "keyword", "vector"):
            raise HTTPException(422, "mode must be hybrid, keyword or vector")
        from app.rag.embed import get_reranker
        from app.rag.retriever import get_retriever
        from app.rag.search import search
        r = retriever or get_retriever()
        reranker = (r.reranker or get_reranker()) if rerank else None  # loaded on request when off by default
        hits = search(q, index=r.index, embedder=r.embedder, reranker=reranker, mode=mode,
                      k=min(k, 20), source=source, datasets=datasets)
        return [{"title": h.title, "agency": h.agency, "url": h.url, "published": h.published,
                 "doc_type": h.doc_type, "text": h.text, "ranks": h.ranks} for h in hits]

    @app.get("/api/datasets")
    def datasets():
        import json
        rows = []
        for key, d in CURATED.items():
            meta_path = SNAPSHOT_DIR / f"{key}.json"
            quality = json.loads(meta_path.read_text())["quality"] if meta_path.is_file() else {}
            rows.append({"key": key, "title": d.title, "agency": d.agency, "topic": d.topic,
                         "frequency": d.frequency, "coverage": quality.get("coverage"), "caveats": d.notes})
        return rows

    @app.get("/api/datasets/{key}")
    def dataset(key: str):
        from app.analysis.workspace import profile
        try:
            loaded = (store or DataStore()).load(key)
        except DataUnavailable as error:
            raise HTTPException(404, str(error)) from error
        return profile(loaded, "S1") | {"quality": loaded.quality.model_dump(),
                                        "provenance": loaded.provenance.model_dump()}

    return app


app = create_app()
