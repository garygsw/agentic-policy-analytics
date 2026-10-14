"""Fast triage decisions (in scope, off topic, vague) by three interchangeable classifiers.

- local: the document-search embedding model plus logistic regression, trained on the labelled questions in
  data/eval/triage_queries.jsonl. Free, offline, milliseconds.
- jev: TypeSafe AI's hosted decision model, asked a "choice" question.
- llm: the fast LLM tier with the triage prompt.

With local or jev, a decision below CONFIDENCE_THRESHOLD is passed to the LLM, which is slower but reads the
question more carefully; decided_by records who made the final call. scripts/triage_trial.py compares them.
"""
import json
import random
import time
from dataclasses import dataclass
from functools import lru_cache

import httpx
import numpy as np
from sklearn.linear_model import LogisticRegression

from app.config import get_settings
from app.data.sources import DATA_DIR
from app.graph.state import TriageResult

LABELS = ("in_scope", "off_topic", "vague")
LABELLED = DATA_DIR / "eval" / "triage_queries.jsonl"
CONFIDENCE_THRESHOLD = 0.6

# The same definitions as the LLM's triage prompt, phrased as Jev "criteria".
CRITERIA = {
    "in_scope": "About Singapore public policy or official statistics (economy, employment, housing, transport, "
                "health, population, education, digital economy) and specific enough to look up data",
    "vague": "On-topic but too unclear to act on: no identifiable topic, measure or question",
    "off_topic": "Not about Singapore public policy or statistics: general knowledge, coding, entertainment, "
                 "personal advice or eligibility, private individuals, or only other countries (comparisons "
                 "with Singapore are in scope)",
}


def load_labelled() -> list[dict]:
    return [json.loads(line) for line in LABELLED.read_text(encoding="utf-8").splitlines() if line.strip()]


def split(rows: list[dict], test_share: float = 0.5, seed: int = 7) -> tuple[list[dict], list[dict]]:
    """Stratified split, so each category is represented in both halves in the same proportion."""
    rng, train, test = random.Random(seed), [], []
    for label in LABELS:
        group = [r for r in rows if r["label"] == label]
        rng.shuffle(group)
        cut = round(len(group) * test_share)
        test += group[:cut]
        train += group[cut:]
    return train, test


class LocalClassifier:
    def __init__(self, embedder, rows: list[dict]):
        self.embedder = embedder
        vectors = np.array(embedder.embed([r["query"] for r in rows]))
        self.model = LogisticRegression(max_iter=2000, class_weight="balanced", C=4.0)
        self.model.fit(vectors, [r["label"] for r in rows])

    def predict_many(self, questions: list[str]) -> list[TriageResult]:
        probabilities = self.model.predict_proba(np.array(self.embedder.embed(questions)))
        results = []
        for row in probabilities:
            best = int(np.argmax(row))
            results.append(TriageResult(category=self.model.classes_[best], confidence=float(row[best]),
                                        reason="local classifier", decided_by="local"))
        return results

    def __call__(self, question: str) -> TriageResult:
        return self.predict_many([question])[0]


@dataclass
class JevUsage:
    input_tokens: int = 0
    output_tokens: int = 0


class JevClassifier:
    URL = "https://api.typesafe.ai/v1/systemone"
    PRICE_PER_MILLION_INPUT = 0.042  # vendor-listed; output tokens are free. Unverified against an invoice.

    def __init__(self, api_key: str | None = None, timeout_s: float = 20):
        settings = get_settings()
        self.key = api_key or (settings.typesafe_api_key.get_secret_value() if settings.typesafe_api_key else "")
        if not self.key:
            raise RuntimeError("Jev is not configured (no TYPESAFE_API_KEY)")
        self.timeout_s = timeout_s
        self.usage = JevUsage()

    def __call__(self, question: str) -> TriageResult:
        body = {"model": "jev-latest", "state": question,
                "questions": {"category": {"type": "choice", "instructions": "Classify this question for a "
                              "research assistant on Singapore public policy and official statistics.",
                              "criteria": CRITERIA}}}
        response = httpx.post(self.URL, headers={"Authorization": f"Bearer {self.key}"}, json=body,
                              timeout=self.timeout_s)
        response.raise_for_status()
        data = response.json()
        answer = data["answers"]["category"]
        usage = data.get("usage") or {}
        self.usage.input_tokens += usage.get("input_tokens", 0)
        self.usage.output_tokens += usage.get("output_tokens", 0)
        return TriageResult(category=answer["choice"], confidence=float(answer.get("confidence", 0.0)),
                            reason=f"Jev ({data.get('model', 'jev')})", decided_by="jev")


def with_escalation(first, fallback, threshold: float = CONFIDENCE_THRESHOLD):
    """Use the fast classifier, but pass low-confidence or failed decisions to the (LLM) fallback."""
    def decide(question: str) -> TriageResult:
        try:
            result = first(question)
        except Exception:  # noqa: BLE001 - a classifier outage must not stop triage
            return fallback(question)
        return result if result.confidence >= threshold else fallback(question)
    return decide


def timed(fn, *args) -> tuple[object, float]:
    started = time.perf_counter()
    result = fn(*args)
    return result, (time.perf_counter() - started) * 1000


@lru_cache(maxsize=1)
def production_local_classifier() -> LocalClassifier:
    """Trained once per process on every labelled question (the trial trains on half, to test on the rest)."""
    from app.rag.embed import get_embedder
    return LocalClassifier(get_embedder(), load_labelled())


def make_triage_decider(llm):
    """The triage decider the graph uses. The classifier is chosen per request (options.triage); local and Jev
    pass low-confidence or failed decisions to the fast LLM."""
    from app.graph.nodes.intake import llm_decider
    from app.graph.options import current_options

    ask_llm = llm_decider(llm)

    def decide(question: str) -> TriageResult:
        kind = current_options().triage
        if kind == "llm":
            return ask_llm(question)
        if kind == "jev":
            return with_escalation(JevClassifier(), ask_llm)(question)
        return with_escalation(production_local_classifier(), ask_llm)(question)
    return decide
