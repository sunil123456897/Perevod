from unittest.mock import MagicMock, patch

from Perevod.agents.translator import simple_translate


@patch("Perevod.agents.translator.LLMProvider")
def test_simple_translate_uses_llm_provider(mock_provider):
    model = MagicMock()
    model.generate_content.return_value.text = " Перевод "
    mock_provider.return_value.get_model.return_value = model

    result = simple_translate("Text", "key", "gemini-2.5-flash")

    assert result == "Перевод"
    mock_provider.assert_called_once_with({"simple_translation": "gemini-2.5-flash"}, api_key="key")
    mock_provider.return_value.get_model.assert_called_once_with("simple_translation")


def test_simple_translate_returns_empty_for_empty_input():
    assert simple_translate("", "key", "model") == ""
