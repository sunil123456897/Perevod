import logging
import json
import time
from typing import Dict, Any
import google.api_core.exceptions

logger = logging.getLogger("NovelTranslator.LLM")

def clean_translation_output(text: str) -> str:
    """Cleans and formats the translation output from the LLM."""
    # This is a placeholder. Actual cleaning logic would go here.
    return text.strip().replace("```json", "").replace("```", "").strip()

def tool_translate_chunk(model, prompt: str, settings: Dict[str, Any]) -> str:
    """Translates a chunk of text using the LLM with retry logic for quota errors."""
    max_retries = 5
    initial_delay = 1  # seconds
    retries = 0

    generation_config = {
        "temperature": settings.get('temperature', 0.7),
        "top_p": settings.get('top_p', 0.9),
    }

    while retries < max_retries:
        try:
            response = model.generate_content(prompt, generation_config=generation_config)
            return response.text
        except google.api_core.exceptions.ResourceExhausted as e:
            retries += 1
            delay = initial_delay * (2 ** (retries - 1))  # Exponential backoff
            logger.warning(f"Превышена квота API. Повторная попытка {retries}/{max_retries} через {delay:.2f} секунд...")
            time.sleep(delay)
            if retries == max_retries:
                logger.error(f"Достигнуто максимальное количество повторных попыток. Не удалось выполнить запрос к LLM: {e}", exc_info=True)
                raise
        except Exception as e:
            logger.error(f"Ошибка при запросе к LLM: {e}", exc_info=True)
            raise
