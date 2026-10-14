"""Compare the triage classifiers on held-out labelled questions: accuracy, latency and cost.

    uv run python scripts/triage_trial.py            # all classifiers (calls Jev and the LLM: about $0.01)
    uv run python scripts/triage_trial.py --no-remote # local classifier only

The local classifier is trained on one half of data/eval/triage_queries.jsonl and every classifier is scored
on the other half. Results go to data/eval/triage_results.json.
"""
import argparse
import json
import statistics

from sklearn.metrics import accuracy_score, classification_report, confusion_matrix, f1_score

from app.graph.nodes.intake import llm_decider
from app.guardrails.triage import (
    CONFIDENCE_THRESHOLD,
    LABELLED,
    LABELS,
    JevClassifier,
    LocalClassifier,
    load_labelled,
    split,
    timed,
)
from app.llm.ledger import Ledger
from app.llm.providers import chat
from app.rag.embed import get_embedder

RESULTS = LABELLED.with_name("triage_results.json")


def run(name: str, classify, questions: list[str], spent=lambda: 0.0) -> list[dict]:
    """Classify each question, recording latency and the cost of that one call (`spent` is a running total)."""
    rows = []
    for question in questions:
        before = spent()
        try:
            result, ms = timed(classify, question)
            rows.append({"category": result.category, "confidence": result.confidence, "ms": ms})
        except Exception as error:  # noqa: BLE001 - recorded as a miss, the run continues
            rows.append({"category": "error", "confidence": 0.0, "ms": 0.0, "error": str(error)[:120]})
        rows[-1]["cost"] = spent() - before
    print(f"  {name}: done")
    return rows


def escalate(first: list[dict], llm: list[dict], threshold: float) -> list[dict]:
    """The first classifier decides when confident; otherwise the LLM does, after it (time and cost add up)."""
    combined = []
    for a, b in zip(first, llm, strict=True):
        if a["confidence"] >= threshold and a["category"] != "error":
            combined.append({**a, "escalated": False})
        else:
            combined.append({**b, "ms": a["ms"] + b["ms"], "cost": a["cost"] + b["cost"], "escalated": True})
    return combined


def score(name: str, truth: list[str], rows: list[dict]) -> dict:
    predicted = [r["category"] for r in rows]
    latencies = sorted(r["ms"] for r in rows)
    report = classification_report(truth, predicted, labels=list(LABELS), output_dict=True, zero_division=0)
    return {
        "classifier": name,
        "accuracy": round(accuracy_score(truth, predicted), 3),
        "macro_f1": round(f1_score(truth, predicted, labels=list(LABELS), average="macro", zero_division=0), 3),
        "recall": {label: round(report[label]["recall"], 3) for label in LABELS},
        "latency_ms_median": round(statistics.median(latencies), 1),
        "latency_ms_p95": round(latencies[int(0.95 * (len(latencies) - 1))], 1),
        "cost_per_1000_usd": round(1000 * sum(r["cost"] for r in rows) / len(rows), 4),
        "escalated": sum(r.get("escalated", False) for r in rows),
        "errors": sum(r["category"] == "error" for r in rows),
        "confusion": confusion_matrix(truth, predicted, labels=list(LABELS)).tolist(),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--no-remote", action="store_true", help="skip Jev and the LLM")
    args = parser.parse_args()
    train, test = split(load_labelled())
    questions, truth = [r["query"] for r in test], [r["label"] for r in test]
    print(f"{len(train)} training and {len(test)} test questions")

    local = LocalClassifier(get_embedder(), train)
    local_rows = run("local", local, questions)
    results = [score("local", truth, local_rows)]

    if not args.no_remote:
        jev = JevClassifier()
        jev_rows = run("jev", jev, questions,
                       spent=lambda: jev.usage.input_tokens * JevClassifier.PRICE_PER_MILLION_INPUT / 1e6)
        results.append(score("jev", truth, jev_rows))

        ledger = Ledger()
        decide = llm_decider(lambda tier, **kw: chat(tier, **kw).with_config(callbacks=[ledger]))
        llm_rows = run("llm", decide, questions, spent=lambda: sum(c.cost_usd or 0 for c in ledger.calls))
        results.append(score("llm (gpt-6-luna)", truth, llm_rows))
        for name, first in (("local + llm", local_rows), ("jev + llm", jev_rows)):
            combined = escalate(first, llm_rows, CONFIDENCE_THRESHOLD)
            results.append(score(f"{name} (below {CONFIDENCE_THRESHOLD})", truth, combined))
        sweep = []  # how the escalation threshold trades accuracy against LLM calls (no extra API calls)
        for threshold in (0.4, 0.5, 0.6, 0.7, 0.8, 0.9):
            combined = escalate(local_rows, llm_rows, threshold)
            scored = score("local + llm", truth, combined)
            sweep.append({"threshold": threshold, "accuracy": scored["accuracy"], "macro_f1": scored["macro_f1"],
                          "escalated_share": round(scored["escalated"] / len(test), 3),
                          "cost_per_1000_usd": scored["cost_per_1000_usd"]})
        print("\nthreshold sweep (local, unsure cases to the LLM):")
        for row in sweep:
            print(f"  {row['threshold']:.1f}: accuracy {row['accuracy']:.0%}, {row['escalated_share']:.0%} escalated, "
                  f"${row['cost_per_1000_usd']:.3f} per 1,000")
        mistakes = {name: [{"query": q, "label": t, "predicted": r["category"], "confidence": round(r["confidence"], 2)}
                           for q, t, r in zip(questions, truth, rows, strict=True) if r["category"] != t]
                    for name, rows in (("local", local_rows), ("jev", jev_rows), ("llm", llm_rows))}

    output = {"train": len(train), "test": len(test), "threshold": CONFIDENCE_THRESHOLD, "results": results}
    if not args.no_remote:
        output |= {"threshold_sweep": sweep, "mistakes": mistakes}
    RESULTS.write_text(json.dumps(output, indent=1, ensure_ascii=False))
    print("\n| classifier | accuracy | macro F1 | recall in/off/vague | median ms | p95 ms | $ per 1,000 | escalated |")
    print("|---|---|---|---|---|---|---|---|")
    for r in results:
        rec = r["recall"]
        recall = f"{rec['in_scope']:.0%} / {rec['off_topic']:.0%} / {rec['vague']:.0%}"
        print(f"| {r['classifier']} | {r['accuracy']:.0%} | {r['macro_f1']:.2f} | {recall} | "
              f"{r['latency_ms_median']:.0f} | {r['latency_ms_p95']:.0f} | {r['cost_per_1000_usd']:.3f} | "
              f"{r['escalated']} |")
    print(f"\nsaved {RESULTS.relative_to(RESULTS.parents[2])}")


if __name__ == "__main__":
    main()
