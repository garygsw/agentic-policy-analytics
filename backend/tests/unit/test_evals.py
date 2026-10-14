"""Evaluation metrics on small hand-checked examples."""
import pytest

from app.evals.metrics import (
    citation_coverage,
    first_hit,
    groundedness,
    jaccard,
    mean_pairwise_jaccard,
    mrr,
    percentile,
    recall_at,
)


def test_ranking_metrics():
    assert first_hit(["a", "b", "c"], "b") == 2 and first_hit(["a"], "z") is None
    ranks = [1, 3, None, 12]  # four queries: hit at 1, hit at 3, miss, hit beyond the cut-off
    assert recall_at(ranks, 1) == 0.25 and recall_at(ranks, 5) == 0.5 and recall_at(ranks, 20) == 0.75
    assert mrr(ranks, k=10) == pytest.approx((1 + 1 / 3) / 4)
    assert recall_at([], 5) == 0.0 and mrr([]) == 0.0


def test_percentile_interpolates():
    assert percentile([10, 20, 30, 40], 50) == 25 and percentile([5], 95) == 5 and percentile([], 50) == 0
    assert percentile(list(range(1, 101)), 95) == pytest.approx(95.05)


def test_groundedness_counts_numbers_that_match_facts():
    stated, grounded = groundedness(["Employment rose 26.2% to 129,700.", "It fell 6.8% in 2025."],
                                    allowed=[26.2, 129_700, 2025])
    assert (stated, grounded) == (4, 3)  # 6.8 matches no fact


def test_citation_coverage_needs_a_known_id():
    assert citation_coverage([["F1"], ["F9"], []], known_ids={"F1", "S1"}) == pytest.approx(1 / 3)
    assert citation_coverage([], {"F1"}) == 0.0


def test_consistency_is_mean_pairwise_overlap():
    assert jaccard({1, 2}, {2, 3}) == pytest.approx(1 / 3) and jaccard(set(), set()) == 1.0
    assert mean_pairwise_jaccard([{1, 2}, {1, 2}, {1, 3}]) == pytest.approx((1 + 1 / 3 + 1 / 3) / 3)
