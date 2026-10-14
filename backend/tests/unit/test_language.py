import pytest

from app.guardrails.language import detect_language


@pytest.mark.parametrize("text,expected", [
    ("Analyse employment trends in the technology sector from 2020-2024", "en"),
    ("分析2020至2024年科技行业的就业趋势", "zh"),
    ("Analisis trend pekerjaan dalam sektor teknologi dari 2020 hingga 2024", "ms"),
    ("2020-2024 வரை தொழில்நுட்பத் துறையில் வேலைவாய்ப்பு போக்குகளை பகுப்பாய்வு செய்யவும்", "ta"),
    ("HDB resale price how ah, still going up or not?", "en"),
    ("Berapa harga COE sekarang?", "ms"),
])
def test_detects_official_languages_and_singlish(text, expected):
    assert detect_language(text) == expected


def test_short_latin_follow_up_keeps_the_conversation_language():
    assert detect_language("COE?", previous="zh") == "zh"
    assert detect_language("ok", previous="ta") == "ta"


def test_short_message_in_a_new_script_switches_language():
    assert detect_language("多少钱?", previous="en") == "zh"


def test_no_letters_falls_back_to_previous_or_english():
    assert detect_language("2020-2024?", previous="ms") == "ms"
    assert detect_language("123") == "en"
