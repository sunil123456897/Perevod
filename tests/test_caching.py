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
