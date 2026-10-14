"""LLM prices in USD per million tokens, used to estimate the cost of each call.

Taken from the providers' official pricing pages on 2026-10-14 (standard tier, short context):
  https://developers.openai.com/api/docs/pricing
  https://platform.claude.com/docs/en/about-claude/pricing
Prices change; update this table when they do. A model missing here is reported as unpriced, never guessed.
"""
from dataclasses import dataclass

from langchain_core.messages.ai import UsageMetadata

PRICES_AS_OF = "2026-10-14"


@dataclass(frozen=True)
class Price:
    input: float
    output: float
    cache_read: float
    cache_write: float  # OpenAI "cache writes"; Anthropic 5-minute cache write


PRICES: dict[str, Price] = {
    "gpt-6.1-sol": Price(input=2.00, output=10.00, cache_read=0.10, cache_write=2.50),
    "gpt-6-luna": Price(input=0.10, output=0.50, cache_read=0.01, cache_write=0.125),
    "claude-sonnet-5-5": Price(input=2.00, output=10.00, cache_read=0.10, cache_write=2.50),
    "claude-haiku-5-5": Price(input=0.10, output=0.50, cache_read=0.01, cache_write=0.125),
}
# Long prompts cost more (over 100k tokens for Claude Haiku 5.5; OpenAI's "long context" column).
# Our prompts stay well below that, so only short-context prices are listed.


@dataclass(frozen=True)
class TokenUsage:
    input_uncached: int = 0
    cache_read: int = 0
    cache_write: int = 0
    output: int = 0
    reasoning: int = 0  # part of output, reported separately when the provider gives it

    @classmethod
    def from_metadata(cls, usage: UsageMetadata | None) -> "TokenUsage":
        """LangChain reports input_tokens as the total, with cache reads/writes as subsets of it."""
        if not usage:
            return cls()
        details = usage.get("input_token_details") or {}
        cache_read = details.get("cache_read") or 0
        cache_write = details.get("cache_creation") or 0
        return cls(
            input_uncached=max(usage.get("input_tokens", 0) - cache_read - cache_write, 0),
            cache_read=cache_read,
            cache_write=cache_write,
            output=usage.get("output_tokens", 0),
            reasoning=(usage.get("output_token_details") or {}).get("reasoning") or 0,
        )

    @property
    def input_total(self) -> int:
        return self.input_uncached + self.cache_read + self.cache_write


def cost_usd(model: str, usage: TokenUsage) -> float | None:
    price = PRICES.get(model)
    if price is None:
        return None
    return (usage.input_uncached * price.input + usage.cache_read * price.cache_read
            + usage.cache_write * price.cache_write + usage.output * price.output) / 1_000_000
