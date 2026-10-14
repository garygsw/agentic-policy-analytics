"""Local screen for personal identifiers.

Runs in the API before a message reaches the graph, so a rejected message is never sent to an LLM provider
and never written to a checkpoint. It blocks identifiers only. Names are allowed on purpose: policy questions
legitimately name ministers and MPs.

The patterns favour blocking: a false alarm costs the user a rephrase, while a miss sends personal data to a
third party. NRIC/FIN-shaped values are blocked whether or not their checksum is valid, so a typo does not
let a real number through.
"""
import re
from dataclasses import dataclass


@dataclass(frozen=True)
class Finding:
    kind: str
    start: int
    end: int


def _luhn_ok(digits: str) -> bool:
    total = 0
    for i, ch in enumerate(reversed(digits)):
        d = int(ch) * (2 if i % 2 else 1)
        total += d - 9 if d > 9 else d
    return total % 10 == 0


PATTERNS: list[tuple[str, re.Pattern]] = [
    ("NRIC/FIN number", re.compile(r"\b[STFGM]\d{7}[A-Z]\b", re.I)),
    ("email address", re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}\b")),
    # Singapore numbers: 8 digits starting 3/6/8/9, optional +65, optional space or hyphen in the middle.
    ("phone number", re.compile(r"(?<![\d$.,/])(?:\+65[\s-]?)?[3689]\d{3}[\s-]?\d{4}(?![\d,./%])")),
    ("street address unit number", re.compile(r"#\s?\d{1,3}\s?-\s?\d{1,5}\b")),
    # Account numbers only when the text says it is an account, since bare digit runs are common in statistics.
    ("bank account number", re.compile(r"(?i)\b(?:bank|account|acct|a/c)\b\D{0,20}?(\d[\d -]{6,16}\d)")),
]
CARD = re.compile(r"(?<!\d)(?:\d[ -]?){12,18}\d(?!\d)")


def screen(text: str) -> list[Finding]:
    """All personal identifiers found in `text`, in order of appearance."""
    findings = [Finding(kind, m.start(), m.end()) for kind, pattern in PATTERNS for m in pattern.finditer(text)]
    for m in CARD.finditer(text):
        digits = re.sub(r"\D", "", m.group())
        if 13 <= len(digits) <= 19 and _luhn_ok(digits):
            findings.append(Finding("payment card number", m.start(), m.end()))
    return sorted(findings, key=lambda f: f.start)


def rejection_message(findings: list[Finding]) -> str:
    kinds = list(dict.fromkeys(f.kind for f in findings))
    listed = kinds[0] if len(kinds) == 1 else ", ".join(kinds[:-1]) + " and " + kinds[-1]
    return (f"Your message was not sent because it appears to contain personal information ({listed}). "
            "Please remove it and ask again. Questions about policies, statistics and public figures are fine.")
