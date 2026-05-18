from unittest.mock import MagicMock, patch
from Perevod.agents.nodes import refine_node
from Perevod.agents.state import AgentState

def test_refine_node_success():
    # Setup mock state
    mock_llm_provider = MagicMock()
    mock_model = MagicMock()
    mock_llm_provider.get_model.return_value = mock_model
    mock_db = MagicMock()
    mock_kb = MagicMock()
    
    # Mock LLM response (Editor returns full text)
    mock_response = MagicMock()
    mock_response.text = "Corrected Russian text"
    mock_model.generate_content.return_value = mock_response
    
    state: AgentState = {
        "app_context": {
            "llm_provider": mock_llm_provider,
            "db_manager": mock_db,
            "kb_manager": mock_kb,
            "settings": MagicMock()
        },
        "processed_chapters": [
            {
                "title": "Chapter 1", 
                "input_path": "input/ch1.txt", 
                "output_path": "output/ch1.txt", 
                "relevant_context": "Some context",
                "blocking_issues": ["Omission in para 2"],
                "cache_key": "key1"
            }
        ],
        "unification_verdicts": [],
        "judge_results": [],
        "blocking_issues": ["Omission in para 2"],
        "refinement_count": 0,
        "error": None,
        "progress_callback": MagicMock()
    }
    
    with patch("Perevod.agents.nodes.tool_read_chapter") as mock_read, \
         patch("Perevod.agents.nodes.tool_write_chapter") as mock_write, \
         patch("Perevod.agents.nodes.clean_translation_output") as mock_clean:
        
        mock_read.side_effect = ["English text", "Original Russian text"]
        mock_clean.return_value = "Corrected Russian text"
        
        result = refine_node(state)
        
        # Verify refinement_count increment
        assert result["refinement_count"] == 1
        
        # Verify write back
        mock_write.assert_called_once_with("output/ch1.txt", "Corrected Russian text")
        
        # Verify cache update
        mock_db.add_to_cache.assert_called_once_with("key1", "Corrected Russian text")
        
        # Verify KB update
        mock_kb.add_or_update_entries.assert_called_once()
        
        # Verify progress reporting
        state["progress_callback"].assert_called()

def test_refine_node_no_issues():
    state: AgentState = {
        "blocking_issues": [],
        "refinement_count": 0
    }
    # Should return early
    result = refine_node(state)
    assert result == {}

def test_refine_node_multi_chapter_selective():
    # Setup mock state
    mock_llm_provider = MagicMock()
    mock_model = MagicMock()
    mock_llm_provider.get_model.return_value = mock_model
    mock_db = MagicMock()
    mock_kb = MagicMock()
    
    mock_response = MagicMock()
    mock_response.text = "Corrected R2"
    mock_model.generate_content.return_value = mock_response
    
    state: AgentState = {
        "app_context": {
            "llm_provider": mock_llm_provider,
            "db_manager": mock_db,
            "kb_manager": mock_kb,
            "settings": MagicMock()
        },
        "processed_chapters": [
            {"title": "Ch 1", "input_path": "in1.txt", "output_path": "out1.txt", "blocking_issues": []},
            {"title": "Ch 2", "input_path": "in2.txt", "output_path": "out2.txt", "blocking_issues": ["Issue"], "cache_key": "k2"}
        ],
        "blocking_issues": ["Issue"],
        "refinement_count": 0,
        "progress_callback": MagicMock()
    }
    
    with patch("Perevod.agents.nodes.tool_read_chapter") as mock_read, \
         patch("Perevod.agents.nodes.tool_write_chapter") as mock_write, \
         patch("Perevod.agents.nodes.clean_translation_output") as mock_clean:
        
        mock_read.side_effect = ["E2", "R2"]
        mock_clean.return_value = "Corrected R2"
        
        result = refine_node(state)
        
        assert result["refinement_count"] == 1
        # Only Ch 2 should be processed
        assert mock_model.generate_content.call_count == 1
        mock_write.assert_called_once_with("out2.txt", "Corrected R2")
        mock_db.add_to_cache.assert_called_once_with("k2", "Corrected R2")


def test_refine_node_keeps_success_when_kb_correction_memory_fails():
    mock_llm_provider = MagicMock()
    mock_model = MagicMock()
    mock_llm_provider.get_model.return_value = mock_model
    mock_model.generate_content.return_value = MagicMock(text="Corrected Russian text")
    mock_db = MagicMock()
    mock_kb = MagicMock()
    mock_kb.add_or_update_entries.side_effect = RuntimeError("ChromaDB unavailable")

    state: AgentState = {
        "app_context": {
            "llm_provider": mock_llm_provider,
            "db_manager": mock_db,
            "kb_manager": mock_kb,
            "settings": MagicMock(),
        },
        "processed_chapters": [
            {
                "title": "Ch 2",
                "input_path": "in2.txt",
                "output_path": "out2.txt",
                "blocking_issues": ["Issue"],
                "cache_key": "k2",
            }
        ],
        "blocking_issues": ["Issue"],
        "refinement_count": 0,
        "progress_callback": MagicMock(),
    }

    with patch("Perevod.agents.nodes.tool_read_chapter") as mock_read, \
         patch("Perevod.agents.nodes.tool_write_chapter") as mock_write, \
         patch("Perevod.agents.nodes.clean_translation_output") as mock_clean:
        mock_read.side_effect = ["E2", "Original R2"]
        mock_clean.return_value = "Corrected Russian text"

        result = refine_node(state)

    assert result == {"refinement_count": 1}
    mock_write.assert_called_once_with("out2.txt", "Corrected Russian text")
    mock_db.add_to_cache.assert_called_once_with("k2", "Corrected Russian text")
    mock_kb.add_or_update_entries.assert_called_once()


def test_refine_node_keeps_success_when_cache_update_fails():
    mock_llm_provider = MagicMock()
    mock_model = MagicMock()
    mock_llm_provider.get_model.return_value = mock_model
    mock_model.generate_content.return_value = MagicMock(text="Corrected Russian text")
    mock_db = MagicMock()
    mock_db.add_to_cache.side_effect = RuntimeError("SQLite is locked")
    mock_kb = MagicMock()

    state: AgentState = {
        "app_context": {
            "llm_provider": mock_llm_provider,
            "db_manager": mock_db,
            "kb_manager": mock_kb,
            "settings": MagicMock(),
        },
        "processed_chapters": [
            {
                "title": "Ch 2",
                "input_path": "in2.txt",
                "output_path": "out2.txt",
                "blocking_issues": ["Issue"],
                "cache_key": "k2",
            }
        ],
        "blocking_issues": ["Issue"],
        "refinement_count": 0,
        "progress_callback": MagicMock(),
    }

    with patch("Perevod.agents.nodes.tool_read_chapter") as mock_read, \
         patch("Perevod.agents.nodes.tool_write_chapter") as mock_write, \
         patch("Perevod.agents.nodes.clean_translation_output") as mock_clean:
        mock_read.side_effect = ["E2", "Original R2"]
        mock_clean.return_value = "Corrected Russian text"

        result = refine_node(state)

    assert result == {"refinement_count": 1}
    mock_write.assert_called_once_with("out2.txt", "Corrected Russian text")
    mock_db.add_to_cache.assert_called_once_with("k2", "Corrected Russian text")
    mock_kb.add_or_update_entries.assert_called_once()


def test_refine_node_reports_empty_editor_correction_as_error():
    mock_llm_provider = MagicMock()
    mock_model = MagicMock()
    mock_llm_provider.get_model.return_value = mock_model
    mock_model.generate_content.return_value = MagicMock(text="   ")

    state: AgentState = {
        "app_context": {
            "llm_provider": mock_llm_provider,
            "db_manager": MagicMock(),
            "kb_manager": MagicMock(),
            "settings": MagicMock(),
        },
        "processed_chapters": [
            {
                "title": "Ch 2",
                "input_path": "in2.txt",
                "output_path": "out2.txt",
                "blocking_issues": ["Issue"],
                "cache_key": "k2",
            }
        ],
        "blocking_issues": ["Issue"],
        "refinement_count": 0,
        "progress_callback": MagicMock(),
    }

    with patch("Perevod.agents.nodes.tool_read_chapter") as mock_read, \
         patch("Perevod.agents.nodes.tool_write_chapter") as mock_write:
        mock_read.side_effect = ["E2", "R2"]

        result = refine_node(state)

    assert result["refinement_count"] == 1
    assert result["error"].startswith("Ошибка Редактора для главы 'Ch 2'")
    assert "пустую правку" in result["error"]
    mock_write.assert_not_called()
