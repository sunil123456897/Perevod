# --- НАЧАЛО ФАЙЛА: src/Perevod/utils/llm.py ---

import logging
import threading
import time
import json
from Perevod.config import Settings
from Perevod.model_registry import model_min_interval_seconds
from Perevod.utils.api_errors import is_retryable_api_error

logger = logging.getLogger("NovelTranslator.LLM")

_LAST_REQUEST_AT: dict[str, float] = {}
_RATE_LIMIT_LOCK = threading.Lock()


def _get_generation_setting(settings: Settings | dict, name: str, default: float) -> float:
    if isinstance(settings, dict):
        return settings.get(name, default)
    return getattr(settings, name, default)


def _extract_json_payload(text: str) -> str:
    cleaned_text = text.strip()
    if cleaned_text.startswith("```"):
        cleaned_text = (
            cleaned_text.replace("```json", "", 1).replace("```", "", 1).strip()
        )

    start_positions = [
        pos for pos in (cleaned_text.find("{"), cleaned_text.find("[")) if pos != -1
    ]
    if not start_positions:
        return cleaned_text

    start = min(start_positions)
    opening = cleaned_text[start]
    closing = "}" if opening == "{" else "]"
    depth = 0
    in_string = False
    escaped = False

    for index in range(start, len(cleaned_text)):
        char = cleaned_text[index]
        if escaped:
            escaped = False
            continue
        if char == "\\":
            escaped = True
            continue
        if char == '"':
            in_string = not in_string
            continue
        if in_string:
            continue
        if char == opening:
            depth += 1
        elif char == closing:
            depth -= 1
            if depth == 0:
                return cleaned_text[start : index + 1]

    return cleaned_text


# --- ДОБАВЛЕНО: Утилита для безопасного парсинга JSON от LLM ---
def safe_json_loads(text: str, default=None):
    """
    Безопасно парсит строку JSON, возвращая default значение в случае ошибки.
    Логирует некорректный текст ответа от LLM.
    """
    fallback = {} if default is None else default
    if not isinstance(text, str):
        return fallback
    cleaned_text = _extract_json_payload(text)
    if not cleaned_text:
        return fallback
    try:
        return json.loads(cleaned_text)
    except json.JSONDecodeError:
        logger.error(f"Не удалось распарсить JSON от LLM. Ответ: {cleaned_text[:500]}...")
        return fallback


def clean_translation_output(text: str) -> str:
    """Cleans and formats the translation output from the LLM."""
    # --- ИСПРАВЛЕНО: Логика очистки перенесена в safe_json_loads, здесь оставляем только базовую. ---
    return text.strip()


def _build_generation_config(settings: Settings | dict) -> dict:
    return {
        "temperature": _get_generation_setting(settings, "temperature", 0.7),
        "top_p": _get_generation_setting(settings, "top_p", 0.9),
    }


def _get_request_timeout(settings: Settings | dict) -> int:
    return int(_get_generation_setting(settings, "request_timeout", 300))


def _rate_limit_model(model, sleep_func=time.sleep, monotonic_func=None) -> None:
    monotonic_func = monotonic_func or time.monotonic
    model_name = getattr(model, "model_name", None)
    if not isinstance(model_name, str):
        return

    min_interval = model_min_interval_seconds(model_name)
    if not min_interval:
        return

    with _RATE_LIMIT_LOCK:
        now = monotonic_func()
        last_request_at = _LAST_REQUEST_AT.get(model_name)
        if last_request_at is not None:
            wait_seconds = min_interval - (now - last_request_at)
            if wait_seconds > 0:
                logger.info(
                    "Ограничение RPM для %s: ожидание %.2f секунд.",
                    model_name,
                    wait_seconds,
                )
                sleep_func(wait_seconds)
                now = monotonic_func()
        _LAST_REQUEST_AT[model_name] = now


def _model_handles_gateway_concerns(model) -> bool:
    try:
        from Perevod.llm_provider import GeminiModelAdapter
    except Exception:
        return False
    return isinstance(model, GeminiModelAdapter)


def generate_text(
    model,
    prompt: str,
    settings: Settings | dict,
    *,
    max_retries: int = 5,
    initial_delay: float = 1,
    sleep_func=time.sleep,
) -> str:
    """Generates text through an LLM model with bounded retries and timeout."""
    generation_config = {
        **_build_generation_config(settings),
    }
    request_options = {"timeout": _get_request_timeout(settings)}
    gateway_model = _model_handles_gateway_concerns(model)
    retry_attempts = 1 if gateway_model else max_retries
    for attempt in range(1, retry_attempts + 1):
        try:
            if not gateway_model:
                _rate_limit_model(model, sleep_func=sleep_func)
            response = model.generate_content(
                prompt,
                generation_config=generation_config,
                request_options=request_options,
            )
            try:
                response_text = response.text
            except (AttributeError, ValueError) as e:
                feedback = getattr(response, "prompt_feedback", None)
                logger.warning(
                    f"API не вернул доступный текст ответа. Причина: {feedback or e}"
                )
                return ""
            # --- ДОБАВЛЕНО: Проверка на наличие текста в ответе ---
            if not response_text:
                logger.warning(
                    f"API вернул пустой текстовый ответ. Причина: {getattr(response, 'prompt_feedback', None)}"
                )
                return ""
            return response_text
        except Exception as e:
            if not is_retryable_api_error(e):
                logger.error(f"Ошибка при запросе к LLM: {e}", exc_info=True)
                raise
            if attempt >= retry_attempts:
                logger.error(
                    f"Достигнуто максимальное количество повторных попыток. Не удалось выполнить запрос к LLM: {e}",
                    exc_info=True,
                )
                raise
            delay = initial_delay * (2 ** (attempt - 1))
            logger.warning(
                f"Временная ошибка API. Повторная попытка {attempt}/{retry_attempts} через {delay:.2f} секунд..."
            )
            sleep_func(delay)
    return ""


def tool_translate_chunk(model, prompt: str, settings: Settings) -> str:
    """Translates a chunk of text using the shared LLM generation adapter."""
    return generate_text(model, prompt, settings)

# --- КОНЕЦ ФАЙЛА: src/Perevod/utils/llm.py ---
