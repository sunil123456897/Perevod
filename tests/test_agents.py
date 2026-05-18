import pytest
from unittest.mock import MagicMock, patch

from Perevod.agents.reranker import Reranker


@pytest.fixture(scope="module")
def reranker():
    model = MagicMock()
    model.predict.return_value = [0.9, 0.4, 0.2]
    return Reranker(model=model)


def test_reranker_empty_list(reranker):
    assert reranker.rerank("query", []) == []
    reranker.model.predict.assert_not_called()


def test_reranker_sorting(reranker):
    query = "What is the capital of France?"
    documents = [
        {"text": "Paris is the capital of France."},
        {"text": "The Eiffel Tower is in Paris."},
        {"text": "France is a country in Europe."},
    ]
    reranked = reranker.rerank(query, documents)
    assert len(reranked) == 3
    assert reranked[0]["text"] == "Paris is the capital of France."


def test_reranker_reports_missing_optional_dependency():
    reranker = Reranker()

    with patch(
        "builtins.__import__",
        side_effect=ImportError("No module named 'sentence_transformers'"),
    ):
        with pytest.raises(RuntimeError, match="sentence-transformers"):
            reranker.rerank("query", [{"text": "document"}])
