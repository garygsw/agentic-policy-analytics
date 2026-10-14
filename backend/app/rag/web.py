"""Web search through Tavily, restricted to Singapore government sites by default.

Results are third-party text: they are shown to the agents as source material marked untrusted, never as
instructions. Tavily's domain filter needs exact domains (wildcards such as *.gov.sg match nothing), so the
government scope is an explicit list.
"""
import re

import httpx

from app.config import get_settings

API = "https://api.tavily.com/search"
GOV_DOMAINS = ["gov.sg", "mom.gov.sg", "imda.gov.sg", "mof.gov.sg", "pmo.gov.sg", "moh.gov.sg", "ura.gov.sg",
               "hdb.gov.sg", "lta.gov.sg", "singstat.gov.sg", "mti.gov.sg", "mas.gov.sg", "edb.gov.sg", "mddi.gov.sg",
               "cpf.gov.sg", "msf.gov.sg", "moe.gov.sg", "mse.gov.sg", "mnd.gov.sg", "mot.gov.sg", "parliament.gov.sg"]


class WebSearchUnavailable(RuntimeError):
    pass


def normalise_url(url: str) -> str:
    """The same page is often returned as http/https and with/without www."""
    return re.sub(r"^https?://(www\.)?", "", url).rstrip("/").lower()


class TavilySearch:
    def __init__(self, api_key: str | None = None, timeout_s: float = 30):
        key = api_key or (get_settings().tavily_api_key.get_secret_value() if get_settings().tavily_api_key else "")
        if not key:
            raise WebSearchUnavailable("web search is not configured (no TAVILY_API_KEY)")
        self.key, self.timeout_s = key, timeout_s

    def search(self, query: str, scope: str = "gov", max_results: int = 5) -> list[dict]:
        body = {"query": query, "search_depth": "basic", "max_results": max_results * 2}
        if scope == "gov":
            body["include_domains"] = GOV_DOMAINS
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
                results.append({"title": item.get("title", ""), "url": item["url"], "agency": key.split("/")[0],
                                "published": item.get("published_date") or "", "text": item.get("content", "")})
        return results[:max_results]
