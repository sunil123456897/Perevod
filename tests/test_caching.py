from Perevod.utils.caching import generate_translation_cache_key


def test_translation_cache_key_uses_sha256_length():
    cache_key = generate_translation_cache_key(
        {
            "original_chunk": "Text",
            "dictionary": {"Council": "Совет"},
            "relevant_context": "Context",
        },
        "model",
    )

    assert len(cache_key) == 64


def test_translation_cache_key_changes_with_style_guide():
    base_payload = {
        "original_chunk": "Text",
        "dictionary": {"Council": "Совет"},
        "relevant_context": "Context",
    }

    restrained_key = generate_translation_cache_key(
        {**base_payload, "style_guide": "Use restrained literary Russian."},
        "model",
    )
    archaic_key = generate_translation_cache_key(
        {**base_payload, "style_guide": "Use archaic epic diction."},
        "model",
    )

    assert restrained_key != archaic_key


def test_translation_cache_key_changes_with_prompt_version():
    base_payload = {
        "original_chunk": "Text",
        "dictionary": {"Council": "Совет"},
        "relevant_context": "Context",
        "prompt_version": "translation-prompt-v1",
    }

    old_prompt_key = generate_translation_cache_key(base_payload, "model")
    new_prompt_key = generate_translation_cache_key(
        {**base_payload, "prompt_version": "translation-prompt-v2"},
        "model",
    )

    assert old_prompt_key != new_prompt_key


def test_translation_cache_key_changes_with_generation_settings():
    base_payload = {
        "original_chunk": "Text",
        "dictionary": {"Council": "Совет"},
        "relevant_context": "Context",
        "generation_settings": {"temperature": 0.2, "top_p": 0.8},
    }

    conservative_key = generate_translation_cache_key(base_payload, "model")
    creative_key = generate_translation_cache_key(
        {
            **base_payload,
            "generation_settings": {"temperature": 0.7, "top_p": 0.95},
        },
        "model",
    )

    assert conservative_key != creative_key
