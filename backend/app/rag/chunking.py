"""Splits a document into passages for retrieval, with a strategy per document type.

- Every passage keeps a header (the title, and for parliamentary replies the question) that is embedded and
  searched with it, so a passage taken out of its page still says what it is about.
- Paragraphs are packed into passages of about MAX_TOKENS, overlapping by one paragraph so an idea split across
  a boundary is found from either side. A paragraph longer than MAX_TOKENS is split at sentence ends.
- Parliamentary replies: the question is repeated in every passage's header, because an answer paragraph rarely
  restates what was asked.
"""
import re
from dataclasses import dataclass

from app.llm.ledger import count_tokens

MAX_TOKENS = 350
QUESTION_CHARS = 400
SENTENCE_END = re.compile(r"(?<=[.!?])\s+")


@dataclass(frozen=True)
class Chunk:
    position: int
    header: str
    text: str
    tokens: int


def paragraphs(text: str) -> list[str]:
    return [p.strip() for p in text.split("\n") if p.strip()]


def _split_long(paragraph: str, max_tokens: int) -> list[str]:
    if count_tokens(paragraph) <= max_tokens:
        return [paragraph]
    pieces, current = [], ""
    for sentence in SENTENCE_END.split(paragraph):
        candidate = f"{current} {sentence}".strip()
        if current and count_tokens(candidate) > max_tokens:
            pieces.append(current)
            current = sentence
        else:
            current = candidate
    return pieces + ([current] if current else [])


def parliament_question(paras: list[str]) -> str:
    """The question of a parliamentary reply: the paragraph naming who asked, else the first paragraph."""
    asked = next((p for p in paras[:6] if re.search(r"\basked\b|\bto ask\b", p, re.I)), None)
    return (asked or (paras[0] if paras else ""))[:QUESTION_CHARS]


def chunk_document(title: str, text: str, kind: str, max_tokens: int = MAX_TOKENS) -> list[Chunk]:
    paras = [piece for p in paragraphs(text) for piece in _split_long(p, max_tokens)]
    if paras and paras[0].lower() == title.lower():
        paras = paras[1:]  # the page repeats its title as the first line
    header = title
    if kind == "parliament":
        header = f"{title}. Question: {parliament_question(paras)}"
    chunks, current = [], []
    for paragraph in paras:
        if current and count_tokens("\n".join([*current, paragraph])) > max_tokens:
            chunks.append(current)
            current = [current[-1]] if count_tokens(current[-1]) < max_tokens // 2 else []  # one-paragraph overlap
        current.append(paragraph)
    if current and (not chunks or current != chunks[-1][-1:]):
        chunks.append(current)
    return [Chunk(i, header, "\n".join(c), count_tokens("\n".join(c))) for i, c in enumerate(chunks)]
