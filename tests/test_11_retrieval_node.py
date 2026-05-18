# tests/test_11_retrieval_node.py
from unittest.mock import MagicMock, patch
from Perevod.agents.nodes import context_retrieval_node

def test_context_retrieval_node_aggregates_lore_and_memory():
    # Setup
    mock_kb_manager = MagicMock()
    mock_collection = MagicMock()
    mock_kb_manager.collection = mock_collection
    mock_kb_manager.reranker = None
    
    # 1. Mock Lore (via collection.query)
    # We return a mix of entries, including chapter_memory which should be filtered out
    mock_collection.count.return_value = 100
    mock_collection.query.return_value = {
        "documents": [["Lore entry 1", "Chapter Memory to skip", "Lore entry 2"]],
        "metadatas": [[
            {"source": "bible", "name": "Term 1"},
            {"type": "chapter_memory", "chapter_index": 0},
            {"source": "dictionary", "name": "Term 2"}
        ]],
        "ids": [["id1", "id2", "id3"]]
    }
    
    # 2. Mock Chapter Memory (via collection.get)
    # We return them unsorted to test if the node sorts them.
    mock_collection.get.return_value = {
        "documents": ["Summary 2", "Summary 1"],
        "metadatas": [
            {"type": "chapter_memory", "chapter_index": 1, "title": "Ch 2"},
            {"type": "chapter_memory", "chapter_index": 0, "title": "Ch 1"}
        ]
    }
    
    state = {
        "app_context": {
            "kb_manager": mock_kb_manager
        },
        "chapters_to_process": [
            {"title": "Ch 3", "input_path": "input/ch3.txt"}
        ],
        "rag_context": "",
        "chapter_summaries": []
    }
    
    # Mock tool_read_chapter to avoid file system access
    with patch("Perevod.agents.nodes.tool_read_chapter", return_value="Chapter 3 content"):
        # Execute
        result = context_retrieval_node(state)
    
    # Verify Lore was queried via collection.query
    mock_collection.query.assert_called_once()
    
    # Verify result structure
    assert "rag_context" in result
    rag_context = result["rag_context"]
    
    assert "=== WORLD BIBLE & LORE ===" in rag_context
    assert "Lore entry 1" in rag_context
    assert "Lore entry 2" in rag_context
    assert "Chapter Memory to skip" not in rag_context
    
    assert "=== RECENT PLOT DEVELOPMENTS (PAST CHAPTERS) ===" in rag_context
    # Summaries should be sorted by chapter_index (0, 1)
    assert "Summary 1" in rag_context
    assert "Summary 2" in rag_context
    
    # Verify chapter_summaries list
    assert "chapter_summaries" in result
    summaries = result["chapter_summaries"]
    assert len(summaries) == 2
    assert summaries[0]["chapter_index"] == 0
    assert summaries[1]["chapter_index"] == 1
    assert result["context_errors"] == []

def test_context_retrieval_node_handles_empty_kb():
    # Setup
    mock_kb_manager = MagicMock()
    mock_collection = MagicMock()
    mock_kb_manager.collection = mock_collection
    
    mock_kb_manager.query.return_value = ""
    mock_collection.count.return_value = 0
    mock_collection.get.return_value = {"documents": [], "metadatas": []}
    
    state = {
        "app_context": {
            "kb_manager": mock_kb_manager
        },
        "chapters_to_process": [{"title": "Ch 1", "input_path": "input/ch1.txt"}],
        "rag_context": "",
        "chapter_summaries": []
    }
    
    with patch("Perevod.agents.nodes.tool_read_chapter", return_value="Ch 1 text"):
        result = context_retrieval_node(state)
    
    assert "rag_context" in result
    assert "No relevant lore found" in result["rag_context"]
    assert "No previous chapter memory found" in result["rag_context"]
    assert result["chapter_summaries"] == []
    assert result["context_errors"] == []


def test_context_retrieval_node_uses_lexical_fallback_when_semantic_query_fails():
    mock_kb_manager = MagicMock()
    mock_collection = MagicMock()
    mock_kb_manager.collection = mock_collection
    mock_kb_manager.reranker = None

    mock_collection.count.return_value = 3
    mock_collection.query.side_effect = RuntimeError("embedding quota exhausted")
    mock_collection.get.side_effect = [
        {
            "documents": [
                "Thunder Lotus seed feeds the strange thunder spirit.",
                "Unrelated market notes.",
                "Old chapter memory.",
            ],
            "metadatas": [
                {"source": "bible", "name": "Thunder Lotus"},
                {"source": "dictionary", "name": "Market"},
                {"type": "chapter_memory", "chapter_index": 584},
            ],
            "ids": ["lore1", "lore2", "memory1"],
        },
        {"documents": [], "metadatas": []},
    ]

    state = {
        "app_context": {"kb_manager": mock_kb_manager},
        "chapters_to_process": [
            {"title": "Ch 585", "input_path": "input/ch585.txt"}
        ],
        "rag_context": "",
        "chapter_summaries": [],
    }

    with patch(
        "Perevod.agents.nodes.tool_read_chapter",
        return_value="Lu Xuan offers a Thunder Lotus seed to a thunder spirit.",
    ):
        result = context_retrieval_node(state)

    rag_context = result["rag_context"]
    assert "Thunder Lotus seed feeds the strange thunder spirit." in rag_context
    assert "Old chapter memory" not in rag_context
    assert result["context_errors"] == []


def test_context_retrieval_node_keeps_lore_with_missing_metadata():
    mock_kb_manager = MagicMock()
    mock_collection = MagicMock()
    mock_kb_manager.collection = mock_collection
    mock_kb_manager.reranker = None

    mock_collection.count.return_value = 1
    mock_collection.query.return_value = {
        "documents": [["Lore without metadata"]],
        "metadatas": [[None]],
        "ids": [["lore1"]],
    }
    mock_collection.get.return_value = {"documents": [], "metadatas": []}

    state = {
        "app_context": {"kb_manager": mock_kb_manager},
        "chapters_to_process": [
            {"title": "Ch 5", "input_path": "input/ch5.txt"}
        ],
        "rag_context": "",
        "chapter_summaries": [],
    }

    with patch("Perevod.agents.nodes.tool_read_chapter", return_value="Chapter text"):
        result = context_retrieval_node(state)

    assert "Lore without metadata" in result["rag_context"]
    assert result["context_errors"] == []


def test_context_retrieval_node_keeps_memory_with_missing_metadata():
    mock_kb_manager = MagicMock()
    mock_collection = MagicMock()
    mock_kb_manager.collection = mock_collection
    mock_kb_manager.reranker = None

    mock_collection.count.return_value = 0
    mock_collection.get.return_value = {
        "documents": ["Summary without metadata"],
        "metadatas": [None],
    }

    state = {
        "app_context": {"kb_manager": mock_kb_manager},
        "chapters_to_process": [
            {"title": "Ch 5", "input_path": "input/ch5.txt"}
        ],
        "rag_context": "",
        "chapter_summaries": [],
    }

    with patch("Perevod.agents.nodes.tool_read_chapter", return_value="Chapter text"):
        result = context_retrieval_node(state)

    assert "Summary without metadata" in result["rag_context"]
    assert result["chapter_summaries"] == [
        {
            "content": "Summary without metadata",
            "chapter_index": 0,
            "title": "Unknown",
        }
    ]
    assert result["context_errors"] == []


def test_context_retrieval_node_excludes_current_and_future_chapter_memory():
    mock_kb_manager = MagicMock()
    mock_collection = MagicMock()
    mock_kb_manager.collection = mock_collection
    mock_kb_manager.reranker = None

    mock_collection.count.return_value = 1
    mock_collection.query.return_value = {
        "documents": [["Lore entry"]],
        "metadatas": [[{"source": "bible", "name": "Lore"}]],
        "ids": [["lore1"]],
    }
    mock_collection.get.return_value = {
        "documents": [
            "Summary 1",
            "Summary 2",
            "Summary 3 should not leak",
            "Summary 4 should not leak",
        ],
        "metadatas": [
            {"type": "chapter_memory", "chapter_index": 1, "title": "Chapter 1"},
            {"type": "chapter_memory", "chapter_index": 2, "title": "Chapter 2"},
            {"type": "chapter_memory", "chapter_index": 3, "title": "Chapter 3"},
            {"type": "chapter_memory", "chapter_index": 4, "title": "Chapter 4"},
        ],
    }
    state = {
        "app_context": {"kb_manager": mock_kb_manager},
        "chapters_to_process": [
            {"title": "Chapter 3", "input_path": "input/ch3.txt"}
        ],
        "rag_context": "",
        "chapter_summaries": [],
    }

    with patch("Perevod.agents.nodes.tool_read_chapter", return_value="Chapter text"):
        result = context_retrieval_node(state)

    rag_context = result["rag_context"]
    assert "Summary 1" in rag_context
    assert "Summary 2" in rag_context
    assert "Summary 3 should not leak" not in rag_context
    assert "Summary 4 should not leak" not in rag_context
    assert [summary["chapter_index"] for summary in result["chapter_summaries"]] == [1, 2]
    assert result["context_errors"] == []


def test_context_retrieval_node_reports_memory_errors():
    mock_kb_manager = MagicMock()
    mock_collection = MagicMock()
    mock_kb_manager.collection = mock_collection
    mock_kb_manager.reranker = None

    mock_collection.count.return_value = 0
    mock_collection.get.side_effect = RuntimeError("memory db locked")

    state = {
        "app_context": {"kb_manager": mock_kb_manager},
        "chapters_to_process": [
            {"title": "Ch 586", "input_path": "input/ch586.txt"}
        ],
        "rag_context": "",
        "chapter_summaries": [],
    }

    with patch("Perevod.agents.nodes.tool_read_chapter", return_value="Chapter text"):
        result = context_retrieval_node(state)

    assert "Error retrieving chapter memory." in result["rag_context"]
    assert result["context_errors"] == [
        {
            "title": "*",
            "scope": "chapter_memory",
            "error": "memory db locked",
        }
    ]
