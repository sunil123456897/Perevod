from unittest.mock import MagicMock, patch
import sqlite3

import pytest

from Perevod.knowledge_base.knowledge_base_manager import GenAIEmbeddingFunction
from Perevod.knowledge_base.knowledge_base_manager import KnowledgeBaseManager

@pytest.fixture
def mock_db_manager():
    db_manager = MagicMock()
    db_manager.get_terms_dictionary.return_value = {
        "term1": {"russian_term": "термин1", "category": "other"}
    }
    db_manager.get_world_bible.return_value = {"entry1": {"description": "description1"}}
    return db_manager

@pytest.fixture
def kb_manager():
    with patch("chromadb.PersistentClient"):
        with patch("Perevod.knowledge_base.knowledge_base_manager.genai.Client"):
            with patch("Perevod.knowledge_base.knowledge_base_manager.Reranker"):
                yield KnowledgeBaseManager("test_project", "test_api_key", "test_model")

def test_kb_manager_initialization(kb_manager):
    assert kb_manager.project_name == "test_project"


def test_kb_manager_rejects_unsafe_project_name():
    unsafe_name = "Project:ads"

    with pytest.raises(ValueError, match="Unsafe project name"):
        KnowledgeBaseManager(unsafe_name, "test_api_key", "test_model")


def test_kb_manager_rejects_chromadb_thin_client(monkeypatch):
    monkeypatch.setattr(
        "Perevod.knowledge_base.knowledge_base_manager.chromadb.config.is_thin_client",
        True,
    )

    with pytest.raises(RuntimeError, match="chromadb-client"):
        KnowledgeBaseManager("test_project", "test_api_key", "test_model")


def test_kb_manager_resets_collection_on_embedding_function_conflict():
    client = MagicMock()
    client.get_or_create_collection.side_effect = [
        ValueError("Embedding function conflict: new: google_genai vs persisted: google_generative_ai"),
        MagicMock(name="collection"),
    ]

    with patch("chromadb.PersistentClient", return_value=client):
        with patch("Perevod.knowledge_base.knowledge_base_manager.genai.Client"):
            kb = KnowledgeBaseManager("test_project", "test_api_key", "test_model")

    client.delete_collection.assert_called_once_with(name="test_project_kb")
    assert kb.collection is not None


def test_query_with_rerank(kb_manager):
    kb_manager.reranker = MagicMock()
    mock_collection = MagicMock()
    mock_collection.count.return_value = 1
    mock_collection.query.return_value = {
        "ids": [["id1"]], "documents": [["doc1"]], "metadatas": [[{"source": "bible"}]]
    }
    kb_manager.collection = mock_collection
    kb_manager.reranker.rerank.return_value = [{"text": "reranked_doc"}]
    
    result = kb_manager.query("valid query")
    assert "reranked_doc" in result
    kb_manager.reranker.rerank.assert_called_once()


def test_kb_manager_does_not_load_heavy_reranker_by_default():
    with patch("chromadb.PersistentClient"):
        with patch("Perevod.knowledge_base.knowledge_base_manager.genai.Client"):
            with patch("Perevod.knowledge_base.knowledge_base_manager.Reranker") as mock_reranker:
                kb = KnowledgeBaseManager("test_project", "test_api_key", "test_model")

    assert kb.reranker is None
    mock_reranker.assert_not_called()


def test_kb_manager_does_not_track_usage_for_test_api_keys():
    with patch("chromadb.PersistentClient"):
        with patch("Perevod.knowledge_base.knowledge_base_manager.genai.Client"):
            with patch("Perevod.knowledge_base.knowledge_base_manager.Reranker"):
                kb = KnowledgeBaseManager("test_project", "test_api_key", "test_model")

    assert kb.embedding_function.usage_tracker is None


def test_query_uses_chroma_ranking_when_reranker_disabled(kb_manager):
    kb_manager.reranker = None
    mock_collection = MagicMock()
    mock_collection.count.return_value = 2
    mock_collection.query.return_value = {
        "ids": [["id1", "id2"]],
        "documents": [["doc1", "doc2"]],
        "metadatas": [[{"source": "bible"}, {"source": "dictionary"}]],
    }
    kb_manager.collection = mock_collection

    result = kb_manager.query("valid query", top_k=1)

    assert "doc1" in result
    assert "doc2" not in result


def test_upsert_resets_collection_once_on_embedding_dimension_mismatch(kb_manager):
    old_collection = MagicMock()
    old_collection.name = "test_project_kb"
    old_collection.upsert.side_effect = [
        ValueError("Collection expecting embedding with dimension of 768, got 3072"),
        None,
    ]
    kb_manager.collection = old_collection
    kb_manager.client.get_or_create_collection.return_value = old_collection

    kb_manager.add_or_update_entries(["doc"], [{"source": "test"}], ["id"])

    kb_manager.client.delete_collection.assert_called_once_with(name="test_project_kb")
    assert old_collection.upsert.call_count == 2


def test_query_resets_collection_on_embedding_dimension_mismatch(kb_manager):
    old_collection = MagicMock()
    old_collection.name = "test_project_kb"
    old_collection.count.return_value = 1
    old_collection.query.side_effect = ValueError(
        "Collection expecting embedding with dimension of 768, got 3072"
    )
    kb_manager.collection = old_collection

    result = kb_manager.query("valid query")

    assert result == ""
    kb_manager.client.delete_collection.assert_called_once_with(name="test_project_kb")


def test_delete_entries_raises_when_chromadb_delete_fails(kb_manager):
    kb_manager.collection = MagicMock()
    kb_manager.collection.delete.side_effect = RuntimeError("ChromaDB is down")

    with pytest.raises(RuntimeError, match="ChromaDB is down"):
        kb_manager.delete_entries(["dict_term1"])


def test_delete_collection_raises_when_chromadb_delete_fails(kb_manager):
    kb_manager.collection = MagicMock()
    kb_manager.collection.name = "test_project_kb"
    kb_manager.client.delete_collection.side_effect = RuntimeError("ChromaDB is down")

    with pytest.raises(RuntimeError, match="ChromaDB is down"):
        kb_manager.delete_collection()


def test_genai_embedding_function_uses_google_genai_client():
    client = MagicMock()
    response = MagicMock()
    response.embeddings = [
        MagicMock(values=[0.1, 0.2]),
        MagicMock(values=[0.3, 0.4]),
    ]
    client.models.embed_content.return_value = response
    embedding_function = GenAIEmbeddingFunction(
        api_key="key",
        model_name="gemini-embedding-2",
        client=client,
    )

    result = embedding_function(["one", "two"])

    assert result == [[0.1, 0.2], [0.3, 0.4]]
    assert embedding_function.name() == "google_genai"
    assert GenAIEmbeddingFunction.name() == "google_genai"
    assert embedding_function.is_legacy() is False
    assert embedding_function.default_space() == "cosine"
    assert "cosine" in embedding_function.supported_spaces()
    assert embedding_function.get_config() == {"model_name": "gemini-embedding-2"}
    reconstructed = GenAIEmbeddingFunction.build_from_config(
        embedding_function.get_config()
    )
    assert reconstructed.model_name == "gemini-embedding-2"
    client.models.embed_content.assert_called_once()
    kwargs = client.models.embed_content.call_args.kwargs
    assert kwargs["model"] == "gemini-embedding-2"
    assert kwargs["contents"] == ["one", "two"]


def test_genai_embedding_function_reserves_usage_only_for_cache_misses(tmp_path):
    client = MagicMock()
    response = MagicMock()
    response.embeddings = [MagicMock(values=[0.1, 0.2])]
    client.models.embed_content.return_value = response
    usage_tracker = MagicMock()
    usage_tracker.reserve_call.return_value = "embedding-reservation"

    cache_path = tmp_path / "embedding_cache_usage.sqlite3"
    embedding_function = GenAIEmbeddingFunction(
        api_key="key",
        model_name="gemini-embedding-2",
        client=client,
        cache_path=str(cache_path),
        usage_tracker=usage_tracker,
    )

    assert embedding_function(["same text"]) == [[0.1, 0.2]]
    assert embedding_function(["same text"]) == [[0.1, 0.2]]

    usage_tracker.reserve_call.assert_called_once_with(
        "gemini-embedding-2",
        "embedContent",
    )
    usage_tracker.record_call.assert_called_once_with(
        "gemini-embedding-2",
        "embedContent",
        reservation_id="embedding-reservation",
    )
    usage_tracker.release_call.assert_not_called()
    usage_tracker.check_call_available.assert_not_called()
    client.models.embed_content.assert_called_once()


def test_genai_embedding_function_retries_temporary_embedding_errors():
    client = MagicMock()
    response = MagicMock()
    response.embeddings = [MagicMock(values=[0.1, 0.2])]
    client.models.embed_content.side_effect = [
        RuntimeError("503 UNAVAILABLE"),
        response,
    ]
    sleep = MagicMock()

    embedding_function = GenAIEmbeddingFunction(
        api_key="key",
        model_name="gemini-embedding-2",
        client=client,
        max_retries=2,
        initial_delay=3,
        sleep_func=sleep,
    )

    assert embedding_function(["same text"]) == [[0.1, 0.2]]
    assert client.models.embed_content.call_count == 2
    sleep.assert_called_once_with(3)


def test_genai_embedding_function_does_not_retry_quota_errors():
    client = MagicMock()
    client.models.embed_content.side_effect = RuntimeError("429 RESOURCE_EXHAUSTED quota")
    sleep = MagicMock()

    embedding_function = GenAIEmbeddingFunction(
        api_key="key",
        model_name="gemini-embedding-2",
        client=client,
        max_retries=2,
        initial_delay=3,
        sleep_func=sleep,
    )

    with pytest.raises(RuntimeError, match="RESOURCE_EXHAUSTED"):
        embedding_function(["same text"])

    assert client.models.embed_content.call_count == 1
    sleep.assert_not_called()


def test_genai_embedding_function_reuses_cached_document_embeddings(tmp_path):
    client = MagicMock()
    response = MagicMock()
    response.embeddings = [MagicMock(values=[0.1, 0.2])]
    client.models.embed_content.return_value = response

    cache_path = tmp_path / "embedding_cache_reuse.sqlite3"
    embedding_function = GenAIEmbeddingFunction(
        api_key="key",
        model_name="gemini-embedding-2",
        client=client,
        cache_path=str(cache_path),
    )

    assert embedding_function(["same text"]) == [[0.1, 0.2]]
    assert embedding_function(["same text"]) == [[0.1, 0.2]]

    client.models.embed_content.assert_called_once()


def test_genai_embedding_function_ignores_unavailable_cache_path(tmp_path):
    client = MagicMock()
    client.models.embed_content.return_value = MagicMock(
        embeddings=[MagicMock(values=[0.1, 0.2])]
    )
    unavailable_cache_path = tmp_path / "cache_dir"
    unavailable_cache_path.mkdir()
    embedding_function = GenAIEmbeddingFunction(
        api_key="key",
        model_name="gemini-embedding-2",
        client=client,
        cache_path=str(unavailable_cache_path),
    )

    assert embedding_function(["same text"]) == [[0.1, 0.2]]

    client.models.embed_content.assert_called_once()


def test_genai_embedding_function_returns_remote_embedding_when_cache_write_fails(
    monkeypatch, tmp_path
):
    client = MagicMock()
    client.models.embed_content.return_value = MagicMock(
        embeddings=[MagicMock(values=[0.1, 0.2])]
    )
    usage_tracker = MagicMock()
    usage_tracker.reserve_call.return_value = "embedding-reservation"
    embedding_function = GenAIEmbeddingFunction(
        api_key="key",
        model_name="gemini-embedding-2",
        client=client,
        cache_path=str(tmp_path / "embedding_cache.sqlite3"),
        usage_tracker=usage_tracker,
    )
    monkeypatch.setattr(
        embedding_function,
        "_write_cached_embeddings",
        MagicMock(side_effect=sqlite3.OperationalError("database is locked")),
    )

    assert embedding_function(["same text"]) == [[0.1, 0.2]]

    usage_tracker.record_call.assert_called_once_with(
        "gemini-embedding-2",
        "embedContent",
        reservation_id="embedding-reservation",
    )


def test_genai_embedding_function_keeps_query_and_document_cache_separate(tmp_path):
    client = MagicMock()
    client.models.embed_content.side_effect = [
        MagicMock(embeddings=[MagicMock(values=[0.1])]),
        MagicMock(embeddings=[MagicMock(values=[0.2])]),
    ]

    cache_path = tmp_path / "embedding_cache_tasks.sqlite3"
    embedding_function = GenAIEmbeddingFunction(
        api_key="key",
        model_name="gemini-embedding-2",
        client=client,
        cache_path=str(cache_path),
    )

    assert embedding_function(["same text"]) == [[0.1]]
    assert embedding_function.embed_query(["same text"]) == [[0.2]]
    assert embedding_function(["same text"]) == [[0.1]]
    assert embedding_function.embed_query(["same text"]) == [[0.2]]

    assert client.models.embed_content.call_count == 2


def test_genai_embedding_function_rejects_partial_embedding_responses(tmp_path):
    client = MagicMock()
    client.models.embed_content.return_value = MagicMock(
        embeddings=[MagicMock(values=[0.1])]
    )
    cache_path = tmp_path / "embedding_cache.sqlite3"
    embedding_function = GenAIEmbeddingFunction(
        api_key="key",
        model_name="gemini-embedding-2",
        client=client,
        cache_path=str(cache_path),
    )

    with pytest.raises(RuntimeError, match="1 embedding.*2 input"):
        embedding_function(["one", "two"])

    client.models.embed_content.return_value = MagicMock(
        embeddings=[MagicMock(values=[0.2]), MagicMock(values=[0.3])]
    )

    assert embedding_function(["one", "two"]) == [[0.2], [0.3]]
    assert client.models.embed_content.call_count == 2


def test_genai_embedding_function_rejects_empty_embedding_vectors(tmp_path):
    client = MagicMock()
    client.models.embed_content.return_value = MagicMock(
        embeddings=[MagicMock(values=[])]
    )
    embedding_function = GenAIEmbeddingFunction(
        api_key="key",
        model_name="gemini-embedding-2",
        client=client,
        cache_path=str(tmp_path / "embedding_cache.sqlite3"),
    )

    with pytest.raises(RuntimeError, match="empty embedding.*index 0"):
        embedding_function(["one"])


def test_genai_embedding_function_refreshes_invalid_cached_embeddings(tmp_path):
    client = MagicMock()
    client.models.embed_content.return_value = MagicMock(
        embeddings=[MagicMock(values=[0.4])]
    )
    cache_path = tmp_path / "embedding_cache.sqlite3"
    embedding_function = GenAIEmbeddingFunction(
        api_key="key",
        model_name="gemini-embedding-2",
        client=client,
        cache_path=str(cache_path),
    )
    embedding_function._ensure_cache_table()

    with sqlite3.connect(cache_path) as conn:
        conn.execute(
            """
            INSERT INTO embedding_cache
                (cache_key, model_name, task_type, embedding_json)
            VALUES (?, ?, ?, ?)
            """,
            (
                embedding_function._cache_key("one", "RETRIEVAL_DOCUMENT"),
                "gemini-embedding-2",
                "RETRIEVAL_DOCUMENT",
                "[]",
            ),
        )
        conn.commit()

    assert embedding_function(["one"]) == [[0.4]]
    client.models.embed_content.assert_called_once()

    with sqlite3.connect(cache_path) as conn:
        row = conn.execute(
            "SELECT embedding_json FROM embedding_cache WHERE cache_key = ?",
            (embedding_function._cache_key("one", "RETRIEVAL_DOCUMENT"),),
        ).fetchone()

    assert row[0] == "[0.4]"


def test_rebuild_index_from_db(kb_manager, mock_db_manager):
    kb_manager.collection = MagicMock()
    kb_manager.collection.get.return_value = {"ids": [], "metadatas": []}
    kb_manager._REBUILD_BATCH_DELAY_SECONDS = 0
    kb_manager.rebuild_index_from_db(mock_db_manager)
    assert kb_manager.collection.upsert.call_count > 0
    documents = kb_manager.collection.upsert.call_args.kwargs["documents"]
    assert any("Translation: термин1." in document for document in documents)


def test_rebuild_index_skips_unchanged_items(kb_manager, mock_db_manager):
    kb_manager.collection = MagicMock()
    kb_manager.collection.count.return_value = 2
    kb_manager._REBUILD_BATCH_DELAY_SECONDS = 0

    bible_text = (
        "Bible Entry. Category: N/A. Name: entry1 (Russian: N/A). "
        "Description: description1"
    )
    term_text = "Dictionary Term. Category: other. Term: term1. Translation: термин1."
    kb_manager.collection.get.return_value = {
        "ids": ["bible_entry1_chunk_0", "dict_term1"],
        "documents": [bible_text, term_text],
        "metadatas": [
            {"text_hash": kb_manager._text_hash(bible_text)},
            {"text_hash": kb_manager._text_hash(term_text)},
        ],
    }

    kb_manager.rebuild_index_from_db(mock_db_manager)

    kb_manager.collection.upsert.assert_not_called()


def test_rebuild_index_updates_changed_items_only(kb_manager, mock_db_manager):
    kb_manager.collection = MagicMock()
    kb_manager.collection.count.return_value = 2
    kb_manager.collection.get.return_value = {
        "ids": ["bible_entry1_chunk_0", "dict_term1"],
        "documents": ["old", "old"],
        "metadatas": [
            {"text_hash": "old"},
            {"text_hash": "old"},
        ],
    }
    kb_manager._REBUILD_BATCH_DELAY_SECONDS = 0

    kb_manager.rebuild_index_from_db(mock_db_manager)

    documents = kb_manager.collection.upsert.call_args.kwargs["documents"]
    assert len(documents) == 2


def test_rebuild_index_skips_legacy_items_when_document_text_matches(
    kb_manager, mock_db_manager
):
    kb_manager.collection = MagicMock()
    kb_manager.collection.count.return_value = 2
    kb_manager._REBUILD_BATCH_DELAY_SECONDS = 0

    bible_text = (
        "Bible Entry. Category: N/A. Name: entry1 (Russian: N/A). "
        "Description: description1"
    )
    term_text = "Dictionary Term. Category: other. Term: term1. Translation: термин1."
    kb_manager.collection.get.return_value = {
        "ids": ["bible_entry1_chunk_0", "dict_term1"],
        "documents": [bible_text, term_text],
        "metadatas": [{}, {}],
    }

    kb_manager.rebuild_index_from_db(mock_db_manager)

    kb_manager.collection.upsert.assert_not_called()


def test_rebuild_index_refreshes_stale_document_even_when_hash_matches(
    kb_manager, mock_db_manager
):
    kb_manager.collection = MagicMock()
    kb_manager.collection.count.return_value = 2
    kb_manager._REBUILD_BATCH_DELAY_SECONDS = 0

    bible_text = (
        "Bible Entry. Category: N/A. Name: entry1 (Russian: N/A). "
        "Description: description1"
    )
    term_text = "Dictionary Term. Category: other. Term: term1. Translation: термин1."
    kb_manager.collection.get.return_value = {
        "ids": ["bible_entry1_chunk_0", "dict_term1"],
        "documents": ["old bible document", "old term document"],
        "metadatas": [
            {"text_hash": kb_manager._text_hash(bible_text)},
            {"text_hash": kb_manager._text_hash(term_text)},
        ],
    }

    kb_manager.rebuild_index_from_db(mock_db_manager)

    documents = kb_manager.collection.upsert.call_args.kwargs["documents"]
    assert documents == [bible_text, term_text]


def test_rebuild_index_removes_stale_dictionary_and_bible_entries(
    kb_manager,
    mock_db_manager,
):
    kb_manager.collection = MagicMock()
    kb_manager.collection.count.return_value = 4
    kb_manager.collection.get.side_effect = [
        {
            "ids": ["dict_term1", "dict_removed"],
            "metadatas": [{"source": "dictionary"}, {"source": "dictionary"}],
        },
        {
            "ids": ["bible_entry1_chunk_0", "bible_removed_chunk_0"],
            "metadatas": [{"source": "bible"}, {"source": "bible"}],
        },
        {"ids": [], "documents": [], "metadatas": []},
    ]
    kb_manager._REBUILD_BATCH_DELAY_SECONDS = 0

    kb_manager.rebuild_index_from_db(mock_db_manager)

    kb_manager.collection.delete.assert_called_once_with(
        ids=["dict_removed", "bible_removed_chunk_0"]
    )


def test_rebuild_index_with_empty_db_preserves_non_rebuild_entries(kb_manager):
    db_manager = MagicMock()
    db_manager.get_terms_dictionary.return_value = {}
    db_manager.get_world_bible.return_value = {}
    kb_manager.collection = MagicMock()
    kb_manager.collection.name = "test_project_kb"
    kb_manager.collection.count.return_value = 3
    kb_manager.collection.get.side_effect = [
        {
            "ids": ["dict_removed"],
            "metadatas": [{"source": "dictionary"}],
        },
        {
            "ids": ["bible_removed_chunk_0"],
            "metadatas": [{"source": "bible"}],
        },
    ]

    kb_manager.rebuild_index_from_db(db_manager)

    kb_manager.collection.delete.assert_called_once_with(
        ids=["dict_removed", "bible_removed_chunk_0"]
    )
    kb_manager.client.delete_collection.assert_not_called()
    kb_manager.client.create_collection.assert_not_called()


def test_dimension_mismatch_recovery(kb_manager):
    mock_db = MagicMock()
    mock_db.get_terms_dictionary.return_value = {}
    mock_db.get_world_bible.return_value = {}
    
    kb_manager.db_manager = mock_db
    
    old_collection = MagicMock()
    old_collection.name = "test_project_kb"
    old_collection.upsert.side_effect = [
        ValueError("Collection expecting embedding with dimension of 768, got 3072"),
        None,
    ]
    
    kb_manager.collection = old_collection
    kb_manager.client.get_or_create_collection.return_value = old_collection
    
    kb_manager.add_or_update_entries(["doc"], [{"source": "test"}], ["id"])
    
    kb_manager.client.delete_collection.assert_called_once_with(name="test_project_kb")
    mock_db.get_terms_dictionary.assert_called_once()
