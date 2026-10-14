"""What the agents' search tools call: document passages, datasets from the catalogue, and the web.

Built lazily, so the models load (and the index opens) only when an agent first searches. Tests pass a
Retriever over a small index with a fake embedder instead.
"""
import re
from functools import lru_cache

from app.data import catalogue
from app.rag.embed import Embedder, Reranker, get_embedder, get_reranker
from app.rag.index import DocumentIndex
from app.rag.search import Passage, open_index, search


class Retriever:
    def __init__(self, index: DocumentIndex, embedder: Embedder, reranker: Reranker | None, web=None):
        self.index, self.embedder, self.reranker, self.web = index, embedder, reranker, web

    def documents(self, query: str, k: int = 6, source: str | None = None, since: str | None = None) -> list[Passage]:
        return search(query, index=self.index, embedder=self.embedder, reranker=self.reranker, k=k, source=source,
                      since=since)

    def datasets(self, query: str, k: int = 8) -> list[dict]:
        """Hybrid search over the indexed data.gov.sg catalogue; keyword search if it has not been indexed."""
        hits = search(query, index=self.index, embedder=self.embedder, reranker=self.reranker, k=k, datasets=True)
        if not hits:
            return catalogue.search(query, k)
        results = []
        for p in hits:
            dataset_id = re.search(r"(d_[0-9a-f]+)", p.url)
            if dataset_id:
                results.append({"dataset_id": dataset_id.group(1), "title": p.title, "agency": p.agency,
                                "last_updated": p.published})
        return results


@lru_cache(maxsize=1)
def get_retriever() -> Retriever:
    from app.rag.web import TavilySearch, WebSearchUnavailable
    try:
        web = TavilySearch()
    except WebSearchUnavailable:
        web = None
    return Retriever(open_index(), get_embedder(), get_reranker(), web)
