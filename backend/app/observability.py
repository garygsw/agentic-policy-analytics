"""Logs and optional tracing, for running the service.

LOG_FORMAT=json writes one JSON object per line (ts in UTC, level, logger, message, run_id while a run is being
worked on, and any structured fields), which Loki/Grafana, Datadog, ELK or CloudWatch ingest as they are; text,
the default, is for reading in a terminal. Logs carry IDs, statuses, timings and costs, never questions or answers.

LangSmith tracing is optional: with LANGSMITH_TRACING=true and a key in .env, LangChain and LangGraph trace every
graph step and LLM call. That sends prompts and data to LangSmith.
"""
import contextlib
import datetime as dt
import json
import logging
import os
import sys
from contextvars import ContextVar

from app.config import Settings, get_settings

# The run being worked on. Set by the run worker; LangGraph carries context variables into the graph's steps, so
# a line logged anywhere during the run (an LLM call, a data source falling back) says which run it belongs to.
current_run: ContextVar[str | None] = ContextVar("current_run", default=None)

_make_record = logging.getLogRecordFactory()


def _record_with_run(*args, **kwargs) -> logging.LogRecord:
    record = _make_record(*args, **kwargs)
    record.run_id = current_run.get()
    return record


logging.setLogRecordFactory(_record_with_run)


@contextlib.contextmanager
def run_context(run_id: str):
    token = current_run.set(run_id)
    try:
        yield
    finally:
        current_run.reset(token)


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        line = {"ts": dt.datetime.fromtimestamp(record.created, dt.UTC).isoformat(timespec="milliseconds"),
                "level": record.levelname, "logger": record.name, "message": record.getMessage()}
        if getattr(record, "run_id", None):
            line["run_id"] = record.run_id
        line |= getattr(record, "data", None) or {}  # structured fields: log.info("...", extra={"data": {...}})
        if record.exc_info:
            line["exc"] = self.formatException(record.exc_info)
        return json.dumps(line, ensure_ascii=False, default=str)


class TextFormatter(logging.Formatter):
    def __init__(self):
        super().__init__("%(asctime)s %(levelname)s %(name)s%(run)s %(message)s%(fields)s")

    def format(self, record: logging.LogRecord) -> str:
        record.run = f" [run {record.run_id}]" if getattr(record, "run_id", None) else ""
        data = getattr(record, "data", None) or {}
        record.fields = "".join(f" {key}={value}" for key, value in data.items())
        return super().format(record)


def setup_logging(settings: Settings | None = None) -> None:
    """One handler on the root logger, in the chosen format; uvicorn's loggers write through it too."""
    settings = settings or get_settings()
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(JsonFormatter() if settings.log_format == "json" else TextFormatter())
    handler.set_name("app")  # replaced on a second call; other handlers (a test's log capture) are left alone
    root = logging.getLogger()
    root.handlers = [h for h in root.handlers if h.get_name() != "app"] + [handler]
    root.setLevel(settings.log_level)
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        logger = logging.getLogger(name)
        logger.handlers, logger.propagate = [], True
    # Quieter libraries: an HTTP line per provider request repeats the "LLM call" line, and the PDF export's font
    # subsetting writes dozens of lines per report.
    for noisy in ("httpx", "httpx2", "httpcore", "fontTools"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def enable_tracing(settings: Settings | None = None) -> bool:
    """LangSmith reads its settings from the environment. Docker Compose passes .env there already; a local run
    reads .env into the settings only, so they are copied across. Returns whether tracing is on."""
    settings = settings or get_settings()
    if not (settings.langsmith_tracing and settings.langsmith_api_key):
        return False
    os.environ["LANGSMITH_TRACING"] = "true"
    os.environ["LANGSMITH_API_KEY"] = settings.langsmith_api_key.get_secret_value()
    if settings.langsmith_project:
        os.environ["LANGSMITH_PROJECT"] = settings.langsmith_project
    if settings.langsmith_endpoint:
        os.environ["LANGSMITH_ENDPOINT"] = settings.langsmith_endpoint
    return True
