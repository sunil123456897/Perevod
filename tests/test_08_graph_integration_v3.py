from unittest.mock import patch

from Perevod.graph_runner import (
    JUDGE,
    REFINE,
    TRANSLATION,
    SUMMARIZATION,
    build_graph,
    should_refine,
)

def test_graph_structure_v3():
    """Verify that the graph has the new Judge -> Refine architecture."""
    graph = build_graph()
    
    # Check edges
    edges = graph.get_graph().edges
    
    # TRANSLATION -> JUDGE
    assert any(e.source == TRANSLATION and e.target == JUDGE for e in edges)
    
    # REFINE -> JUDGE (loop back)
    assert any(e.source == REFINE and e.target == JUDGE for e in edges)
    
    # JUDGE has conditional edges (we can't easily check targets of conditional edges via get_graph().edges in some versions, 
    # but we can check that JUDGE is a source)
    assert any(e.source == JUDGE for e in edges)

def test_should_refine_logic():
    """Test the conditional routing logic."""
    
    # Case 1: No blocking issues -> SUMMARIZATION
    state_no_issues = {"blocking_issues": [], "refinement_count": 0}
    assert should_refine(state_no_issues) == SUMMARIZATION
    
    # Case 2: Blocking issues, first attempt -> REFINE
    state_issues_1 = {"blocking_issues": ["Issue 1"], "refinement_count": 0}
    assert should_refine(state_issues_1) == REFINE
    
    # Case 3: Blocking issues, second attempt -> REFINE
    state_issues_2 = {"blocking_issues": ["Issue 1"], "refinement_count": 1}
    assert should_refine(state_issues_2) == REFINE
    
    # Case 4: Blocking issues, third attempt (limit reached) -> SUMMARIZATION
    state_issues_3 = {"blocking_issues": ["Issue 1"], "refinement_count": 2}
    assert should_refine(state_issues_3) == SUMMARIZATION

@patch("Perevod.graph_runner.os.makedirs")
@patch("Perevod.graph_runner._release_workflow_lock")
@patch("Perevod.graph_runner._acquire_workflow_lock", return_value="lock")
@patch("Perevod.graph_runner.os.path.exists", return_value=False)
@patch("Perevod.graph_runner.os.path.isfile", return_value=True)
@patch("Perevod.graph_runner.os.listdir", return_value=["chapter1.txt"])
@patch("Perevod.graph_runner.DatabaseManager")
@patch("Perevod.graph_runner.KnowledgeBaseManager")
@patch("Perevod.graph_runner.LLMProvider")
@patch("Perevod.graph_runner.analysis_node")
@patch("Perevod.graph_runner.autonomous_curation_node")
@patch("Perevod.graph_runner.translation_node")
@patch("Perevod.graph_runner.judge_node")
@patch("Perevod.graph_runner.refine_node")
@patch("Perevod.graph_runner.summarization_node")
def test_full_workflow_execution_v3(
    mock_summarization,
    mock_refine,
    mock_judge,
    mock_translate,
    mock_curation,
    mock_analysis,
    mock_llm,
    mock_kb,
    mock_db,
    mock_listdir,
    mock_isfile,
    mock_exists,
    mock_acquire,
    mock_release,
    mock_makedirs
):
    """Verify that the workflow initializes the new state fields and runs the graph."""
    from Perevod.graph_runner import run_translation_workflow
    
    # Mock nodes to just return state
    mock_analysis.side_effect = lambda s: {"analysis_results": []}
    mock_curation.side_effect = lambda s: {"unification_verdicts": []}
    mock_translate.side_effect = lambda s: {"processed_chapters": [{"title": "chapter1", "input_path": "in", "output_path": "out"}]}
    
    # First call to judge returns issues, second call returns no issues
    mock_judge.side_effect = [
        {"blocking_issues": ["Issue 1"], "judge_results": [{"pass_check": False}]},
        {"blocking_issues": [], "judge_results": [{"pass_check": True}]}
    ]
    mock_refine.side_effect = lambda s: {"refinement_count": s.get("refinement_count", 0) + 1}
    mock_summarization.side_effect = lambda s: {
        "chapter_summaries": [{"title": "chapter1"}],
        "summary_errors": [],
    }
    
    project_settings = {
        "input_dir": "input",
        "output_dir": "output",
        "GOOGLE_API_KEY": "AIza-real-looking-key"
    }
    
    with patch("Perevod.graph_runner._write_workflow_report"):
        final_state = run_translation_workflow("test_proj", project_settings)
    
    assert final_state["refinement_count"] == 1
    assert mock_judge.call_count == 2
    assert mock_refine.call_count == 1
    assert final_state["blocking_issues"] == []
