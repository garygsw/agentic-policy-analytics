"""Standard evaluation metrics, implemented here so each one can be read and explained in a few lines."""
import statistics
from collections.abc import Iterable, Sequence

from app.quality.grounding import numbers_in, ungrounded


# -- retrieval ----------------------------------------------------------------------------------------------------
def first_hit(ranked: Sequence[str], relevant: str) -> int | None:
    """1-based rank of the relevant item in a ranked list, or None if it is absent."""
    return ranked.index(relevant) + 1 if relevant in ranked else None


def recall_at(ranks: Sequence[int | None], k: int) -> float:
    """Share of queries whose relevant item is in the top k. (One relevant item per query, so recall@k and
    hit rate@k are the same thing.)"""
    return sum(1 for r in ranks if r is not None and r <= k) / len(ranks) if ranks else 0.0


def mrr(ranks: Sequence[int | None], k: int = 10) -> float:
    """Mean reciprocal rank: 1 when the relevant item is first, 1/2 when second, ... and 0 beyond k."""
    return sum(1 / r for r in ranks if r is not None and r <= k) / len(ranks) if ranks else 0.0


def percentile(values: Sequence[float], p: float) -> float:
    """The p-th percentile (0-100), interpolated; 0 for no values."""
    if not values:
        return 0.0
    ordered = sorted(values)
    position = (len(ordered) - 1) * p / 100
    low, high = int(position), min(int(position) + 1, len(ordered) - 1)
    return ordered[low] + (ordered[high] - ordered[low]) * (position - low)


# -- answers ------------------------------------------------------------------------------------------------------
def groundedness(texts: Iterable[str], allowed: Iterable[float]) -> tuple[int, int]:
    """(numbers stated, numbers that match an allowed value) across the texts: a report's summary and findings,
    checked against the computed facts and the passages they cite."""
    allowed = list(allowed)
    stated = grounded = 0
    for text in texts:
        found = numbers_in(text)
        stated += len(found)
        grounded += len(found) - len(ungrounded(text, allowed))
    return stated, grounded


def citation_coverage(citations: Sequence[Sequence[str]], known_ids: set[str]) -> float:
    """Share of findings that cite at least one fact or source that exists."""
    if not citations:
        return 0.0
    return sum(1 for cited in citations if any(c in known_ids for c in cited)) / len(citations)


def jaccard(a: set, b: set) -> float:
    """Overlap of two sets: 1 when equal, 0 when disjoint (and 1 when both are empty)."""
    return len(a & b) / len(a | b) if a | b else 1.0


def mean_pairwise_jaccard(sets: Sequence[set]) -> float:
    """How alike repeated runs are: the average overlap over every pair of runs."""
    pairs = [jaccard(sets[i], sets[j]) for i in range(len(sets)) for j in range(i + 1, len(sets))]
    return statistics.fmean(pairs) if pairs else 1.0
