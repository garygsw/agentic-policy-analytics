"""Checkpoint storage for conversation threads (SQLite, one row per step of every thread)."""
import sqlite3
from pathlib import Path

from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from langgraph.checkpoint.sqlite import SqliteSaver

from app.graph import state

# Our own classes that may be rebuilt from stored checkpoints. Restoring stored data can run code, so only
# these (plus LangGraph's built-in safe types) are allowed.
STATE_TYPES = [state.StandaloneQuestion, state.TriageDecision, state.TriageResult, state.CoordinatorStep,
               state.Claim, state.Report, state.Review]


def serializer() -> JsonPlusSerializer:
    return JsonPlusSerializer(allowed_msgpack_modules=STATE_TYPES)


def sqlite_checkpointer(path: Path) -> SqliteSaver:
    path.parent.mkdir(parents=True, exist_ok=True)
    return SqliteSaver(sqlite3.connect(path, check_same_thread=False), serde=serializer())
