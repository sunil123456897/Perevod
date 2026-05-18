import pytest
from unittest.mock import MagicMock, patch
import json
from Perevod.agents.nodes import summarization_node
from Perevod.agents.state import AgentState

@pytest.fixture
def mock_state():
    app_context = {
        "llm_provider": MagicMock(),
        "kb_manager": MagicMock(),
        "settings": MagicMock(),
        "db_manager": MagicMock()
    }
    state: AgentState = {
        "app_context": app_context,
        "project_name": "test_project",
        "chapters_to_process": [
            {"title": "The Beginning", "input_path": "input/ch1.txt", "output_path": "output/ch1.txt"}
        ],
        "processed_chapters": [
            {"title": "The Beginning", "input_path": "input/ch1.txt", "output_path": "output/ch1.txt"}
        ],
        "analysis_results": [],
        "unification_verdicts": [],
        "judge_results": [],
        "refinement_count": 0,
        "blocking_issues": [],
        "rag_context": "",
        "chapter_summaries": [],
        "summary_errors": [],
        "error": None,
        "progress_callback": None
    }
    return state

@patch("Perevod.agents.nodes.tool_read_chapter")
def test_summarization_node_success(mock_read, mock_state):
    # Setup mocks
    mock_read.return_value = "Translated text of chapter 1"
    
    mock_model = MagicMock()
    mock_state["app_context"]["llm_provider"].get_model.return_value = mock_model
    
    summary_data = {
        "title": "Chapter 1",
        "summary": "A great summary of chapter 1.",
        "key_events": ["Event 1", "Event 2"],
        "active_characters": ["Hero", "Villain"]
    }
    mock_response = MagicMock()
    mock_response.text = json.dumps(summary_data)
    mock_model.generate_content.return_value = mock_response
    
    # Mock existing entries in KB
    kb_manager = mock_state["app_context"]["kb_manager"]
    kb_manager.collection.get.return_value = {
        "metadatas": [{"type": "chapter_memory", "chapter_index": 5}]
    }
    
    # Execute
    result = summarization_node(mock_state)
    
    # Verify
    assert "chapter_summaries" in result
    assert len(result["chapter_summaries"]) == 1
    assert result["chapter_summaries"][0]["title"] == "Chapter 1"
    
    # Verify KB storage
    kb_manager.add_or_update_entries.assert_called_once()
    
    args, kwargs = kb_manager.add_or_update_entries.call_args
    documents = kwargs.get("documents") or args[0]
    metadatas = kwargs.get("metadatas") or args[1]
    ids = kwargs.get("ids") or args[2]
    
    assert "A great summary of chapter 1." in documents[0]
    assert "Event 1" in documents[0]
    assert metadatas[0]["type"] == "chapter_memory"
    assert metadatas[0]["chapter_index"] == 6  # 5 + 1
    assert metadatas[0]["title"] == "The Beginning"
    assert ids[0] == "memory_test_project_The Beginning"
    assert kwargs["embeddings"] == [[0.0] * 3072]


@patch("Perevod.agents.nodes.tool_read_chapter")
def test_summarization_node_ignores_missing_memory_metadata_when_numbering(
    mock_read, mock_state
):
    mock_read.return_value = "Translated text of next chapter"
    mock_model = MagicMock()
    mock_state["app_context"]["llm_provider"].get_model.return_value = mock_model
    mock_model.generate_content.return_value = MagicMock(
        text=json.dumps(
            {
                "title": "Next Chapter",
                "summary": "A great summary.",
                "key_events": ["Event"],
                "active_characters": ["Hero"],
            }
        )
    )
    kb_manager = mock_state["app_context"]["kb_manager"]
    kb_manager.collection.get.return_value = {
        "metadatas": [
            None,
            {"type": "chapter_memory", "chapter_index": 5},
        ]
    }

    result = summarization_node(mock_state)

    assert result["summary_errors"] == []
    metadatas = kb_manager.add_or_update_entries.call_args.kwargs["metadatas"]
    assert metadatas[0]["chapter_index"] == 6


@patch("Perevod.agents.nodes.tool_read_chapter")
def test_summarization_node_preserves_zero_chapter_index(mock_read, mock_state):
    mock_read.return_value = "Translated prologue"
    mock_state["processed_chapters"] = [
        {
            "title": "Chapter 0",
            "input_path": "input/ch0.txt",
            "output_path": "output/ch0.txt",
        }
    ]

    mock_model = MagicMock()
    mock_state["app_context"]["llm_provider"].get_model.return_value = mock_model
    mock_model.generate_content.return_value = MagicMock(
        text=json.dumps(
            {
                "title": "Chapter 0",
                "summary": "A prologue summary.",
                "key_events": ["prologue"],
                "active_characters": ["Hero"],
            }
        )
    )
    kb_manager = mock_state["app_context"]["kb_manager"]
    kb_manager.collection.get.return_value = {
        "metadatas": [{"type": "chapter_memory", "chapter_index": 5}]
    }

    result = summarization_node(mock_state)

    assert result["summary_errors"] == []
    metadatas = kb_manager.add_or_update_entries.call_args.kwargs["metadatas"]
    assert metadatas[0]["chapter_index"] == 0


def test_summarization_node_empty_processed_chapters(mock_state):
    mock_state["processed_chapters"] = []
    result = summarization_node(mock_state)
    assert result == {"chapter_summaries": [], "summary_errors": []}


def test_summarization_node_preserves_existing_workflow_error(mock_state):
    mock_state["error"] = "translation failed"
    result = summarization_node(mock_state)
    assert result == {"error": "translation failed"}


@patch("Perevod.agents.nodes.tool_read_chapter")
def test_summarization_node_reports_empty_translation_as_warning(mock_read, mock_state):
    mock_read.return_value = "   "
    mock_model = MagicMock()
    mock_state["app_context"]["llm_provider"].get_model.return_value = mock_model
    mock_state["app_context"]["kb_manager"].collection.get.return_value = {
        "metadatas": []
    }

    result = summarization_node(mock_state)

    assert result["chapter_summaries"] == []
    assert result["summary_errors"] == [
        {
            "title": "The Beginning",
            "error": "Translated output is empty; chapter memory was not updated.",
        }
    ]
    mock_model.generate_content.assert_not_called()
    mock_state["app_context"]["kb_manager"].add_or_update_entries.assert_not_called()


@patch("Perevod.agents.nodes.tool_read_chapter")
def test_summarization_node_uses_chapter_number_from_title(mock_read, mock_state):
    mock_read.return_value = "Translated text"
    mock_model = MagicMock()
    mock_state["app_context"]["llm_provider"].get_model.return_value = mock_model
    mock_model.generate_content.return_value = MagicMock(
        text=json.dumps(
            {
                "title": "Chapter 585",
                "summary": "Summary.",
                "key_events": [],
                "active_characters": [],
            }
        )
    )
    mock_state["processed_chapters"] = [
        {
            "title": "Chapter 585 The Ghost-Like Enemy Named Thunder",
            "input_path": "input/ch585.txt",
            "output_path": "output/ch585.txt",
        }
    ]
    kb_manager = mock_state["app_context"]["kb_manager"]
    kb_manager.collection.get.return_value = {"metadatas": []}

    summarization_node(mock_state)

    _, kwargs = kb_manager.add_or_update_entries.call_args
    assert kwargs["metadatas"][0]["chapter_index"] == 585


@patch("Perevod.agents.nodes.tool_read_chapter")
def test_summarization_node_reports_non_fatal_summary_errors(mock_read, mock_state):
    mock_read.return_value = "Translated text"
    mock_model = MagicMock()
    mock_state["app_context"]["llm_provider"].get_model.return_value = mock_model
    mock_model.generate_content.return_value = MagicMock(text="{invalid json")
    mock_state["app_context"]["kb_manager"].collection.get.return_value = {
        "metadatas": []
    }

    result = summarization_node(mock_state)

    assert result["chapter_summaries"] == []
    assert result["summary_errors"][0]["title"] == "The Beginning"
