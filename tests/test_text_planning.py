import pytest

from Perevod.utils.text_planning import estimate_token_count, split_text_by_token_budget


def test_estimate_token_count_uses_conservative_character_ratio():
    assert estimate_token_count("") == 0
    assert estimate_token_count("abcd") == 1
    assert estimate_token_count("abcde") == 2


def test_split_text_by_token_budget_keeps_short_text_whole():
    assert split_text_by_token_budget("short text", 100) == ["short text"]


def test_split_text_by_token_budget_prefers_paragraph_boundaries():
    text = "A" * 20 + "\n\n" + "B" * 20

    assert split_text_by_token_budget(text, 5) == ["A" * 20, "B" * 20]


def test_split_text_by_token_budget_splits_oversized_paragraph():
    chunks = split_text_by_token_budget("A" * 50, 5)

    assert len(chunks) > 1
    assert "".join(chunks) == "A" * 50


def test_split_text_by_token_budget_rejects_invalid_budget():
    with pytest.raises(ValueError):
        split_text_by_token_budget("text", 0)
