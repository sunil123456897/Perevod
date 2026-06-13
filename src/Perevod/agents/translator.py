# agents/translator.py
import logging
from Perevod.llm_provider import LLMProvider
from Perevod.utils.llm import generate_text

logger = logging.getLogger("NovelTranslator.TranslatorAgent")


def simple_translate(text_to_translate, api_key, model_name):
    """
    Простая функция для перевода текста с английского на русский.
    """
    if not text_to_translate:
        return ""
    try:
        provider = LLMProvider({"simple_translation": model_name}, api_key=api_key)
        model = provider.get_model("simple_translation")
        prompt = f"Translate the following English text to Russian. Preserve the original meaning, style, and tone. Text to translate:\n\n---\n{text_to_translate}\n---\n\nRussian translation:"
        return generate_text(model, prompt, {}).strip()
    except Exception as e:
        logger.error(f"Ошибка при вызове API перевода: {e}", exc_info=True)
        return f"ОШИБКА ПЕРЕВОДА: {e}"
