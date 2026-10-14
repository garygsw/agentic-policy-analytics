"""Numeric grounding: every number in generated text must match a number the system actually computed.

Used to check reports before they are shown, and to check that a translation kept every number intact.
"""
import re
from collections.abc import Iterable

# Unit words that scale the number before them, in the four supported languages.
MULTIPLIERS = {
    "thousand": 1e3, "k": 1e3, "million": 1e6, "mil": 1e6, "m": 1e6, "billion": 1e9, "bn": 1e9, "b": 1e9,
    "juta": 1e6, "bilion": 1e9, "ribu": 1e3,           # Malay
    "மில்லியன்": 1e6, "பில்லியன்": 1e9,                  # Tamil
    "千": 1e3, "万": 1e4, "百万": 1e6, "亿": 1e8,          # Chinese
}
_UNIT = "|".join(sorted((re.escape(u) for u in MULTIPLIERS), key=len, reverse=True))
# Boundaries are ASCII-only on purpose: Python's \w matches Chinese and Tamil letters, and those scripts put
# numbers directly against words ("增长了370万人"), which a \w boundary would never match.
NUMBER = re.compile(rf"(?<![A-Za-z0-9_.])(-?\d[\d,]*(?:\.\d+)?)\s*({_UNIT})?(?![A-Za-z0-9_])", re.I)
RELATIVE_TOLERANCE = 0.01


def numbers_in(text: str) -> list[float]:
    """Numeric values in text, with unit words applied ("3.7 million" -> 3700000).

    Small whole numbers (0-10) are skipped: in prose they are usually counts ("two sources", "Figure 1"),
    not data. Decimals and percentages of any size are kept.
    """
    values = []
    for match in NUMBER.finditer(text):
        raw, unit = match.group(1), (match.group(2) or "").lower()
        try:
            value = float(raw.replace(",", ""))
        except ValueError:
            continue
        if not unit and "." not in raw and 0 <= value <= 10 and not text[match.end():match.end() + 1] == "%":
            continue
        values.append(value * MULTIPLIERS.get(unit, 1))
    return values


def _close(a: float, b: float) -> bool:
    return a == b or abs(a - b) <= RELATIVE_TOLERANCE * max(abs(a), abs(b))


def ungrounded(text: str, allowed: Iterable[float]) -> list[float]:
    """Numbers in `text` that match none of the `allowed` values (within 1%)."""
    allowed = list(allowed)
    return [v for v in numbers_in(text) if not any(_close(v, a) for a in allowed)]


def numbers_preserved(original: str, translated: str) -> bool:
    """True if the translation contains every number of the original, and no new ones."""
    before, after = numbers_in(original), numbers_in(translated)
    return not ungrounded(translated, before) and not ungrounded(original, after)
