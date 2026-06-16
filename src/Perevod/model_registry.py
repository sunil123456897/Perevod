from dataclasses import dataclass


GEMINI_FLASH = "gemini-3-flash-preview"
GEMINI_FLASH_LITE = "gemini-3.1-flash-lite-preview"
GEMINI_35_FLASH = "gemini-3.5-flash"
GEMINI_EMBEDDING = "gemini-embedding-2"
GEMMA_31B = "gemma-4-31b-it"
GEMMA_26B = "gemma-4-26b-a4b-it"


@dataclass(frozen=True)
class GeminiModelSpec:
    name: str
    category: str
    daily_limit: int | None = None
    min_interval_seconds: float | None = None


MODEL_REGISTRY: dict[str, GeminiModelSpec] = {
    GEMINI_FLASH: GeminiModelSpec(
        name=GEMINI_FLASH,
        category="text",
        daily_limit=20,
        min_interval_seconds=12.0,
    ),
    GEMINI_35_FLASH: GeminiModelSpec(
        name=GEMINI_35_FLASH,
        category="text",
        daily_limit=20,
        min_interval_seconds=12.0,
    ),
    GEMINI_FLASH_LITE: GeminiModelSpec(
        name=GEMINI_FLASH_LITE,
        category="text",
        daily_limit=500,
        min_interval_seconds=4.0,
    ),
    GEMMA_31B: GeminiModelSpec(
        name=GEMMA_31B,
        category="text",
        daily_limit=1500,
        min_interval_seconds=4.0,
    ),
    GEMMA_26B: GeminiModelSpec(
        name=GEMMA_26B,
        category="text",
        daily_limit=1500,
        min_interval_seconds=4.0,
    ),
    GEMINI_EMBEDDING: GeminiModelSpec(
        name=GEMINI_EMBEDDING,
        category="embedding",
        daily_limit=1000,
    ),
}

DEFAULT_TASK_MODELS = {
    "analysis": GEMINI_FLASH_LITE,
    "curation": GEMINI_FLASH_LITE,
    "translation": GEMMA_31B,
    "qa": GEMINI_FLASH_LITE,
    "summarization": GEMINI_FLASH_LITE,
}

# Auxiliary expert models used for QA escalation and refinement.
# Kept in the registry (not hardcoded in config) so that a model rename or
# budget change only touches one place.
DEFAULT_EXPERT_JUDGE_MODEL = GEMINI_35_FLASH
DEFAULT_EDITOR_MODEL = GEMINI_35_FLASH

AVAILABLE_TEXT_MODELS = [
    GEMINI_FLASH,
    GEMINI_FLASH_LITE,
    GEMINI_35_FLASH,
    GEMMA_31B,
    GEMMA_26B,
]

LEGACY_FREE_TIER_REPLACEMENTS = {
    "gemini-2.5-flash": GEMINI_FLASH,
    "gemini-2.5-flash-lite": GEMINI_FLASH_LITE,
}

LEGACY_EMBEDDING_REPLACEMENTS = {
    "models/text-embedding-004": GEMINI_EMBEDDING,
    "text-embedding-004": GEMINI_EMBEDDING,
}


def default_daily_limits() -> dict[str, int]:
    return {
        name: spec.daily_limit
        for name, spec in MODEL_REGISTRY.items()
        if spec.daily_limit is not None
    }


def model_min_interval_seconds(model_name: str) -> float | None:
    spec = MODEL_REGISTRY.get((model_name or "").strip())
    return spec.min_interval_seconds if spec else None


def normalize_text_model(model_name: str, *, free_tier_mode: bool) -> str:
    normalized = (model_name or "").strip()
    if not free_tier_mode:
        return normalized

    lowered = normalized.lower()
    if lowered in LEGACY_FREE_TIER_REPLACEMENTS:
        return LEGACY_FREE_TIER_REPLACEMENTS[lowered]
    if lowered.endswith("-pro"):
        return GEMINI_FLASH
    return normalized


def normalize_model_configs(
    model_configs: dict[str, str], *, free_tier_mode: bool
) -> dict[str, str]:
    return {
        task_name: normalize_text_model(model_name, free_tier_mode=free_tier_mode)
        for task_name, model_name in model_configs.items()
    }


def normalize_embedding_model(model_name: str, *, free_tier_mode: bool) -> str:
    normalized = (model_name or "").strip()
    if not free_tier_mode:
        return normalized
    return LEGACY_EMBEDDING_REPLACEMENTS.get(normalized.lower(), normalized)
