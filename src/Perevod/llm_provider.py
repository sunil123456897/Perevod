# src/Perevod/llm_provider.py
import logging
import threading
import time
from typing import Dict
from google import genai
from google.genai import types

from Perevod.api_usage import (
    ApiUsageLimitExceeded,
    ApiUsageTracker,
    should_track_api_usage,
)
from Perevod.model_registry import model_min_interval_seconds
from Perevod.utils.api_errors import (
    GeminiAPIError,
    classify_api_error,
    is_retryable_api_error,
)

logger = logging.getLogger("NovelTranslator.LLMProvider")


def _timeout_seconds_to_milliseconds(timeout_seconds: int | float | None) -> int | None:
    if timeout_seconds is None:
        return None
    return int(timeout_seconds * 1000)


def _gateway_error(model_name: str, operation: str, error: Exception) -> GeminiAPIError:
    return GeminiAPIError(
        model_name=model_name,
        operation=operation,
        info=classify_api_error(error),
        original_error=error,
    )


class GeminiModelAdapter:
    """Compatibility wrapper exposing the old generate_content contract."""

    def __init__(
        self,
        client: genai.Client,
        model_name: str,
        usage_tracker: ApiUsageTracker | None = None,
        min_interval_seconds: float | None = None,
        last_call_times: dict[str, float] | None = None,
        time_func=time.monotonic,
        default_request_timeout_seconds: int = 600,
        rate_limit_lock=None,
    ):
        self.client = client
        self.model_name = model_name
        self.usage_tracker = usage_tracker
        self.min_interval_seconds = min_interval_seconds
        self._last_call_times = last_call_times if last_call_times is not None else {}
        self._time_func = time_func
        self.default_request_timeout_seconds = default_request_timeout_seconds
        self._rate_limit_lock = rate_limit_lock or threading.Lock()

    def _apply_rate_limit(self, sleep_func) -> None:
        if not self.usage_tracker or not self.min_interval_seconds:
            return

        with self._rate_limit_lock:
            now = self._time_func()
            previous_call_at = self._last_call_times.get(self.model_name)
            if previous_call_at is not None:
                elapsed = now - previous_call_at
                delay = self.min_interval_seconds - elapsed
                if delay > 0:
                    logger.info(
                        "Ограничение RPM для модели '%s': пауза %.2f секунд.",
                        self.model_name,
                        delay,
                    )
                    sleep_func(delay)
                    now = self._time_func()
            self._last_call_times[self.model_name] = now

    def generate_content(
        self,
        prompt: str,
        generation_config: dict | None = None,
        request_options: dict | None = None,
        max_retries: int = 6,
        initial_delay: float = 10,
        max_delay: float = 60,
        sleep_func=time.sleep,
    ):
        generation_config = generation_config or {}
        request_options = request_options or {}
        timeout_seconds = request_options.get("timeout")
        if timeout_seconds is None:
            timeout_seconds = self.default_request_timeout_seconds
        config = types.GenerateContentConfig(
            temperature=generation_config.get("temperature"),
            topP=generation_config.get("top_p"),
            httpOptions=types.HttpOptions(
                timeout=_timeout_seconds_to_milliseconds(timeout_seconds)
            ),
            safetySettings=[
                types.SafetySetting(
                    category=types.HarmCategory.HARM_CATEGORY_HARASSMENT,
                    threshold=types.HarmBlockThreshold.BLOCK_NONE,
                ),
                types.SafetySetting(
                    category=types.HarmCategory.HARM_CATEGORY_HATE_SPEECH,
                    threshold=types.HarmBlockThreshold.BLOCK_NONE,
                ),
                types.SafetySetting(
                    category=types.HarmCategory.HARM_CATEGORY_SEXUALLY_EXPLICIT,
                    threshold=types.HarmBlockThreshold.BLOCK_NONE,
                ),
                types.SafetySetting(
                    category=types.HarmCategory.HARM_CATEGORY_DANGEROUS_CONTENT,
                    threshold=types.HarmBlockThreshold.BLOCK_NONE,
                ),
            ],
        )
        for attempt in range(1, max_retries + 1):
            reservation_id = None
            try:
                if self.usage_tracker:
                    reservation_id = self.usage_tracker.reserve_call(
                        self.model_name,
                        "generateContent",
                    )
                self._apply_rate_limit(sleep_func)
                response = self.client.models.generate_content(
                    model=self.model_name,
                    contents=prompt,
                    config=config,
                )
                if self.usage_tracker:
                    temp_res_id = reservation_id
                    reservation_id = None
                    try:
                        self.usage_tracker.record_call(
                            self.model_name,
                            "generateContent",
                            reservation_id=temp_res_id,
                        )
                    except Exception as tracker_err:
                        logger.warning("Failed to record api usage in generate_content: %s", tracker_err)
                return response
            except Exception as error:
                if self.usage_tracker and reservation_id is not None:
                    self.usage_tracker.release_call(
                        self.model_name,
                        "generateContent",
                        reservation_id=reservation_id,
                    )
                if isinstance(error, ApiUsageLimitExceeded):
                    raise
                if not is_retryable_api_error(error) or attempt >= max_retries:
                    raise _gateway_error(
                        self.model_name,
                        "generateContent",
                        error,
                    ) from error

                delay = min(initial_delay * (2 ** (attempt - 1)), max_delay)
                logger.warning(
                    "Временная ошибка Gemini API для модели '%s': %s. "
                    "Повторная попытка %s/%s через %.2f секунд.",
                    self.model_name,
                    error,
                    attempt,
                    max_retries,
                    delay,
                )
                sleep_func(delay)

        raise RuntimeError("Gemini API retry loop exited unexpectedly.")


class GeminiEmbeddingAdapter:
    """Gateway wrapper for Gemini embedContent calls."""

    def __init__(
        self,
        client: genai.Client,
        model_name: str,
        usage_tracker: ApiUsageTracker | None = None,
        default_request_timeout_seconds: int = 300,
    ):
        self.client = client
        self.model_name = model_name
        self.usage_tracker = usage_tracker
        self.default_request_timeout_seconds = default_request_timeout_seconds

    def embed_content(
        self,
        texts: list[str],
        task_type: str,
        output_dimensionality: int = 3072,
        request_options: dict | None = None,
        max_retries: int = 4,
        initial_delay: float = 10,
        sleep_func=time.sleep,
    ):
        request_options = request_options or {}
        timeout_seconds = request_options.get("timeout")
        if timeout_seconds is None:
            timeout_seconds = self.default_request_timeout_seconds
        config = types.EmbedContentConfig(
            taskType=task_type,
            outputDimensionality=output_dimensionality,
            httpOptions=types.HttpOptions(
                timeout=_timeout_seconds_to_milliseconds(timeout_seconds)
            ),
        )
        for attempt in range(1, max_retries + 1):
            reservation_id = None
            try:
                if self.usage_tracker:
                    reservation_id = self.usage_tracker.reserve_call(
                        self.model_name,
                        "embedContent",
                    )
                response = self.client.models.embed_content(
                    model=self.model_name,
                    contents=texts,
                    config=config,
                )
                if self.usage_tracker:
                    temp_res_id = reservation_id
                    reservation_id = None
                    try:
                        self.usage_tracker.record_call(
                            self.model_name,
                            "embedContent",
                            reservation_id=temp_res_id,
                        )
                    except Exception as tracker_err:
                        logger.warning("Failed to record api usage in embed_content: %s", tracker_err)
                return response
            except Exception as error:
                if self.usage_tracker and reservation_id is not None:
                    self.usage_tracker.release_call(
                        self.model_name,
                        "embedContent",
                        reservation_id=reservation_id,
                    )
                if isinstance(error, ApiUsageLimitExceeded):
                    raise
                if not is_retryable_api_error(error) or attempt >= max_retries:
                    raise _gateway_error(
                        self.model_name,
                        "embedContent",
                        error,
                    ) from error

                delay = initial_delay * (2 ** (attempt - 1))
                logger.warning(
                    "Временная ошибка Gemini Embedding API для модели '%s': %s. "
                    "Повторная попытка %s/%s через %.2f секунд.",
                    self.model_name,
                    error,
                    attempt,
                    max_retries,
                    delay,
                )
                sleep_func(delay)

        raise RuntimeError("Gemini Embedding API retry loop exited unexpectedly.")


class LLMProvider:
    """
    Управляет инициализацией и доступом к различным моделям LLM.
    Кэширует инстансы моделей для предотвращения повторной инициализации.
    """

    def __init__(
        self,
        model_configs: Dict[str, str],
        api_key: str,
        usage_tracker: ApiUsageTracker | None = None,
    ):
        """
        Инициализируется словарем конфигураций.
        Пример: {'analysis': 'gemini-3.1-flash-lite-preview', 'translation': 'gemini-3-flash-preview'}
        """
        self.model_configs = model_configs
        self.api_key = api_key
        self.client = genai.Client(api_key=self.api_key)
        self.usage_tracker = (
            usage_tracker
            if usage_tracker is not None
            else ApiUsageTracker()
            if should_track_api_usage(api_key)
            else None
        )
        self._models_cache: Dict[str, GeminiModelAdapter] = {}
        self._last_call_times: Dict[str, float] = {}
        self._rate_limit_locks: Dict[str, threading.Lock] = {}

        logger.info(f"LLMProvider инициализирован с конфигурацией: {model_configs}")

    def get_model(self, task_name: str) -> GeminiModelAdapter:
        """
        Возвращает инстанс модели для указанной задачи.
        Если модель уже была создана, возвращает ее из кэша.
        """
        if task_name not in self.model_configs:
            raise ValueError(
                f"Конфигурация для задачи '{task_name}' не найдена в LLMProvider."
            )

        model_name = (self.model_configs[task_name] or "").strip()
        if not model_name:
            raise ValueError(
                f"Имя модели для задачи '{task_name}' не указано в LLMProvider."
            )

        if model_name in self._models_cache:
            return self._models_cache[model_name]

        logger.info(
            f"Создание нового инстанса модели '{model_name}' для задачи '{task_name}'..."
        )
        model = GeminiModelAdapter(
            self.client,
            model_name,
            usage_tracker=self.usage_tracker,
            min_interval_seconds=model_min_interval_seconds(model_name)
            if self.usage_tracker
            else None,
            last_call_times=self._last_call_times,
            rate_limit_lock=self._rate_limit_locks.setdefault(
                model_name,
                threading.Lock(),
            ),
        )
        self._models_cache[model_name] = model
        return model
