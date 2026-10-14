"""Runs the agent graph in the background and records everything it emits.

Each run executes in a worker thread (the graph and its LLM calls are synchronous). Every event is stored with
a sequence number, so a WebSocket can replay a run from the start and then follow it live; waiting WebSockets
are woken through an in-process notification, and fall back to polling the database. Nothing about a run lives
only in memory: any API process can serve its history.
"""
import asyncio
import datetime as dt
import threading
import time
import uuid

from langchain_core.messages import HumanMessage
from langgraph.types import Command
from sqlalchemy import func, select
from sqlalchemy.orm import sessionmaker

from app.api.db import LLMCallRecord, Run, RunEvent, Thread, utcnow
from app.graph.events import LEVELS
from app.llm.ledger import Ledger

TERMINAL = {"completed", "failed", "cancelled"}
DRAFT_FLUSH_CHARS = 120  # report drafts are stored in pieces of about this size, not token by token


def new_id() -> str:
    return uuid.uuid4().hex[:12]


class RunManager:
    def __init__(self, graph, sessions: sessionmaker):
        self.graph, self.sessions = graph, sessions
        self.cancelled: set[str] = set()
        self.tasks: dict[str, asyncio.Task] = {}
        self._signals: dict[str, asyncio.Event] = {}
        self._loop: asyncio.AbstractEventLoop | None = None
        self._lock = threading.Lock()

    # -- starting and steering runs --------------------------------------------------------------------------
    def start(self, run_id: str, thread_id: str, payload, options: dict) -> None:
        """Begin (or resume) a run in the background; returns at once."""
        self._loop = asyncio.get_running_loop()
        task = asyncio.create_task(asyncio.to_thread(self._execute, run_id, thread_id, payload, options))
        self.tasks[run_id] = task
        task.add_done_callback(lambda _: self.tasks.pop(run_id, None))

    def message_payload(self, text: str, require_plan_approval: bool) -> dict:
        return {"messages": [HumanMessage(text)], "require_plan_approval": require_plan_approval}

    def resume_payload(self, value) -> Command:
        return Command(resume=value)

    def cancel(self, run_id: str) -> None:
        self.cancelled.add(run_id)

    # -- waiting for events (used by WebSockets) --------------------------------------------------------------
    def signal(self, run_id: str) -> asyncio.Event:
        return self._signals.setdefault(run_id, asyncio.Event())

    def _notify(self, run_id: str) -> None:
        if self._loop and not self._loop.is_closed():
            self._loop.call_soon_threadsafe(lambda: self.signal(run_id).set())

    # -- the worker thread ---------------------------------------------------------------------------------------
    def _execute(self, run_id: str, thread_id: str, payload, options: dict) -> None:
        with self.sessions() as db:
            seq = db.scalar(select(func.coalesce(func.max(RunEvent.seq), 0)).where(RunEvent.run_id == run_id))
        state = {"seq": seq, "draft": "", "draft_id": None}

        def save(event: dict) -> None:
            with self._lock, self.sessions() as db:
                state["seq"] += 1
                db.add(RunEvent(run_id=run_id, seq=state["seq"], kind=event["kind"],
                                level=event.get("level", LEVELS.get(event["kind"], 2)), node=event.get("node", ""),
                                payload=event, ts=event.get("ts", time.time())))
                db.commit()
            self._notify(run_id)

        def flush_draft(final: bool = False) -> None:
            if state["draft"] and (final or len(state["draft"]) >= DRAFT_FLUSH_CHARS):
                save({"node": "report_writer", "kind": "draft", "level": 1, "content": state["draft"],
                      "draft_id": state["draft_id"], "ts": time.time()})
                state["draft"] = ""

        def record_call(kind: str, record: dict) -> None:
            if kind != "llm_call":
                return
            usage = record["usage"]
            with self.sessions() as db:
                db.add(LLMCallRecord(run_id=run_id, agent=record["agent"], provider=record["provider"],
                                     model=record["model"], effort=record.get("effort"), ok=record["ok"],
                                     error=record["error"], latency_ms=record["latency_ms"],
                                     input_uncached=usage["input_uncached"], cache_read=usage["cache_read"],
                                     cache_write=usage["cache_write"], output=usage["output"],
                                     cost_usd=record["cost_usd"], tool_calls=record["tool_calls"]))
                db.commit()

        ledger = Ledger(on_record=record_call)
        config = {"configurable": {"thread_id": thread_id, "options": options}, "callbacks": [ledger]}
        try:
            for mode, chunk in self.graph.stream(payload, config, stream_mode=["custom", "messages"]):
                if run_id in self.cancelled:
                    flush_draft(final=True)
                    save({"node": "run", "kind": "end", "level": 1, "content": "Cancelled by the user",
                          "status": "cancelled", "ts": time.time()})
                    self._finish(run_id, status="cancelled", ledger=ledger)
                    return
                if mode == "custom":
                    flush_draft(final=True)
                    save(chunk)
                    continue
                message, metadata = chunk
                if metadata.get("langgraph_node") != "report_writer":
                    continue
                if message.id != state["draft_id"]:  # a new draft (first one, or a revision after the check)
                    flush_draft(final=True)
                    state["draft_id"] = message.id
                content = message.content
                state["draft"] += content if isinstance(content, str) else "".join(
                    b.get("text", "") for b in content if isinstance(b, dict))
                flush_draft()
            flush_draft(final=True)
            snapshot = self.graph.get_state(config)
            # The "end" event is stored before the run's status changes: a WebSocket stops following a run
            # only once it has sent an "end" event and the run is no longer running.
            if snapshot.interrupts:
                pending = snapshot.interrupts[0].value
                save({"node": "run", "kind": "end", "level": 1, "content": "Waiting for your answer",
                      "status": "awaiting_input", "pending": pending, "ts": time.time()})
                self._finish(run_id, status="awaiting_input", ledger=ledger, pending=pending)
            else:
                values = snapshot.values
                save({"node": "run", "kind": "end", "level": 1, "content": values.get("outcome", ""),
                      "status": "completed", "outcome": values.get("outcome"), "ts": time.time()})
                self._finish(run_id, status="completed", ledger=ledger, values=values)
        except Exception as error:  # noqa: BLE001 - the run fails visibly; the server keeps going
            save({"node": "run", "kind": "end", "level": 1, "content": f"Run failed: {type(error).__name__}",
                  "status": "failed", "ts": time.time()})
            self._finish(run_id, status="failed", ledger=ledger, error=f"{type(error).__name__}: {error}")
        finally:
            self.cancelled.discard(run_id)

    def _finish(self, run_id: str, *, status: str, ledger: Ledger, values: dict | None = None,
                pending: dict | None = None, error: str | None = None) -> None:
        with self.sessions() as db:
            run = db.get(Run, run_id)
            run.status, run.pending, run.error = status, pending, error
            run.totals = _merge_totals(run.totals, ledger.totals())
            if values:
                findings = values.get("findings") or {}
                run.outcome = values.get("outcome")
                run.answer = values.get("answer")
                run.report_html = values.get("report_html") or None
                run.sources = ((findings.get("analytics") or {}).get("sources")
                               or (findings.get("extraction") or {}).get("sources") or [])
                thread = db.get(Thread, run.thread_id)
                thread.language = values.get("language")
                thread.updated_at = utcnow()
            if status != "awaiting_input":
                run.finished_at = utcnow()
            db.commit()


def _merge_totals(previous: dict | None, current: dict) -> dict:
    """A run that paused and resumed has one ledger per part; its totals are the sum of both."""
    if not previous:
        return current
    merged = dict(current)
    for key, value in previous.items():
        if isinstance(value, int | float) and key in merged:
            merged[key] = round(merged[key] + value, 6)
        elif isinstance(value, list):
            merged[key] = sorted(set(merged.get(key, [])) | set(value))
    return merged


def event_rows(db, run_id: str, after: int) -> list[RunEvent]:
    return list(db.scalars(select(RunEvent).where(RunEvent.run_id == run_id, RunEvent.seq > after)
                           .order_by(RunEvent.seq)))


def as_dict(record) -> dict:
    return {c.key: getattr(record, c.key) for c in record.__table__.columns} if record else {}


def iso(value: dt.datetime | None) -> str | None:
    return value.isoformat() if value else None

