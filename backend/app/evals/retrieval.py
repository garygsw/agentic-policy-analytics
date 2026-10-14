"""Retrieval evaluation: does search put the right document near the top?

    uv run python -m app.evals.retrieval build   # write the golden set (LLM calls; a few cents)
    uv run python -m app.evals.retrieval run     # score every search mode (local only, free)

Golden set: passages are sampled from the index (a fixed number per agency, plus catalogue entries), and a
language model writes, for each, a question that the passage answers, in its own words. The document the
passage came from is the relevant result. Search modes are then scored on those questions with recall@k and
mean reciprocal rank (MRR), at document level.

Known biases: generated questions can echo the passage's wording, which favours keyword search; and only the
source document counts as relevant, so another document that also answers the question scores as a miss. Both
make the absolute numbers conservative; the comparison between modes is the useful part.
"""
import argparse
import datetime as dt
import json
import random
import time

from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, Field

from app.config import REPO_ROOT
from app.evals.metrics import first_hit, mrr, percentile, recall_at
from app.guardrails.pii import screen
from app.llm.providers import chat
from app.rag.retriever import get_retriever
from app.rag.search import search

EVAL_DIR = REPO_ROOT / "backend" / "data" / "eval"
GOLDEN = EVAL_DIR / "retrieval_golden.jsonl"
RESULTS = EVAL_DIR / "retrieval_results.json"
AGENCIES = ["mom", "mof", "pmo", "moh", "ura"]
MODES = {"keyword (BM25)": ("keyword", False), "vector": ("vector", False), "hybrid (RRF)": ("hybrid", False),
         "hybrid + rerank": ("hybrid", True)}
K = 10

WRITE_QUESTION = """You build a test set for a search engine over Singapore government documents.
Given one passage, write ONE question that a policy researcher might type, which this passage answers.
- Use your own words: do not copy distinctive phrases from the passage. Keep it under 25 words.
- Ask about the substance (a policy, figure, programme or plan), not about the document itself.
- If the passage has no substantive content (navigation, contact details, boilerplate), set usable to false."""


class GoldenQuestion(BaseModel):
    question: str
    usable: bool = Field(description="False if the passage has nothing worth asking about")


def sample_passages(index, per_agency: int, datasets: int, seed: int = 7) -> list[dict]:
    """Passages of a reasonable length, at most one per document, a fixed number per agency."""
    rng = random.Random(seed)
    picked = []
    groups = [(a, per_agency, 600) for a in AGENCIES] + [("datagovsg", datasets, 150)]  # minimum characters
    for source, count, min_chars in groups:
        rows = index.db.execute(
            "select c.text, d.url, d.title, d.source from chunks c join documents d on d.id = c.document_id "
            "where d.source = ? and length(c.text) >= ? order by c.id", (source, min_chars)).fetchall()
        rng.shuffle(rows)
        seen = set()
        for row in rows:
            if row["url"] not in seen:
                seen.add(row["url"])
                picked.append(dict(row))
            if len(seen) == count * 2:  # spares, for passages the model finds unusable
                break
    return picked


def build(per_agency: int, datasets: int) -> None:
    retriever = get_retriever()
    writer = chat("fast", schema=GoldenQuestion)
    golden, counts = [], {}
    for passage in sample_passages(retriever.index, per_agency, datasets):
        target = datasets if passage["source"] == "datagovsg" else per_agency
        if counts.get(passage["source"], 0) >= target:
            continue
        reply = writer.invoke([SystemMessage(WRITE_QUESTION),
                               HumanMessage(f"Title: {passage['title']}\n\nPassage:\n{passage['text']}")])
        if not reply.usable or screen(reply.question):  # never keep personal data, even if generated
            continue
        counts[passage["source"]] = counts.get(passage["source"], 0) + 1
        golden.append({"id": f"r{len(golden) + 1:03d}", "question": reply.question.strip(),
                       "relevant_url": passage["url"], "source": passage["source"], "title": passage["title"]})
        print(f"{golden[-1]['id']} [{passage['source']}] {golden[-1]['question']}")
    GOLDEN.write_text("".join(json.dumps(g, ensure_ascii=False) + "\n" for g in golden))
    print(f"wrote {len(golden)} questions to {GOLDEN.relative_to(REPO_ROOT)}")


def ranked_urls(question: str, mode: str, rerank: bool, datasets: bool, retriever) -> list[str]:
    """Distinct documents in rank order (search returns passages, at most two per document)."""
    hits = search(question, index=retriever.index, embedder=retriever.embedder,
                  reranker=retriever.reranker if rerank else None, mode=mode, k=2 * K, datasets=datasets)
    return list(dict.fromkeys(h.url for h in hits))[:K]


def run() -> dict:
    retriever = get_retriever()
    golden = [json.loads(line) for line in GOLDEN.read_text().splitlines() if line.strip()]
    results = {"date": dt.date.today().isoformat(), "questions": len(golden), "k": K,
               "index": {k: v for k, v in retriever.index.stats().items() if k in ("documents", "chunks")},
               "modes": {}, "by_source": {}}
    for name, (mode, rerank) in MODES.items():
        ranks, latencies, per_source = [], [], {}
        for g in golden:
            started = time.perf_counter()
            urls = ranked_urls(g["question"], mode, rerank, g["source"] == "datagovsg", retriever)
            latencies.append((time.perf_counter() - started) * 1000)
            rank = first_hit(urls, g["relevant_url"])
            ranks.append(rank)
            per_source.setdefault(g["source"], []).append(rank)
        results["modes"][name] = {"recall@1": recall_at(ranks, 1), "recall@5": recall_at(ranks, 5),
                                  f"recall@{K}": recall_at(ranks, K), f"mrr@{K}": mrr(ranks, K),
                                  "median_ms": percentile(latencies, 50), "p95_ms": percentile(latencies, 95)}
        results["by_source"][name] = {s: round(recall_at(r, 5), 3) for s, r in per_source.items()}
    RESULTS.write_text(json.dumps(results, indent=1) + "\n")
    print(table(results))
    return results


def table(results: dict) -> str:
    lines = [f"| Mode | Recall@1 | Recall@5 | Recall@{K} | MRR@{K} | Median ms | p95 ms |",
             "|---|---|---|---|---|---|---|"]
    for name, m in results["modes"].items():
        lines.append(f"| {name} | {m['recall@1']:.0%} | {m['recall@5']:.0%} | {m[f'recall@{K}']:.0%} | "
                     f"{m[f'mrr@{K}']:.2f} | {m['median_ms']:.0f} | {m['p95_ms']:.0f} |")
    return "\n".join(lines)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Retrieval evaluation")
    parser.add_argument("command", choices=["build", "run"])
    parser.add_argument("--per-agency", type=int, default=8)
    parser.add_argument("--datasets", type=int, default=10)
    args = parser.parse_args()
    build(args.per_agency, args.datasets) if args.command == "build" else run()
