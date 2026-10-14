"""Local language detection for Singapore's four official languages: English, Chinese, Malay and Tamil.

Runs before any LLM call. The detected language is kept in graph state, and answers are written in the
user's most recent language.
"""
from functools import lru_cache
from typing import Literal

from lingua import Language, LanguageDetectorBuilder

LangCode = Literal["en", "zh", "ms", "ta"]
SUPPORTED: dict[Language, LangCode] = {
    Language.ENGLISH: "en", Language.CHINESE: "zh", Language.MALAY: "ms", Language.TAMIL: "ta",
}
LANGUAGE_NAMES: dict[LangCode, str] = {"en": "English", "zh": "Chinese", "ms": "Malay", "ta": "Tamil"}
MIN_CONFIDENCE = 0.6
SHORT_MESSAGE_WORDS = 3


@lru_cache(maxsize=1)
def _detector():
    return LanguageDetectorBuilder.from_languages(*SUPPORTED).with_preloaded_language_models().build()


def detect_language(text: str, previous: LangCode | None = None) -> LangCode:
    """The language of `text`.

    Keeps the previous language when the text is too short or ambiguous to judge: a Chinese-speaking user
    typing "COE?" or "ok" as a follow-up should still get answers in Chinese.
    """
    fallback: LangCode = previous or "en"
    if sum(ch.isalpha() for ch in text) < 3:
        return fallback
    best = _detector().compute_language_confidence_values(text)[0]
    language = SUPPORTED[best.language]
    if best.value < MIN_CONFIDENCE:
        return fallback
    if previous and language in ("en", "ms") and len(text.split()) < SHORT_MESSAGE_WORDS:
        return previous  # a few Latin-script words (acronyms, "ok", "yes") say little about the language
    return language
