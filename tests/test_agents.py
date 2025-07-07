# tests/test_agents.py

import pytest
from Perevod.agents.semantic_chunker import SemanticChunker
from agents.reranker import Reranker

@pytest.fixture(scope="module")
def chunker():
    return SemanticChunker(max_chunk_size=150)

@pytest.fixture(scope="module")
def reranker():
    return Reranker()

def test_chunker_empty_text(chunker):
    assert chunker.chunk("") == []
    assert chunker.chunk("   ") == []

def test_chunker_small_text(chunker):
    text = "This is a short sentence."
    assert chunker.chunk(text) == [text]

def test_chunker_long_paragraph(chunker):
    text = "This is a long sentence that should be split into multiple chunks based on the max_chunk_size parameter. It has several sentences. This is the second one. And this is the third one, just to be sure."
    chunks = chunker.chunk(text)
    assert len(chunks) > 1
    for chunk in chunks:
        assert len(chunk) <= 150

def test_chunker_multiple_paragraphs(chunker):
    text = "First paragraph.\n\nSecond paragraph, which is also short."
    chunks = chunker.chunk(text);
    assert len(chunks) == 2
    assert chunks[0] == "First paragraph."
    assert chunks[1] == "Second paragraph, which is also short."

def test_reranker_empty_list(reranker):
    assert reranker.rerank("query", []) == []

def test_reranker_sorting(reranker):
    query = "What is the capital of France?"
    documents = [
        {'text': 'Paris is the capital of France.'},
        {'text': 'The Eiffel Tower is in Paris.'},
        {'text': 'France is a country in Europe.'}
    ]
    reranked = reranker.rerank(query, documents);
    assert len(reranked) == 3
    assert reranked[0]['text'] == 'Paris is the capital of France.'
