"""HTTP for data sources: an identifying User-Agent, timeouts, and retries with exponential backoff.

data.gov.sg rate-limits anonymous use (HTTP 429) without advertising its limits, so 429 and 5xx responses are
retried after 10 s, 20 s, 40 s (or the server's Retry-After, when given).
"""
import time
from collections.abc import Callable

import httpx

USER_AGENT = "policy-analytics/0.1 (+https://github.com/garygsw/agentic-policy-analytics)"
RETRY_STATUS = {429, 500, 502, 503, 504}


class SourceUnavailable(RuntimeError):
    """A source could not be reached or refused the request after all retries."""


def fetch(url: str, *, params: dict | None = None, attempts: int = 4, backoff_s: float = 10.0,
          timeout_s: float = 60.0, sleep: Callable[[float], None] = time.sleep) -> httpx.Response:
    problem = ""
    for attempt in range(attempts):
        wait = backoff_s * 2 ** attempt
        try:
            response = httpx.get(url, params=params, headers={"User-Agent": USER_AGENT}, timeout=timeout_s,
                                 follow_redirects=True)
        except httpx.HTTPError as error:
            problem = f"{type(error).__name__}: {error}"
        else:
            if response.status_code < 400:
                return response
            problem = f"HTTP {response.status_code}"
            if response.status_code not in RETRY_STATUS:
                break
            retry_after = response.headers.get("Retry-After", "")
            wait = float(retry_after) if retry_after.isdigit() else wait
        if attempt < attempts - 1:
            sleep(wait)
    raise SourceUnavailable(f"{url} failed: {problem}")
