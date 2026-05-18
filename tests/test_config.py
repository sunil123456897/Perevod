import os
from types import SimpleNamespace

from Perevod.config import (
    Settings,
    apply_proxy_environment,
    normalize_embedding_model,
    normalize_model_configs,
)


def test_settings_can_load_without_global_api_key(monkeypatch):
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)

    settings = Settings(_env_file=None)

    assert settings.GOOGLE_API_KEY == ""


def test_default_models_use_free_tier_friendly_flash_family():
    settings = Settings(_env_file=None)

    assert settings.analysis_model_name == "gemini-3.1-flash-lite-preview"
    assert settings.curation_model_name == "gemini-3.1-flash-lite-preview"
    assert settings.translation_model_name == "gemini-3-flash-preview"
    assert settings.qa_model_name == "gemini-3.1-flash-lite-preview"
    assert settings.summarization_model_name == "gemini-3.1-flash-lite-preview"
    assert settings.embedding_model_name == "gemini-embedding-2"


def test_apply_proxy_environment_sets_missing_process_proxy(monkeypatch):
    monkeypatch.delenv("HTTPS_PROXY", raising=False)
    monkeypatch.setenv("HTTP_PROXY", "http://already-set:8080")

    proxy_settings = SimpleNamespace(
        HTTPS_PROXY="http://127.0.0.1:7890",
        HTTP_PROXY="http://from-env-file:8080",
        https_proxy="",
        http_proxy="",
    )

    apply_proxy_environment(proxy_settings)

    assert os.environ["HTTPS_PROXY"] == "http://127.0.0.1:7890"
    assert os.environ["HTTP_PROXY"] == "http://already-set:8080"


def test_normalize_model_configs_replaces_pro_models_in_free_tier_mode():
    configs = {
        "analysis": "gemini-2.5-pro",
        "translation": "gemini-2.5-flash",
        "qa": "gemini-2.5-flash-lite",
    }

    normalized = normalize_model_configs(configs, free_tier_mode=True)

    assert normalized["analysis"] == "gemini-3-flash-preview"
    assert normalized["translation"] == "gemini-3-flash-preview"
    assert normalized["qa"] == "gemini-3.1-flash-lite-preview"


def test_normalize_embedding_model_replaces_legacy_embedding_model():
    assert (
        normalize_embedding_model("models/text-embedding-004", free_tier_mode=True)
        == "gemini-embedding-2"
    )
    assert (
        normalize_embedding_model("text-embedding-004", free_tier_mode=True)
        == "gemini-embedding-2"
    )
