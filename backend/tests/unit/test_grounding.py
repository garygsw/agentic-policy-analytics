from app.quality.grounding import numbers_in, numbers_preserved, ungrounded


def test_numbers_apply_units_and_skip_small_counts():
    assert numbers_in("3.7 million and 12.5% in 2024, see Figure 1 and two sources") == [3_700_000, 12.5, 2024]
    assert numbers_in("up 5%") == [5.0]
    assert numbers_in("rose to 3,950,000 persons") == [3_950_000]


def test_numbers_inside_chinese_and_malay_text():
    assert numbers_in("就业人数增长了370万人，增幅为12.5%") == [3_700_000, 12.5]
    assert numbers_in("meningkat kepada 3.7 juta orang") == [3_700_000]


def test_citation_markers_and_ranges_are_not_misread():
    assert numbers_in("[F1, S2] from 2020-2024") == [2020, 2024]


def test_ungrounded_allows_one_percent_rounding():
    assert ungrounded("rose to 125,000 (up 25%), about 3.7 million in total", [125_000, 25.0, 3_712_345]) == []
    assert ungrounded("rose to 130,000", [125_000]) == [130_000]


def test_translation_must_keep_every_number():
    assert numbers_preserved("Rose 25% to 125,000 in 2024", "2024年增长25%，达到12.5万")
    assert not numbers_preserved("Rose 25% to 125,000 in 2024", "2024年增长25%，达到13万")
    assert not numbers_preserved("Rose 25% to 125,000", "Naik 25% kepada 125,000 pada 2023")
