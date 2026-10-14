"""Triage classifiers: the labelled set, the local classifier, Jev parsing, escalation and per-request choice."""
from collections import Counter

import httpx
import pytest
from langchain_core.runnables import RunnableLambda

from app.graph.state import TriageResult
from app.guardrails import triage
from tests.unit.test_rag import HashEmbedder


def test_labelled_set_is_balanced_enough_and_unique():
    rows = triage.load_labelled()
    counts = Counter(r["label"] for r in rows)
    assert set(counts) == set(triage.LABELS) and min(counts.values()) >= 40
    assert len({r["query"] for r in rows}) == len(rows)


def test_split_is_stratified_and_disjoint():
    train, test = triage.split(triage.load_labelled())
    assert not {r["query"] for r in train} & {r["query"] for r in test}
    train_counts, test_counts = Counter(r["label"] for r in train), Counter(r["label"] for r in test)
    assert all(abs(train_counts[label] - test_counts[label]) <= 1 for label in triage.LABELS)  # odd counts


def test_local_classifier_learns_the_categories():
    rows = [{"query": f"{word} statistics trend singapore {i}", "label": "in_scope"} for i, word in
            enumerate(["employment", "housing", "transport", "health", "population", "wages"])]
    rows += [{"query": f"write a poem joke recipe game {i}", "label": "off_topic"} for i in range(6)]
    rows += [{"query": f"tell me something please {i}", "label": "vague"} for i in range(6)]
    result = triage.LocalClassifier(HashEmbedder(), rows)("housing statistics trend singapore")
    assert (result.category, result.decided_by) == ("in_scope", "local") and 0 < result.confidence <= 1


def test_jev_answer_is_parsed_and_usage_counted(monkeypatch):
    def post(url, headers, json, timeout):
        assert json["questions"]["category"]["criteria"].keys() == triage.CRITERIA.keys()
        body = {"model": "jev-1.13.0", "usage": {"input_tokens": 300, "output_tokens": 20},
                "answers": {"category": {"type": "choice", "choice": "vague", "confidence": 0.81,
                                         "probabilities": {"in_scope": 0.1, "vague": 0.81, "off_topic": 0.09}}}}
        return httpx.Response(200, json=body, request=httpx.Request("POST", url))

    monkeypatch.setattr(httpx, "post", post)
    jev = triage.JevClassifier(api_key="test")
    result = jev("tell me something")
    assert (result.category, result.confidence, result.decided_by) == ("vague", 0.81, "jev")
    assert jev.usage.input_tokens == 300


def fixed(category, confidence, by="local"):
    return lambda q: TriageResult(category=category, reason="x", confidence=confidence, decided_by=by)


def test_escalation_passes_unsure_or_failed_decisions_to_the_llm():
    llm = fixed("in_scope", 0.99, "llm")
    assert triage.with_escalation(fixed("vague", 0.9), llm)("q").decided_by == "local"
    assert triage.with_escalation(fixed("vague", 0.4), llm)("q").decided_by == "llm"

    def broken(q):
        raise httpx.ConnectError("down")

    assert triage.with_escalation(broken, llm)("q").decided_by == "llm"


@pytest.mark.parametrize("choice,expected", [("local", "local"), ("jev", "jev"), ("llm", "llm")])
def test_the_classifier_is_chosen_per_request(monkeypatch, choice, expected):
    monkeypatch.setattr(triage, "production_local_classifier", lambda: fixed("in_scope", 0.95, "local"))
    monkeypatch.setattr(triage, "JevClassifier", lambda: fixed("in_scope", 0.95, "jev"))
    monkeypatch.setattr("app.graph.nodes.intake.llm_decider", lambda llm: fixed("in_scope", 0.99, "llm"))
    decide = triage.make_triage_decider(llm=None)
    result = RunnableLambda(lambda q: decide(q)).invoke("q", {"configurable": {"options": {"triage": choice}}})
    assert result.decided_by == expected
