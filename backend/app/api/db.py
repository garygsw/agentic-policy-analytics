"""The application database (SQLite via SQLAlchemy): conversations, runs, their trace events and LLM calls.

Conversation state itself (messages, plan, findings) lives in the LangGraph checkpointer; this database holds
what the API and UI need to list, replay and audit runs. Tables are created on start-up; a production deployment
would manage schema changes with Alembic migrations instead.
"""
import datetime as dt

from sqlalchemy import JSON, Float, ForeignKey, Integer, String, Text, UniqueConstraint, create_engine, event
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker

from app.config import REPO_ROOT

DB_PATH = REPO_ROOT / "var" / "app.sqlite"


def utcnow() -> dt.datetime:
    return dt.datetime.now(dt.UTC)


class Base(DeclarativeBase):
    pass


class Thread(Base):
    __tablename__ = "threads"
    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    title: Mapped[str] = mapped_column(String(200), default="")
    language: Mapped[str | None] = mapped_column(String(8))
    created_at: Mapped[dt.datetime] = mapped_column(default=utcnow)
    updated_at: Mapped[dt.datetime] = mapped_column(default=utcnow)


class Run(Base):
    """One user message and everything the agents did to answer it (including pauses for the user)."""
    __tablename__ = "runs"
    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    thread_id: Mapped[str] = mapped_column(ForeignKey("threads.id"), index=True)
    question: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20))   # running | awaiting_input | completed | failed | cancelled
    outcome: Mapped[str | None] = mapped_column(String(20))  # answered | partial | off_topic | declined | fallback | .
    options: Mapped[dict] = mapped_column(JSON, default=dict)
    pending: Mapped[dict | None] = mapped_column(JSON)       # the question the agents are waiting on
    answer: Mapped[str | None] = mapped_column(Text)
    report_html: Mapped[str | None] = mapped_column(Text)
    sources: Mapped[list | None] = mapped_column(JSON)
    totals: Mapped[dict | None] = mapped_column(JSON)
    error: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[dt.datetime] = mapped_column(default=utcnow)
    finished_at: Mapped[dt.datetime | None] = mapped_column()


class RunEvent(Base):
    """Every trace event of a run, numbered, so a client that connects late can replay it from the start."""
    __tablename__ = "run_events"
    __table_args__ = (UniqueConstraint("run_id", "seq"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id"), index=True)
    seq: Mapped[int] = mapped_column(Integer)
    kind: Mapped[str] = mapped_column(String(20))
    level: Mapped[int] = mapped_column(Integer)
    node: Mapped[str] = mapped_column(String(40))
    payload: Mapped[dict] = mapped_column(JSON)
    ts: Mapped[float] = mapped_column(Float)


class LLMCallRecord(Base):
    __tablename__ = "llm_calls"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id"), index=True)
    agent: Mapped[str | None] = mapped_column(String(40))
    provider: Mapped[str] = mapped_column(String(20))
    model: Mapped[str] = mapped_column(String(60))
    effort: Mapped[str | None] = mapped_column(String(10))
    ok: Mapped[bool] = mapped_column()
    error: Mapped[str | None] = mapped_column(Text)
    latency_ms: Mapped[int] = mapped_column(Integer)
    input_uncached: Mapped[int] = mapped_column(Integer)
    cache_read: Mapped[int] = mapped_column(Integer)
    cache_write: Mapped[int] = mapped_column(Integer)
    output: Mapped[int] = mapped_column(Integer)
    cost_usd: Mapped[float | None] = mapped_column(Float)
    tool_calls: Mapped[list] = mapped_column(JSON, default=list)


def make_session_factory(url: str | None = None) -> sessionmaker:
    if url is None:
        DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        url = f"sqlite:///{DB_PATH}"
    # A file database, one connection per thread. (A shared in-memory connection would let one thread's rollback
    # undo another thread's insert while a run is writing events.)
    engine = create_engine(url, connect_args={"check_same_thread": False})

    @event.listens_for(engine, "connect")
    def _pragmas(connection, _record):
        connection.execute("pragma journal_mode = wal")  # readers (WebSockets) never block the run writing
        connection.execute("pragma foreign_keys = on")

    Base.metadata.create_all(engine)
    return sessionmaker(engine, expire_on_commit=False)
