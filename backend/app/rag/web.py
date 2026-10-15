"""Web search through Tavily, over the whole web.

Results are third-party text: they are shown to the agents as source material marked untrusted, never as
instructions. The agents are told to prefer official sources and to say when a source is third-party.
"""
import re
from urllib.parse import parse_qsl, urlencode, urlsplit

import httpx

from app.config import get_settings

API = "https://api.tavily.com/search"


class WebSearchUnavailable(RuntimeError):
    pass


# Query parameters that do not change the page: page versions (government sites) and campaign tracking.
NON_CONTENT_PARAMS = re.compile(r"^(pageversion|utm_\w+)$", re.I)


def normalise_url(url: str) -> str:
    """One key per page: the same page is often returned as http/https, with/without www, in different letter
    case, or with a page-version or tracking parameter."""
    parts = urlsplit(url)
    query = urlencode([(k, v) for k, v in parse_qsl(parts.query) if not NON_CONTENT_PARAMS.match(k)])
    key = re.sub(r"^www\.", "", parts.netloc) + parts.path.rstrip("/") + (f"?{query}" if query else "")
    return key.lower()


class TavilySearch:
    def __init__(self, api_key: str | None = None, timeout_s: float = 30):
        key = api_key or (get_settings().tavily_api_key.get_secret_value() if get_settings().tavily_api_key else "")
        if not key:
            raise WebSearchUnavailable("web search is not configured (no TAVILY_API_KEY)")
        self.key, self.timeout_s = key, timeout_s

    def search(self, query: str, max_results: int = 5) -> list[dict]:
        body = {"query": query, "search_depth": "basic", "max_results": max_results * 2}
        try:
            response = httpx.post(API, headers={"Authorization": f"Bearer {self.key}"}, json=body,
                                  timeout=self.timeout_s)
            response.raise_for_status()
        except httpx.HTTPError as error:
            raise WebSearchUnavailable(f"web search failed: {type(error).__name__}") from error
        results, seen = [], set()
        for item in response.json().get("results", []):
            key = normalise_url(item.get("url", ""))
            if key and key not in seen:
                seen.add(key)
                results.append({"title": item.get("title", ""), "url": item["url"], "key": key,
                                "agency": key.split("/")[0],
                                "published": item.get("published_date") or "", "text": item.get("content", "")})
        return results[:max_results]
