# tests/conftest.py
import pytest
from unittest.mock import MagicMock
from collections import defaultdict


# Фикстура для мокирования всего AppContext
@pytest.fixture
def mock_app_context():
    """Создает полный мок AppContext для использования в тестах узлов."""
    mock_context = MagicMock()

    # Мок LLMProvider
    mock_context.llm_provider.get_model.return_value = MagicMock()

    # Мок DatabaseManager
    mock_db_manager = MagicMock()
    mock_db_manager.get_from_cache.return_value = None
    mock_db_manager.add_to_cache.return_value = None
    mock_context.db_manager = mock_db_manager

    # Мок KnowledgeBaseManager
    mock_context.kb_manager.query.return_value = "Mocked RAG context"

    # Мок Settings
    mock_context.settings.temperature = 0.5
    mock_context.settings.top_p = 0.9

    return mock_context


# Фикстура для базового состояния агента
@pytest.fixture
def base_agent_state(mock_app_context):
    """Создает базовый AgentState для тестов."""
    return {
        "app_context": mock_app_context,
        "project_name": "test_project",
        "project_settings": {},
        "chapters_to_process": [{"input_path": "ch1.txt", "output_path": "out1.txt"}],
        "analysis_results": [],
        "unification_verdicts": [],
        "processed_chunks": [],
        "fix_attempts": defaultdict(int),
        "error": None,
        "progress_callback": None,
    }
