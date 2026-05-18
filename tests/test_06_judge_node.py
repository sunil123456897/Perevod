from unittest.mock import MagicMock, patch
from Perevod.agents.nodes import judge_node
from Perevod.agents.state import AgentState

def test_judge_node_success():
    # Setup mock state
    mock_llm_provider = MagicMock()
    mock_model = MagicMock()
    mock_llm_provider.get_model.return_value = mock_model
    
    # Mock LLM response
    mock_response = MagicMock()
    mock_response.text = '{"pass_check": true, "severity": "low", "blocking_issues": [], "suggestions": ["Good job"], "score": 9}'
    mock_model.generate_content.return_value = mock_response
    
    state: AgentState = {
        "app_context": {
            "llm_provider": mock_llm_provider,
            "db_manager": MagicMock(),
            "kb_manager": MagicMock(),
            "settings": MagicMock()
        },
        "chapters_to_process": [
            {"title": "Chapter 1", "input_path": "input/ch1.txt", "output_path": "output/ch1.txt"}
        ],
        "processed_chapters": [
            {"title": "Chapter 1", "input_path": "input/ch1.txt", "output_path": "output/ch1.txt", "relevant_context": "Some context"}
        ],
        "unification_verdicts": [],
        "judge_results": [],
        "blocking_issues": [],
        "refinement_count": 0,
        "error": None,
        "progress_callback": None
    }
    
    with patch("Perevod.agents.nodes.tool_read_chapter") as mock_read:
        mock_read.side_effect = ["English text", "Russian text"]
        
        result = judge_node(state)
        
        assert "judge_results" in result
        assert len(result["judge_results"]) == 1
        assert result["judge_results"][0]["pass_check"] is True
        assert result["judge_results"][0]["score"] == 9
        assert result["blocking_issues"] == []
        assert result["processed_chapters"][0]["blocking_issues"] == []

def test_judge_node_blocking_issues():
    # Setup mock state
    mock_llm_provider = MagicMock()
    mock_model = MagicMock()
    mock_llm_provider.get_model.return_value = mock_model
    
    # Mock LLM response
    mock_response = MagicMock()
    mock_response.text = '{"pass_check": false, "severity": "high", "blocking_issues": ["Omission in para 2"], "suggestions": [], "score": 4}'
    mock_model.generate_content.return_value = mock_response
    
    state: AgentState = {
        "app_context": {
            "llm_provider": mock_llm_provider,
            "db_manager": MagicMock(),
            "kb_manager": MagicMock(),
            "settings": MagicMock()
        },
        "chapters_to_process": [
            {"title": "Chapter 1", "input_path": "input/ch1.txt", "output_path": "output/ch1.txt"}
        ],
        "processed_chapters": [
            {"title": "Chapter 1", "input_path": "input/ch1.txt", "output_path": "output/ch1.txt", "relevant_context": "Some context"}
        ],
        "unification_verdicts": [],
        "judge_results": [],
        "blocking_issues": [],
        "refinement_count": 0,
        "error": None,
        "progress_callback": None
    }
    
    with patch("Perevod.agents.nodes.tool_read_chapter") as mock_read:
        mock_read.side_effect = ["English text", "Russian text"]
        
        result = judge_node(state)
        
        assert result["judge_results"][0]["pass_check"] is False
        assert "Omission in para 2" in result["blocking_issues"]
        assert result["processed_chapters"][0]["blocking_issues"] == ["Omission in para 2"]


def test_judge_node_caches_passing_api_translation():
    mock_llm_provider = MagicMock()
    mock_model = MagicMock()
    mock_llm_provider.get_model.return_value = mock_model
    mock_response = MagicMock()
    mock_response.text = '{"pass_check": true, "severity": "low", "blocking_issues": [], "suggestions": [], "score": 9}'
    mock_model.generate_content.return_value = mock_response
    db_manager = MagicMock()

    state: AgentState = {
        "app_context": {
            "llm_provider": mock_llm_provider,
            "db_manager": db_manager,
            "kb_manager": MagicMock(),
            "settings": MagicMock(),
        },
        "processed_chapters": [
            {
                "title": "Ch 1",
                "input_path": "in1.txt",
                "output_path": "out1.txt",
                "cache_key": "cache-key",
                "translation_source": "api",
            }
        ],
        "unification_verdicts": [],
        "judge_results": [],
        "blocking_issues": [],
        "refinement_count": 0,
        "error": None,
        "progress_callback": None,
    }

    with patch("Perevod.agents.nodes.tool_read_chapter") as mock_read:
        mock_read.side_effect = ["English text.", "Русский текст."]

        result = judge_node(state)

    assert result["blocking_issues"] == []
    db_manager.add_to_cache.assert_called_once_with("cache-key", "Русский текст.")


def test_judge_node_does_not_cache_blocked_api_translation():
    mock_llm_provider = MagicMock()
    mock_model = MagicMock()
    mock_llm_provider.get_model.return_value = mock_model
    mock_response = MagicMock()
    mock_response.text = '{"pass_check": false, "severity": "high", "blocking_issues": ["Wrong term"], "suggestions": [], "score": 4}'
    mock_model.generate_content.return_value = mock_response
    db_manager = MagicMock()

    state: AgentState = {
        "app_context": {
            "llm_provider": mock_llm_provider,
            "db_manager": db_manager,
            "kb_manager": MagicMock(),
            "settings": MagicMock(),
        },
        "processed_chapters": [
            {
                "title": "Ch 1",
                "input_path": "in1.txt",
                "output_path": "out1.txt",
                "cache_key": "cache-key",
                "translation_source": "api",
            }
        ],
        "unification_verdicts": [],
        "judge_results": [],
        "blocking_issues": [],
        "refinement_count": 0,
        "error": None,
        "progress_callback": None,
    }

    with patch("Perevod.agents.nodes.tool_read_chapter") as mock_read:
        mock_read.side_effect = ["English text.", "Русский текст."]

        result = judge_node(state)

    assert result["blocking_issues"] == ["Wrong term"]
    db_manager.add_to_cache.assert_not_called()


def test_judge_node_invalidates_blocked_cached_translation():
    mock_llm_provider = MagicMock()
    mock_model = MagicMock()
    mock_llm_provider.get_model.return_value = mock_model
    mock_response = MagicMock()
    mock_response.text = '{"pass_check": false, "severity": "high", "blocking_issues": ["Wrong term"], "suggestions": [], "score": 4}'
    mock_model.generate_content.return_value = mock_response
    db_manager = MagicMock()

    state: AgentState = {
        "app_context": {
            "llm_provider": mock_llm_provider,
            "db_manager": db_manager,
            "kb_manager": MagicMock(),
            "settings": MagicMock(),
        },
        "processed_chapters": [
            {
                "title": "Ch 1",
                "input_path": "in1.txt",
                "output_path": "out1.txt",
                "cache_key": "cache-key",
                "translation_source": "cache",
            }
        ],
        "unification_verdicts": [],
        "judge_results": [],
        "blocking_issues": [],
        "refinement_count": 0,
        "error": None,
        "progress_callback": None,
    }

    with patch("Perevod.agents.nodes.tool_read_chapter") as mock_read:
        mock_read.side_effect = ["English text.", "Русский текст."]

        result = judge_node(state)

    assert result["blocking_issues"] == ["Wrong term"]
    db_manager.add_to_cache.assert_not_called()
    db_manager.delete_from_cache.assert_called_once_with("cache-key")


def test_judge_node_multi_chapter():
    # Setup mock state
    mock_llm_provider = MagicMock()
    mock_model = MagicMock()
    mock_llm_provider.get_model.return_value = mock_model
    
    # Mock LLM responses: Chapter 1 passes, Chapter 2 fails
    mock_response_pass = MagicMock()
    mock_response_pass.text = '{"pass_check": true, "severity": "low", "blocking_issues": [], "suggestions": [], "score": 10}'
    
    mock_response_fail = MagicMock()
    mock_response_fail.text = '{"pass_check": false, "severity": "medium", "blocking_issues": ["Wrong name"], "suggestions": [], "score": 6}'
    
    mock_model.generate_content.side_effect = [mock_response_pass, mock_response_fail]
    
    state: AgentState = {
        "app_context": {
            "llm_provider": mock_llm_provider,
            "db_manager": MagicMock(),
            "kb_manager": MagicMock(),
            "settings": MagicMock()
        },
        "processed_chapters": [
            {"title": "Ch 1", "input_path": "in1.txt", "output_path": "out1.txt"},
            {"title": "Ch 2", "input_path": "in2.txt", "output_path": "out2.txt"}
        ],
        "unification_verdicts": [],
        "judge_results": [],
        "blocking_issues": [],
        "refinement_count": 0,
        "error": None,
        "progress_callback": None
    }
    
    with patch("Perevod.agents.nodes.tool_read_chapter") as mock_read:
        mock_read.side_effect = ["E1", "R1", "E2", "R2"]
        
        result = judge_node(state)
        
        assert result["processed_chapters"][0]["blocking_issues"] == []
        assert result["processed_chapters"][1]["blocking_issues"] == ["Wrong name"]
        assert result["blocking_issues"] == ["Wrong name"]


def test_judge_node_deduplicates_blocking_issues_without_reordering():
    mock_llm_provider = MagicMock()
    mock_model = MagicMock()
    mock_llm_provider.get_model.return_value = mock_model

    first_response = MagicMock()
    first_response.text = (
        '{"pass_check": false, "severity": "high", '
        '"blocking_issues": ["zeta omission", "alpha term"], '
        '"suggestions": [], "score": 4}'
    )
    second_response = MagicMock()
    second_response.text = (
        '{"pass_check": false, "severity": "high", '
        '"blocking_issues": ["zeta omission", "middle style"], '
        '"suggestions": [], "score": 4}'
    )
    mock_model.generate_content.side_effect = [first_response, second_response]

    state: AgentState = {
        "app_context": {
            "llm_provider": mock_llm_provider,
            "db_manager": MagicMock(),
            "kb_manager": MagicMock(),
            "settings": MagicMock(),
        },
        "processed_chapters": [
            {"title": "Ch 1", "input_path": "in1.txt", "output_path": "out1.txt"},
            {"title": "Ch 2", "input_path": "in2.txt", "output_path": "out2.txt"},
        ],
        "unification_verdicts": [],
        "judge_results": [],
        "blocking_issues": [],
        "refinement_count": 0,
        "error": None,
        "progress_callback": None,
    }

    with patch("Perevod.agents.nodes.tool_read_chapter") as mock_read:
        mock_read.side_effect = ["E1", "R1", "E2", "R2"]

        result = judge_node(state)

    assert result["blocking_issues"] == [
        "zeta omission",
        "alpha term",
        "middle style",
    ]


def test_judge_node_returns_error_on_invalid_response():
    mock_llm_provider = MagicMock()
    mock_model = MagicMock()
    mock_llm_provider.get_model.return_value = mock_model

    mock_response = MagicMock()
    mock_response.text = "not json"
    mock_model.generate_content.return_value = mock_response

    state: AgentState = {
        "app_context": {
            "llm_provider": mock_llm_provider,
            "db_manager": MagicMock(),
            "kb_manager": MagicMock(),
            "settings": MagicMock(),
        },
        "processed_chapters": [
            {"title": "Ch 1", "input_path": "in1.txt", "output_path": "out1.txt"}
        ],
        "unification_verdicts": [],
        "judge_results": [],
        "blocking_issues": [],
        "refinement_count": 0,
        "error": None,
        "progress_callback": None,
    }

    with patch("Perevod.agents.nodes.tool_read_chapter") as mock_read:
        mock_read.side_effect = ["E1", "R1"]

        result = judge_node(state)

    assert result["error"].startswith("Ошибка Судьи для главы 'Ch 1'")
    assert result["blocking_issues"] == []


def test_judge_node_preserves_existing_workflow_error():
    state: AgentState = {
        "app_context": {
            "llm_provider": MagicMock(),
            "db_manager": MagicMock(),
            "kb_manager": MagicMock(),
            "settings": MagicMock(),
        },
        "processed_chapters": [],
        "unification_verdicts": [],
        "judge_results": [],
        "blocking_issues": [],
        "refinement_count": 0,
        "error": "translation failed",
        "progress_callback": None,
    }

    assert judge_node(state) == {"error": "translation failed"}


def test_judge_node_merges_deterministic_quality_issues():
    mock_llm_provider = MagicMock()
    mock_model = MagicMock()
    mock_llm_provider.get_model.return_value = mock_model

    mock_response = MagicMock()
    mock_response.text = '{"pass_check": true, "severity": "low", "blocking_issues": [], "suggestions": [], "score": 9}'
    mock_model.generate_content.return_value = mock_response

    state: AgentState = {
        "app_context": {
            "llm_provider": mock_llm_provider,
            "db_manager": MagicMock(),
            "kb_manager": MagicMock(),
            "settings": MagicMock(),
        },
        "processed_chapters": [
            {"title": "Ch 1", "input_path": "in1.txt", "output_path": "out1.txt"}
        ],
        "unification_verdicts": [
            {
                "english_term": "Spirit Lotus",
                "correct_variant": "Духовный лотос",
            }
        ],
        "judge_results": [],
        "blocking_issues": [],
        "refinement_count": 0,
        "error": None,
        "progress_callback": None,
    }

    with patch("Perevod.agents.nodes.tool_read_chapter") as mock_read:
        mock_read.side_effect = [
            "Spirit Lotus " * 80,
            "Лотос.",
        ]

        result = judge_node(state)

    assert result["judge_results"][0]["pass_check"] is False
    assert result["judge_results"][0]["severity"] == "high"
    assert result["judge_results"][0]["score"] <= 4.0
    assert any("suspiciously short" in issue for issue in result["blocking_issues"])
    assert any("Spirit Lotus -> Духовный лотос" in issue for issue in result["blocking_issues"])
    assert result["processed_chapters"][0]["blocking_issues"]


def test_judge_node_uses_existing_project_dictionary_for_sanity_checks():
    mock_llm_provider = MagicMock()
    mock_model = MagicMock()
    mock_llm_provider.get_model.return_value = mock_model

    mock_response = MagicMock()
    mock_response.text = '{"pass_check": true, "severity": "low", "blocking_issues": [], "suggestions": [], "score": 9}'
    mock_model.generate_content.return_value = mock_response

    db_manager = MagicMock()
    db_manager.get_terms_dictionary.return_value = {
        "Spirit Lotus": {"russian_term": "Духовный лотос", "category": "Item"}
    }

    state: AgentState = {
        "app_context": {
            "llm_provider": mock_llm_provider,
            "db_manager": db_manager,
            "kb_manager": MagicMock(),
            "settings": MagicMock(),
        },
        "processed_chapters": [
            {"title": "Ch 1", "input_path": "in1.txt", "output_path": "out1.txt"}
        ],
        "unification_verdicts": [],
        "judge_results": [],
        "blocking_issues": [],
        "refinement_count": 0,
        "error": None,
        "progress_callback": None,
    }

    with patch("Perevod.agents.nodes.tool_read_chapter") as mock_read:
        mock_read.side_effect = [
            "The Spirit Lotus awakened.",
            "Лотос пробудился.",
        ]

        result = judge_node(state)

    assert result["judge_results"][0]["pass_check"] is False
    assert any(
        "Spirit Lotus -> Духовный лотос" in issue
        for issue in result["blocking_issues"]
    )
