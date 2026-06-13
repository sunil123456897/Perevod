# src/Perevod/utils/caching.py
import json
import hashlib
from typing import Dict, Any


def generate_translation_cache_key(chunk_data: Dict[str, Any], model_name: str) -> str:
    """
    Генерирует консистентный ключ кэша на основе входных данных для перевода.
    Использует только те данные, которые влияют на результат перевода.
    """
    dictionary = chunk_data.get("dictionary", {})
    context = chunk_data.get("relevant_context", "")
    style_guide = chunk_data.get("style_guide", "")
    summary = chunk_data.get("summary", "")
    original_chunk = chunk_data.get("original_chunk", "")
    prompt_version = chunk_data.get("prompt_version", "")
    generation_settings = chunk_data.get("generation_settings", {})

    sorted_dict_str = json.dumps(dictionary, sort_keys=True, ensure_ascii=False)
    generation_settings_str = json.dumps(
        generation_settings,
        sort_keys=True,
        ensure_ascii=False,
    )

    full_input = (
        f"{original_chunk}|{model_name}|{sorted_dict_str}|{context}|"
        f"{style_guide}|{summary}|{prompt_version}|{generation_settings_str}"
    )
    return hashlib.sha256(full_input.encode("utf-8")).hexdigest()
