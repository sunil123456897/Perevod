# src/Perevod/config.py
import os
from pydantic_settings import BaseSettings, SettingsConfigDict

from Perevod.model_registry import (
    DEFAULT_EDITOR_MODEL,
    DEFAULT_EXPERT_JUDGE_MODEL,
    DEFAULT_TASK_MODELS,
    GEMINI_EMBEDDING,
    normalize_embedding_model as _normalize_embedding_model,
    normalize_model_configs as _normalize_model_configs,
)

# Определяем абсолютный путь к корневой директории проекта
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))


class Settings(BaseSettings):
    """
    Централизованная и валидируемая конфигурация для всего приложения.
    Автоматически читает переменные из файла .env и окружения.
    """

    model_config = SettingsConfigDict(
        env_file=os.path.join(PROJECT_ROOT, ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- Ключевые параметры ---
    GOOGLE_API_KEY: str = ""
    HTTPS_PROXY: str = ""
    HTTP_PROXY: str = ""
    https_proxy: str = ""
    http_proxy: str = ""

    # --- Настройки моделей по задачам ---
    gemini_free_tier_mode: bool = True
    analysis_model_name: str = DEFAULT_TASK_MODELS["analysis"]
    curation_model_name: str = DEFAULT_TASK_MODELS["curation"]
    translation_model_name: str = DEFAULT_TASK_MODELS["translation"]
    qa_model_name: str = DEFAULT_TASK_MODELS["qa"]
    judge_model_name: str = DEFAULT_TASK_MODELS["qa"]
    expert_judge_model_name: str = DEFAULT_EXPERT_JUDGE_MODEL
    editor_model_name: str = DEFAULT_EDITOR_MODEL
    summarization_model_name: str = DEFAULT_TASK_MODELS["summarization"]

    # Модель для создания эмбеддингов
    embedding_model_name: str = GEMINI_EMBEDDING

    # --- Параметры генерации ---
    temperature: float = 0.5
    top_p: float = 0.95
    translation_chunk_token_budget: int = 120_000

    # --- Настройки семантического ядра ---
    relevance_threshold: float = 0.75
    max_context_items: int = 7
    enable_reranker: bool = False

# Создаем единый, глобально доступный экземпляр настроек
settings = Settings()


def apply_proxy_environment(settings_obj: Settings) -> None:
    for name in ("HTTPS_PROXY", "HTTP_PROXY", "https_proxy", "http_proxy"):
        value = getattr(settings_obj, name, "")
        if value and not os.environ.get(name):
            os.environ[name] = value


def normalize_model_configs(
    model_configs: dict[str, str], *, free_tier_mode: bool
) -> dict[str, str]:
    return _normalize_model_configs(model_configs, free_tier_mode=free_tier_mode)


def normalize_embedding_model(model_name: str, *, free_tier_mode: bool) -> str:
    return _normalize_embedding_model(model_name, free_tier_mode=free_tier_mode)


apply_proxy_environment(settings)
