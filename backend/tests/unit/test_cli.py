from app import cli


def test_a_clarifying_question_is_answered_by_number_or_in_words(monkeypatch):
    request = {"kind": "clarification", "question": "Which area?", "choices": ["Employment", "Housing"]}
    for typed, expected in [("2", "Housing"), ("3", "3"), ("transport since 2020", "transport since 2020")]:
        monkeypatch.setattr("builtins.input", lambda prompt, typed=typed: typed)
        assert cli.clarification_answer(request) == expected
    monkeypatch.setattr("builtins.input", lambda prompt: "1")
    assert cli.clarification_answer({"kind": "clarification", "question": "Which?"}) == "1"  # nothing offered
