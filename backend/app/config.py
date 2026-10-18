"""Application settings, read from environment variables and the repo-root .env file."""
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=REPO_ROOT / ".env", extra="ignore")

    openai_api_key: SecretStr | None = None
    anthropic_api_key: SecretStr | None = None
    tavily_api_key: SecretStr | None = None      # web search tool (optional)
    firms_map_key: SecretStr | None = None       # NASA fire hotspots: only to rebuild their snapshot (optional)
    typesafe_api_key: SecretStr | None = None    # Jev classifier trial (optional)
    # Provider endpoints are set here on purpose. The SDKs would otherwise honour ambient
    # OPENAI_BASE_URL / ANTHROPIC_BASE_URL shell variables and could silently send traffic elsewhere.
    llm_openai_base_url: str = "https://api.openai.com/v1"
    llm_anthropic_base_url: str = "https://api.anthropic.com"

    # Two tiers, each with a primary and a fallback model from the other provider.
    # "quality" runs the agents' reasoning; "fast" runs triage, translation and other short calls.
    llm_quality_primary: str = "claude-sonnet-5-5"
    llm_quality_fallback: str = "gpt-6.1-sol"
    llm_fast_primary: str = "gpt-6-luna"
    llm_fast_fallback: str = "claude-haiku-5-5"
    # How hard the quality tier reasons. Users can override it per request; lower is faster and cheaper.
    llm_reasoning_effort: Literal["low", "medium", "high"] = "low"
    llm_timeout_s: float = 60.0
    llm_max_retries: int = 1  # per provider, before falling back
    llm_max_output_tokens: int = 4096

    # Demo switch: make every primary call fail so the fallback is visible.
    force_primary_fail: bool = False

    # Logs: text for a terminal, json for a log platform (one JSON object per line). See app/observability.py.
    log_format: Literal["text", "json"] = "text"
    log_level: str = "INFO"
    # Optional LangSmith tracing of every graph step and LLM call. It sends prompts and data to LangSmith.
    langsmith_tracing: bool = False
    langsmith_api_key: SecretStr | None = None
    langsmith_project: str | None = None
    langsmith_endpoint: str | None = None  # e.g. the EU endpoint; LangSmith's default otherwise
    langsmith_workspace_id: str | None = None  # only when the key can reach several workspaces

    # Data sources: cleaned data younger than this is reused without fetching again.
    data_max_age_hours: float = 24.0
    # Demo switch: treat every live data source as down, so cached data and snapshots are used.
    force_source_fail: bool = False

    # Browser origins allowed to call the API (the web UI in development and in Docker Compose).
    api_cors_origins: list[str] = ["http://localhost:3000"]

    # Document ingestion: when the ingest service runs (cron syntax, Singapore time) and how far back it looks.
    ingest_cron: str = "0 3 * * *"
    ingest_since: str = "2024-01-01"
    ingest_limit_per_source: int = 500

    # Cross-encoder reranking of search results. Off: on the retrieval evaluation it ranked worse than hybrid
    # search alone and added about half a second per search (see TESTING.md).
    rag_rerank: bool = False

    # The claim checker: the reviewer also checks each statement against what it cites (D-035). On by default;
    # off makes runs cheaper and faster (about a third less per evaluation run) at the cost of that check.
    claim_check: bool = True


@lru_cache
def get_settings() -> Settings:
    return Settings()
