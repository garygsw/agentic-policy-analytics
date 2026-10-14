"""Application settings, read from environment variables and the repo-root .env file."""
from functools import lru_cache
from pathlib import Path

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=REPO_ROOT / ".env", extra="ignore")

    openai_api_key: SecretStr | None = None
    anthropic_api_key: SecretStr | None = None
    # Provider endpoints are set here on purpose. The SDKs would otherwise honour ambient
    # OPENAI_BASE_URL / ANTHROPIC_BASE_URL shell variables and could silently send traffic elsewhere.
    llm_openai_base_url: str = "https://api.openai.com/v1"
    llm_anthropic_base_url: str = "https://api.anthropic.com"

    # Two tiers, each with a primary (OpenAI) and a fallback (Anthropic) model.
    # "quality" runs the agents' reasoning; "fast" runs triage, translation and other short calls.
    llm_quality_primary: str = "gpt-6.1-sol"
    llm_quality_fallback: str = "claude-sonnet-5-5"
    llm_fast_primary: str = "gpt-6-luna"
    llm_fast_fallback: str = "claude-haiku-5-5"
    llm_timeout_s: float = 60.0
    llm_max_retries: int = 1  # per provider, before falling back
    llm_max_output_tokens: int = 4096

    # Demo switch: make every primary call fail so the fallback is visible.
    force_primary_fail: bool = False


@lru_cache
def get_settings() -> Settings:
    return Settings()
