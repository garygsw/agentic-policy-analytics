"""Answer evaluation: run a fixed question set through the whole agent graph and score what comes out.

    uv run python -m app.evals.answers                 # every question, plus repeats for consistency (~$2)
    uv run python -m app.evals.answers --only coe      # one question
    uv run python -m app.evals.answers --repeats 0     # no consistency runs

Deterministic checks, per question:
- outcome: answered / partial / declined / off_topic / clarification, against the expected one
- time, LLM calls and cost
- groundedness: share of the numbers in the final report that match a computed fact or a passage the same
  finding cites (the report check enforces this, so it should be 100%), and how many drafts the check rejected
  first (what the check caught)
- citation coverage: share of findings and caveats that cite a fact or source that exists
- language: an answer to a question in Chinese comes back in Chinese

LLM-as-judge: a model from the *other* provider (to avoid a model grading its own work) scores each report from
1 to 5 on relevance, completeness, faithfulness to the facts, clarity and caveats, using only the facts and
sources the agents had, not outside knowledge.

Consistency: some questions run several times; the score is the average overlap (Jaccard) between runs of the
numbers their reports state.
"""
import argparse
import datetime as dt
import json
import statistics
import time
from concurrent.futures import ThreadPoolExecutor

from langchain_core.messages import HumanMessage, SystemMessage
from langgraph.checkpoint.memory import InMemorySaver
from pydantic import BaseModel, Field

from app.config import REPO_ROOT, get_settings
from app.evals.metrics import citation_coverage, groundedness, mean_pairwise_jaccard
from app.evals.retrieval import EVAL_DIR
from app.graph.build import build_graph
from app.graph.checkpoint import serializer
from app.graph.nodes.reporting import citable_text, facts_of, sources_of
from app.graph.options import FALLBACK_FOR, RunOptions
from app.llm.ledger import Ledger
from app.llm.providers import chat, make_model
from app.quality.grounding import fact_values, numbers_in

QUESTIONS = EVAL_DIR / "questions.jsonl"
RESULTS = EVAL_DIR / "answers_results.json"
MATERIAL = REPO_ROOT / "var" / "evals" / "answers_material.json"  # full reports and facts; not committed
CONSISTENCY = ["tech-employment", "hdb-towns"]
CRITERIA = ["relevance", "completeness", "faithfulness", "clarity", "caveats"]

JUDGE = """You review policy research reports written by an AI system from Singapore government data.
Score the report from 1 (poor) to 5 (excellent) on each criterion:
- relevance: it answers the question that was asked, not a different one.
- completeness: it covers every part of the question, as far as the facts allow.
- faithfulness: every claim is supported by the facts and sources listed (1 if it asserts what they do not show).
- clarity: it is clear, well organised and easy for a policy reader to use.
- caveats: it states the limitations that matter (coverage, definitions, missing data) without burying the answer.
Judge only from the material given. Do not use outside knowledge of the true figures. Add a one-sentence comment."""


class Judgement(BaseModel):
    relevance: int = Field(ge=1, le=5)
    completeness: int = Field(ge=1, le=5)
    faithfulness: int = Field(ge=1, le=5)
    clarity: int = Field(ge=1, le=5)
    caveats: int = Field(ge=1, le=5)
    comment: str


def judge_model() -> tuple:
    """The judge is the other provider's model at the same tier as the main model."""
    settings = get_settings()
    name = FALLBACK_FOR[settings.llm_quality_primary]
    model = make_model(name, "low", settings)
    return chat("quality", schema=Judgement, models=(model, model)), name


def report_numbers(values: dict) -> tuple[int, int]:
    """(numbers stated, numbers grounded) in the final report, checked the same way as the report check."""
    report, facts, sources = values["report"], facts_of(values), sources_of(values)
    allowed = fact_values(facts)
    allowed += numbers_in(" ".join(str(f.get("label", "")) for f in facts)) + numbers_in(values["question"])
    citable = citable_text(sources)

    def quoted(ids: list[str]) -> list[float]:
        return numbers_in(" ".join(citable[i] for i in ids if i in citable))

    everywhere = [c for f in report.findings for c in f.citations]
    stated, grounded = groundedness([report.summary], allowed + quoted(everywhere))
    for claim in [*report.findings, *report.caveats]:
        s, g = groundedness([claim.text], allowed + quoted(claim.citations))
        stated, grounded = stated + s, grounded + g
    return stated, grounded


def judge_material(values: dict) -> dict:
    """What the judge sees: exactly what the report writer had. That is every fact (with its method and sources)
    and every source with all its metadata (publisher, origin, retrieval date, licence, notes, quality report).
    Passages carry their text when the report cites them. Anything less makes correct statements look unsupported."""
    report = values["report"]
    cited = {c for claim in [*report.findings, *report.caveats] for c in claim.citations}
    sources = []
    for s in sources_of(values):
        entry = {k: v for k, v in s.items() if k not in ("text", "key") and v not in (None, "", [])}
        if s.get("kind") in ("document", "web") and s["id"] in cited:
            entry["text"] = s.get("text", "")[:1500]
        sources.append(entry)
    facts = [{k: f.get(k) for k in ("id", "label", "value", "unit", "sources", "method")} for f in facts_of(values)]
    return {"question": values["question"], "report": report.model_dump(), "facts": facts, "sources": sources}


def judge(judge_llm, material: dict) -> dict:
    content = (f"Question: {material['question']}\n\nFacts computed by the analysis:\n"
               f"{json.dumps(material['facts'], ensure_ascii=False)}\n\nSources (with caveats and cited passages):\n"
               f"{json.dumps(material['sources'], ensure_ascii=False)}\n\nReport:\n"
               f"{json.dumps(material['report'], ensure_ascii=False)}")
    try:
        verdict = judge_llm.invoke([SystemMessage(JUDGE), HumanMessage(content)])
        return verdict.model_dump()
    except Exception as error:  # noqa: BLE001 - a failed judgement is recorded, not fatal
        return {"error": f"{type(error).__name__}: {error}"}


def expected(item: dict) -> list[str]:
    """Acceptable outcomes: one, or several where the data may or may not cover every part of a question."""
    return item["expect"] if isinstance(item["expect"], list) else [item["expect"]]


def run_one(item: dict, run: int, judge_llm) -> dict:
    graph = build_graph(checkpointer=InMemorySaver(serde=serializer()))
    ledger = Ledger()
    config = {"configurable": {"thread_id": f"{item['id']}-{run}", "options": RunOptions().model_dump()},
              "callbacks": [ledger]}
    checks, objections, error, started = [], [], None, time.time()
    try:
        for event in graph.stream({"messages": [HumanMessage(item["question"])]}, config, stream_mode="custom"):
            if event.get("node") == "validate" and "passed" in event:
                checks.append(event["passed"])
                objections += event.get("issues", [])
    except Exception as exc:  # noqa: BLE001 - a crashed run is a result too
        error = f"{type(exc).__name__}: {exc}"
    snapshot = graph.get_state(config)
    values, totals = snapshot.values, ledger.totals()
    outcome = "clarification" if snapshot.interrupts else (values.get("outcome") or "error")
    result = {"id": item["id"], "run": run, "expect": item["expect"], "outcome": outcome,
              "outcome_ok": outcome in expected(item), "seconds": round(time.time() - started, 1),
              "llm_calls": totals["llm_calls"], "cost_usd": round(totals["cost_usd"], 4), "error": error,
              "drafts_checked": len(checks), "drafts_rejected": checks.count(False)}
    if item.get("language"):
        answer = values.get("answer", "")
        result["language_ok"] = values.get("language") == item["language"] and any(
            "一" <= ch <= "鿿" for ch in answer)
    if values.get("report") is not None and outcome in ("answered", "partial"):
        report = values["report"]
        stated, grounded = report_numbers(values)
        known = {f["id"] for f in facts_of(values)} | {s["id"] for s in sources_of(values)}
        result |= {"numbers_stated": stated, "numbers_grounded": grounded,
                   "citation_coverage": citation_coverage([c.citations for c in [*report.findings, *report.caveats]],
                                                          known),
                   "facts": len(facts_of(values)), "findings": len(report.findings),
                   "numbers": sorted({round(n, 1) for t in [report.summary, *(f.text for f in report.findings)]
                                      for n in numbers_in(t)})}
        result["material"] = judge_material(values)  # saved locally, so reports can be judged again
        if run == 0:
            result["judge"] = judge(judge_llm, result["material"])
    # Why a run ended as it did: node errors, and the report check's last objections.
    result["errors"] = values.get("errors") or []
    result["grounding_issues"] = values.get("grounding_issues") or []
    result["check_objections"] = objections  # every problem the report check raised, across all drafts
    return result


def summarise(runs: list[dict], judge_name: str) -> dict:
    first = [r for r in runs if r["run"] == 0]
    reports = [r for r in first if "numbers_stated" in r]
    judged = [r["judge"] for r in reports if r.get("judge") and "error" not in r["judge"]]
    stated = sum(r["numbers_stated"] for r in reports)
    consistency = {}
    for qid in CONSISTENCY:
        repeats = [r for r in runs if r["id"] == qid]
        if len(repeats) > 1:
            consistency[qid] = {
                "runs": len(repeats), "outcomes": [r["outcome"] for r in repeats],
                "number_overlap": round(mean_pairwise_jaccard([set(r.get("numbers", [])) for r in repeats]), 2)}
    return {
        "questions": len(first), "outcome_accuracy": sum(r["outcome_ok"] for r in first) / len(first),
        "reports": len(reports),
        "groundedness": sum(r["numbers_grounded"] for r in reports) / stated if stated else None,
        "drafts_rejected_by_check": f"{sum(r['drafts_rejected'] for r in first)} of "
                                    f"{sum(r['drafts_checked'] for r in first)}",
        "citation_coverage": statistics.fmean(r["citation_coverage"] for r in reports) if reports else None,
        "judge_model": judge_name,
        "judge_mean": {c: round(statistics.fmean(j[c] for j in judged), 2) for c in CRITERIA} if judged else {},
        "median_seconds_answered": statistics.median(r["seconds"] for r in reports) if reports else None,
        "cost_usd_total": round(sum(r["cost_usd"] for r in runs), 2),
        "consistency": consistency,
    }


def table(results: dict) -> str:
    lines = ["| Question | Expected | Outcome | Time | Calls | Cost | Grounded | Cited "
             "| Judge (rel/comp/faith/clar/cav) |", "|---|---|---|---|---|---|---|---|---|"]
    for r in (r for r in results["runs"] if r["run"] == 0):
        j = r.get("judge") or {}
        scores = "/".join(str(j.get(c, "-")) for c in CRITERIA) if "error" not in j else "error"
        grounded = f"{r['numbers_grounded']}/{r['numbers_stated']}" if "numbers_stated" in r else "-"
        cited = f"{r['citation_coverage']:.0%}" if "citation_coverage" in r else "-"
        mark = "" if r["outcome_ok"] else " ✗"
        lines.append(f"| {r['id']} | {' or '.join(expected(r))} | {r['outcome']}{mark} | {r['seconds']:.0f}s | "
                     f"{r['llm_calls']} | ${r['cost_usd']:.2f} | {grounded} | {cited} | {scores if j else '-'} |")
    return "\n".join(lines)


def rejudge() -> None:
    """Judge the reports saved by the last full run again (e.g. after changing the rubric)."""
    material = json.loads(MATERIAL.read_text())
    results = json.loads(RESULTS.read_text())
    judge_llm, judge_name = judge_model()
    for r in results["runs"]:
        saved = material.get(f"{r['id']}#{r['run']}")
        if r["run"] == 0 and saved:
            r["judge"] = judge(judge_llm, saved)
    results["summary"] = summarise(results["runs"], judge_name)
    RESULTS.write_text(json.dumps(results, indent=1, ensure_ascii=False) + "\n")
    print(table(results))
    print(json.dumps(results["summary"], indent=1))


def main() -> None:
    parser = argparse.ArgumentParser(description="Answer evaluation (calls real LLMs; costs money)")
    parser.add_argument("--only", help="comma-separated question ids (their runs replace the saved ones)")
    parser.add_argument("--repeats", type=int, default=2, help="extra runs of the consistency questions")
    parser.add_argument("--workers", type=int, default=3)
    parser.add_argument("--rejudge", action="store_true", help="score the saved reports again; no agent runs")
    args = parser.parse_args()
    if args.rejudge:
        return rejudge()
    items = [json.loads(line) for line in QUESTIONS.read_text().splitlines() if line.strip()]
    if args.only:
        items = [i for i in items if i["id"] in args.only.split(",")]
    jobs = [(item, 0) for item in items]
    jobs += [(item, n) for item in items if item["id"] in CONSISTENCY for n in range(1, args.repeats + 1)]
    judge_llm, judge_name = judge_model()
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        runs = list(pool.map(lambda job: run_one(job[0], job[1], judge_llm), jobs))
    for r in runs:
        print(f"{r['id']} #{r['run']}: {r['outcome']} in {r['seconds']}s, ${r['cost_usd']}")
    material = {f"{r['id']}#{r['run']}": r.pop("material", None) for r in runs}
    if args.only and RESULTS.is_file():  # merge into the saved results, replacing these questions' runs
        rerun = {r["id"] for r in runs}
        runs = [r for r in json.loads(RESULTS.read_text())["runs"] if r["id"] not in rerun] + runs
        order = [json.loads(line)["id"] for line in QUESTIONS.read_text().splitlines() if line.strip()]
        runs.sort(key=lambda r: (order.index(r["id"]) if r["id"] in order else 99, r["run"]))
        material = (json.loads(MATERIAL.read_text()) if MATERIAL.is_file() else {}) | material
    results = {"date": dt.date.today().isoformat(), "options": RunOptions().model_dump(),
               "model": get_settings().llm_quality_primary, "summary": summarise(runs, judge_name), "runs": runs}
    MATERIAL.parent.mkdir(parents=True, exist_ok=True)
    MATERIAL.write_text(json.dumps(material, ensure_ascii=False) + "\n")
    RESULTS.write_text(json.dumps(results, indent=1, ensure_ascii=False) + "\n")
    print(table(results))
    print(json.dumps(results["summary"], indent=1))


if __name__ == "__main__":
    main()
