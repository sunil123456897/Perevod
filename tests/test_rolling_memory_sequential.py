import os
import shutil
import logging
import sys
import uuid

# Add src to sys.path
sys.path.append(os.path.join(os.getcwd(), "src"))

from Perevod.graph_runner import run_translation_workflow
from Perevod.config import settings
from Perevod.database import database_manager as database_manager_module
from Perevod.knowledge_base import knowledge_base_manager as kb_manager_module
from Perevod.knowledge_base.knowledge_base_manager import KnowledgeBaseManager
from Perevod.database.database_manager import DatabaseManager

from unittest.mock import MagicMock, patch

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("TestRollingMemory")
TEST_API_KEY = "test_api_key"

@patch("Perevod.llm_provider.genai.Client")
@patch("Perevod.utils.file_io.tool_write_chapter")
def test_rolling_memory_sequential(mock_write, mock_genai_client, tmp_path, monkeypatch):
    monkeypatch.setattr(database_manager_module, "PROJECT_ROOT", str(tmp_path))
    monkeypatch.setattr(kb_manager_module, "PROJECT_ROOT", str(tmp_path))
    monkeypatch.setattr("Perevod.graph_runner.is_placeholder_api_key", lambda _api_key: False)

    # Use a simple class for the response to avoid MagicMock complications
    class MockResponse:
        def __init__(self, text):
            self.text = text

    # Pre-defined responses
    responses = [
        '{"found_terms": []}', # Ch 1 Analysis
        "Translated text 1", # Ch 1 Trans
        '{"pass_check": true, "severity": "low", "blocking_issues": [], "suggestions": [], "score": 9}', # Ch 1 Judge
        '{"title": "Chapter 1", "summary": "John finds a sword.", "key_events": ["found sword"], "active_characters": ["John"]}', # Ch 1 Summary
        '{"found_terms": []}', # Ch 2 Analysis
        "Translated text 2", # Ch 2 Trans
        '{"pass_check": true, "severity": "low", "blocking_issues": [], "suggestions": [], "score": 9}', # Ch 2 Judge
        '{"title": "Chapter 2", "summary": "John fights a dragon.", "key_events": ["fought dragon"], "active_characters": ["John"]}', # Ch 2 Summary
    ]
    
    # Mock the direct call used by GeminiModelAdapter
    mock_genai_client.return_value.models.generate_content.side_effect = lambda **kwargs: MockResponse(responses.pop(0))
    
    # Mock embedding response
    mock_genai_client.return_value.models.embed_content.return_value = MagicMock(
        embeddings=[MagicMock(values=[0.1]*3072)]
    )
    
    # Use a unique project name
    project_name = f"test_rm_seq_{uuid.uuid4().hex}"
    test_dir = os.path.abspath(os.path.join(tmp_path, f"workflow_test_{project_name}"))
    input_dir = os.path.join(test_dir, "input")
    output_dir = os.path.join(test_dir, "output")
    
    # Cleanup previous test runs
    if os.path.exists(test_dir):
        shutil.rmtree(test_dir)
    os.makedirs(input_dir, exist_ok=True)
    os.makedirs(output_dir, exist_ok=True)
    
    # Initialize managers
    DatabaseManager(project_name)
    kb_manager = KnowledgeBaseManager(
        project_name=project_name,
        api_key=TEST_API_KEY,
        embedding_model_name=settings.embedding_model_name
    )
    
    # 1. Create Chapter 1
    ch1_path = os.path.join(input_dir, "001_Chapter_1.txt")
    with open(ch1_path, "w", encoding="utf-8") as f:
        f.write("This is the first chapter. The hero, John, enters the dark forest. He finds a magic sword.")
    
    project_settings = {
        "input_dir": input_dir,
        "output_dir": output_dir,
        "GOOGLE_API_KEY": TEST_API_KEY,
        "overwrite_existing": False,
        "gemini_free_tier_mode": True
    }
    
    logger.info(f"--- Running Chapter 1 for project {project_name} ---")
    run_translation_workflow(project_name, project_settings)
    
    # Verify summary 1 exists
    results = kb_manager.collection.get(where={"type": "chapter_memory"})
    assert len(results["ids"]) == 1, f"Expected 1 summary, got {len(results['ids'])}"
    logger.info(f"Chapter 1 summary: {results['documents'][0]}")
    
    # 2. Create Chapter 2
    ch2_path = os.path.join(input_dir, "002_Chapter_2.txt")
    with open(ch2_path, "w", encoding="utf-8") as f:
        f.write("This is the second chapter. John uses the magic sword to fight a dragon in the forest.")
    
    logger.info("--- Running Chapter 2 ---")
    final_state = run_translation_workflow(project_name, project_settings)
    
    # Verify summary 2 exists
    results = kb_manager.collection.get(where={"type": "chapter_memory"})
    assert len(results["ids"]) == 2, f"Expected 2 summaries, got {len(results['ids'])}"
    
    # Check if Chapter 1 summary was in rag_context for Chapter 2
    rag_context = final_state.get("rag_context", "")
    logger.info(f"RAG Context for Chapter 2:\n{rag_context}")
    
    assert "Chapter 1" in rag_context or "001_Chapter_1" in rag_context
    assert "Chapter Summary:" in rag_context
    
    logger.info("Sequential test passed!")
    
    # Cleanup
    try:
        kb_manager.delete_collection()
        shutil.rmtree(test_dir)
    except Exception:
        pass

if __name__ == "__main__":
    try:
        test_rolling_memory_sequential()
    except Exception as e:
        logger.error(f"Test failed: {e}", exc_info=True)
        sys.exit(1)
