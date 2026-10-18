"""Logs a log platform can ingest (one JSON object per line, with the run's ID), and opt-in LangSmith tracing."""
import json
import logging
import os
import sys

from app.config import Settings
from app.observability import JsonFormatter, TextFormatter, enable_tracing, run_context, setup_logging


def settings(**values) -> Settings:
    return Settings(_env_file=None, openai_api_key="x", anthropic_api_key="y", **values)


def record(message="run completed", data=None, exc_info=None) -> logging.LogRecord:
    rec = logging.getLogger("app.runs").makeRecord("app.runs", logging.INFO, __file__, 1, message, (), exc_info)
    if data:
        rec.data = data
    return rec


def test_a_json_line_has_the_time_level_logger_message_and_run():
    outside = json.loads(JsonFormatter().format(record()))
    assert set(outside) == {"ts", "level", "logger", "message"} and outside["ts"].endswith("+00:00")
    with run_context("abc123"):
        inside = json.loads(JsonFormatter().format(record(data={"llm_calls": 6})))
    assert [inside[k] for k in ("run_id", "level", "logger", "llm_calls")] == ["abc123", "INFO", "app.runs", 6]
    try:
        raise ValueError("boom")
    except ValueError:
        failed = json.loads(JsonFormatter().format(record("run failed", exc_info=sys.exc_info())))
    assert "ValueError: boom" in failed["exc"]


def test_a_text_line_is_for_reading():
    with run_context("abc123"):
        line = TextFormatter().format(record(data={"outcome": "answered"}))
    assert line.endswith("INFO app.runs [run abc123] run completed outcome=answered")


def test_setup_picks_the_format_and_routes_uvicorn_through_it():
    root = logging.getLogger()
    handlers, level = root.handlers[:], root.level
    try:
        setup_logging(settings(log_format="json"))
        ours = [h for h in root.handlers if h.get_name() == "app"]
        assert len(ours) == 1 and isinstance(ours[0].formatter, JsonFormatter)
        assert logging.getLogger("uvicorn.access").propagate and not logging.getLogger("uvicorn.access").handlers
        setup_logging(settings())
        ours = [h for h in root.handlers if h.get_name() == "app"]
        assert len(ours) == 1 and isinstance(ours[0].formatter, TextFormatter)
    finally:
        root.handlers = handlers
        root.setLevel(level)


def test_langsmith_is_off_unless_switched_on_with_a_key(monkeypatch):
    for name in ("LANGSMITH_TRACING", "LANGSMITH_API_KEY", "LANGSMITH_PROJECT", "LANGSMITH_ENDPOINT",
                 "LANGSMITH_WORKSPACE_ID"):
        monkeypatch.setenv(name, "")  # recorded first, so whatever enable_tracing sets is undone afterwards
        monkeypatch.delenv(name)
    assert not enable_tracing(settings()) and not enable_tracing(settings(langsmith_tracing=True))  # no key
    assert enable_tracing(settings(langsmith_tracing=True, langsmith_api_key="ls-test", langsmith_project="policy",
                                   langsmith_workspace_id="ws-1"))
    assert [os.environ[f"LANGSMITH_{n}"] for n in ("TRACING", "PROJECT", "WORKSPACE_ID")] == ["true", "policy", "ws-1"]
