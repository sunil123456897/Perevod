from Perevod.model_registry import (
    GEMINI_EMBEDDING,
    GEMINI_FLASH,
    GEMINI_FLASH_LITE,
    default_daily_limits,
    model_min_interval_seconds,
    normalize_embedding_model,
    normalize_model_configs,
)


def test_default_daily_limits_match_free_tier_profile():
    from Perevod.model_registry import GEMINI_35_FLASH, GEMMA_31B, GEMMA_26B
    assert default_daily_limits() == {
        GEMINI_FLASH: 20,
        GEMINI_35_FLASH: 20,
        GEMINI_FLASH_LITE: 500,
        GEMMA_31B: 1500,
        GEMMA_26B: 1500,
        GEMINI_EMBEDDING: 1000,
    }


def test_model_registry_normalizes_legacy_free_tier_models():
    normalized = normalize_model_configs(
        {
            "analysis": "gemini-2.5-pro",
            "translation": "gemini-2.5-flash",
            "qa": "gemini-2.5-flash-lite",
        },
        free_tier_mode=True,
    )

    assert normalized == {
        "analysis": GEMINI_FLASH,
        "translation": GEMINI_FLASH,
        "qa": GEMINI_FLASH_LITE,
    }


def test_model_registry_normalizes_legacy_embedding_models():
    assert (
        normalize_embedding_model("models/text-embedding-004", free_tier_mode=True)
        == GEMINI_EMBEDDING
    )
    assert (
        normalize_embedding_model("text-embedding-004", free_tier_mode=True)
        == GEMINI_EMBEDDING
    )


def test_model_registry_exposes_rpm_intervals_for_text_models():
    assert model_min_interval_seconds(GEMINI_FLASH) == 12.0
    assert model_min_interval_seconds(GEMINI_FLASH_LITE) == 4.0
    assert model_min_interval_seconds(GEMINI_EMBEDDING) is None
