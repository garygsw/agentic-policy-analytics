"""Hybrid search over the document index: BM25 keywords + vector similarity, merged by reciprocal rank fusion,
optionally reranked by a cross-encoder.

    uv run python -m app.rag.search "tech sector hiring outlook" --mode hybrid -k 5 [--rerank]

Keyword search finds exact terms (scheme names, acronyms, figures); vector search finds paraphrases. Reciprocal
rank fusion combines the two rank lists without having to compare their incompatible scores. The cross-encoder
reranker reads query and passage together; it is off by default because, on the retrieval evaluation, it ranked
worse than the fused lists and added half a second per search (docs/EVALUATION.md).
"""
import argparse
from dataclasses import dataclass, field
from typing import Literal

from app.rag.embed import EMBEDDING_DIM, EMBEDDING_MODEL, Embedder, Reranker, get_embedder, get_reranker
from app.rag.index import DocumentIndex

Mode = Literal["hybrid", "keyword", "vector"]
RRF_K = 60  # the usual constant: dampens the weight of top ranks so one list cannot dominate
MAX_PER_DOCUMENT = 2  # so a few long documents cannot fill every result slot


@dataclass
class Passage:
    chunk_id: int
    title: str
    agency: str
    source: str
    url: str
    published: str
    doc_type: str
    header: str
    text: str
    ranks: dict = field(default_factory=dict)  # keyword / vector rank, fused score and rerank score


def reciprocal_rank_fusion(*rankings: list[int]) -> list[tuple[int, float]]:
    scores: dict[int, float] = {}
    for ranking in rankings:
        for rank, chunk_id in enumerate(ranking, 1):
            scores[chunk_id] = scores.get(chunk_id, 0.0) + 1 / (RRF_K + rank)
    return sorted(scores.items(), key=lambda item: item[1], reverse=True)


def search(query: str, *, index: DocumentIndex, embedder: Embedder, reranker: Reranker | None = None,
           mode: Mode = "hybrid", k: int = 6, candidates: int = 30, source: str | None = None,
           since: str | None = None, datasets: bool = False) -> list[Passage]:
    keyword = index.keyword(query, candidates, source, since, datasets) if mode != "vector" else []
    vector = (index.nearest(embedder.embed_query(query), candidates, source, since, datasets)
              if mode != "keyword" else [])
    fused = reciprocal_rank_fusion(keyword, vector)[:candidates]
    rows = index.passages([chunk_id for chunk_id, _ in fused])
    passages = []
    for chunk_id, score in fused:
        row = rows[chunk_id]
        ranks = {"keyword": keyword.index(chunk_id) + 1 if chunk_id in keyword else None,
                 "vector": vector.index(chunk_id) + 1 if chunk_id in vector else None, "fused": round(score, 5)}
        passages.append(Passage(chunk_id, row["title"], row["agency"], row["source"], row["url"],
                                row["published"] or "", row["doc_type"], row["header"], row["text"], ranks))
    if reranker and passages:
        scores = reranker.rerank(query, [f"{p.header}\n{p.text}" for p in passages])
        for passage, score in zip(passages, scores, strict=True):
            passage.ranks["rerank"] = round(score, 3)
        passages.sort(key=lambda p: p.ranks["rerank"], reverse=True)
    results, per_document = [], {}
    for passage in passages:
        per_document[passage.url] = per_document.get(passage.url, 0) + 1
        if per_document[passage.url] <= MAX_PER_DOCUMENT:
            results.append(passage)
    return results[:k]


def open_index() -> DocumentIndex:
    return DocumentIndex(model=EMBEDDING_MODEL, dim=EMBEDDING_DIM)


def main() -> None:
    parser = argparse.ArgumentParser(description="Search the document index")
    parser.add_argument("query")
    parser.add_argument("--mode", choices=["hybrid", "keyword", "vector"], default="hybrid")
    parser.add_argument("-k", type=int, default=5)
    parser.add_argument("--rerank", action="store_true", help="rerank with the cross-encoder")
    parser.add_argument("--source", help="only this agency source (mom, mof, pmo, moh, ura)")
    parser.add_argument("--datasets", action="store_true", help="search the dataset catalogue instead")
    args = parser.parse_args()
    results = search(args.query, index=open_index(), embedder=get_embedder(),
                     reranker=get_reranker() if args.rerank else None, mode=args.mode, k=args.k,
                     source=args.source, datasets=args.datasets)
    for i, p in enumerate(results, 1):
        print(f"{i}. [{p.agency}, {p.published[:10]}] {p.title}\n   {p.url}\n   ranks {p.ranks}")
        print(f"   {p.text[:240]}...\n")


if __name__ == "__main__":
    main()
