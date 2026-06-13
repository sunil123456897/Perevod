import os
import shutil
import uuid
from unittest.mock import MagicMock, patch

import pytest

from Perevod.config import PROJECT_ROOT
from Perevod.graph_runner import (
    JUDGE,
    TRANSLATION,
    _acquire_workflow_lock,
    _release_workflow_lock,
    build_graph,
    run_translation_workflow,
)


def _workspace_temp_dir():
    path = os.path.join(PROJECT_ROOT, f"_test_graph_integration_{uuid.uuid4().hex}")
    os.makedirs(path, exist_ok=False)
    return path


def test_graph_runs_judge_after_translation():
    graph = build_graph()

    assert graph.get_graph().edges
    assert any(
        edge.source == TRANSLATION and edge.target == JUDGE
        for edge in graph.get_graph().edges
    )


@patch("Perevod.graph_runner.os.makedirs")
@patch("Perevod.graph_runner._release_workflow_lock")
@patch("Perevod.graph_runner._acquire_workflow_lock", return_value="lock")
@patch("Perevod.graph_runner.os.path.exists", return_value=False)
@patch("Perevod.graph_runner.os.path.isfile", return_value=True)
@patch("Perevod.graph_runner.os.path.isdir", return_value=True)
@patch("Perevod.graph_runner.os.listdir", return_value=["chapter1.txt"])
@patch("Perevod.graph_runner.build_graph")
@patch("Perevod.graph_runner.DatabaseManager")
@patch("Perevod.graph_runner.KnowledgeBaseManager")
@patch("Perevod.graph_runner.LLMProvider")
def test_workflow_wires_dependencies_and_chapters(
    mock_llm_provider,
    mock_kb_manager,
    mock_db_manager,
    mock_build_graph,
    mock_listdir,
    mock_isdir,
    mock_isfile,
    mock_exists,
    mock_acquire_lock,
    mock_release_lock,
    mock_makedirs,
):
    mock_graph_app = MagicMock()
    mock_graph_app.invoke.side_effect = lambda state: {
        **state,
        "processed_chapters": state["chapters_to_process"],
        "error": None,
    }
    mock_build_graph.return_value = mock_graph_app

    project_settings = {
        "input_dir": r"C:\novel\input",
        "output_dir": r"C:\novel\output",
        "GOOGLE_API_KEY": "AIza-real-looking-key",
        "overwrite_existing": True,
    }

    final_state = run_translation_workflow("integration_test_project", project_settings)

    mock_makedirs.assert_called_once_with(r"C:\novel\output", exist_ok=True)
    mock_listdir.assert_called_once_with(r"C:\novel\input")
    mock_isfile.assert_called_once()
    mock_exists.assert_called_once_with(r"C:\novel\output\chapter1.txt")
    mock_db_manager.assert_called_once_with("integration_test_project")
    mock_kb_manager.assert_called_once()
    mock_llm_provider.assert_called_once()
    mock_build_graph.assert_called_once()
    mock_graph_app.invoke.assert_called_once()
    mock_acquire_lock.assert_called_once_with(r"C:\novel\output")
    mock_release_lock.assert_called_once_with("lock")

    assert final_state["error"] is None
    assert final_state["processed_chapters"] == [
        {
            "title": "chapter1",
            "input_path": r"C:\novel\input\chapter1.txt",
            "output_path": r"C:\novel\output\chapter1.txt",
        }
    ]


@patch("Perevod.graph_runner.os.makedirs")
@patch("Perevod.graph_runner._release_workflow_lock")
@patch("Perevod.graph_runner._acquire_workflow_lock", return_value="lock")
@patch("Perevod.graph_runner.os.path.isfile", return_value=True)
@patch("Perevod.graph_runner.os.path.isdir", return_value=True)
@patch("Perevod.graph_runner.os.listdir", return_value=["chapter1.txt"])
def test_workflow_requires_api_key(
    mock_listdir,
    mock_isdir,
    mock_isfile,
    mock_acquire_lock,
    mock_release_lock,
    mock_makedirs,
    monkeypatch,
):
    monkeypatch.setattr("Perevod.graph_runner.settings.GOOGLE_API_KEY", "")

    with pytest.raises(ValueError, match="GOOGLE_API_KEY"):
        run_translation_workflow(
            "integration_test_project",
            {
                "input_dir": r"C:\novel\input",
                "output_dir": r"C:\novel\output",
                "overwrite_existing": True,
            },
        )


@patch("Perevod.graph_runner.os.makedirs")
@patch("Perevod.graph_runner._release_workflow_lock")
@patch("Perevod.graph_runner._acquire_workflow_lock", return_value="lock")
@patch("Perevod.graph_runner.os.path.exists", return_value=False)
@patch("Perevod.graph_runner.os.path.isfile", return_value=True)
@patch("Perevod.graph_runner.os.path.isdir", return_value=True)
@patch("Perevod.graph_runner.os.listdir", return_value=["chapter1.txt"])
@patch("Perevod.graph_runner.build_graph")
@patch("Perevod.graph_runner.DatabaseManager")
@patch("Perevod.graph_runner.KnowledgeBaseManager")
@patch("Perevod.graph_runner.LLMProvider")
def test_workflow_rejects_placeholder_api_key(
    mock_llm_provider,
    mock_kb_manager,
    mock_db_manager,
    mock_build_graph,
    mock_listdir,
    mock_isdir,
    mock_isfile,
    mock_exists,
    mock_acquire_lock,
    mock_release_lock,
    mock_makedirs,
):
    with pytest.raises(ValueError, match="real GOOGLE_API_KEY"):
        run_translation_workflow(
            "integration_test_project",
            {
                "input_dir": r"C:\novel\input",
                "output_dir": r"C:\novel\output",
                "GOOGLE_API_KEY": "test_api_key",
                "overwrite_existing": True,
            },
        )

    mock_db_manager.assert_not_called()
    mock_kb_manager.assert_not_called()
    mock_llm_provider.assert_not_called()
    mock_build_graph.assert_not_called()
    mock_release_lock.assert_called_once_with("lock")


def test_workflow_lock_rejects_concurrent_run():
    temp_dir = _workspace_temp_dir()
    lock_path = _acquire_workflow_lock(temp_dir)
    try:
        with pytest.raises(RuntimeError, match="already running"):
            _acquire_workflow_lock(temp_dir)
    finally:
        _release_workflow_lock(lock_path)
        shutil.rmtree(temp_dir, ignore_errors=True)
