from unittest.mock import patch
from langgraph.graph import END

from Perevod.graph_runner import (
    ANALYSIS,
    CURATION,
    CONTEXT_RETRIEVAL,
    TRANSLATION,
    JUDGE,
    SUMMARIZATION,
    build_graph,
    should_refine,
)

def test_graph_structure_v3_2():
    """Verify that the graph has the new Context -> Translation -> ... -> Summarization architecture."""
    graph = build_graph()
    
    # Check edges
    edges = graph.get_graph().edges
    
    # CONTEXT_RETRIEVAL -> ANALYSIS -> CURATION -> TRANSLATION
    assert any(e.source == CONTEXT_RETRIEVAL and e.target == ANALYSIS for e in edges)
    assert any(e.source == ANALYSIS and e.target == CURATION for e in edges)
    assert any(e.source == CURATION and e.target == TRANSLATION for e in edges)
    
    # TRANSLATION -> JUDGE
    assert any(e.source == TRANSLATION and e.target == JUDGE for e in edges)
    
    # JUDGE has conditional edges
    assert any(e.source == JUDGE for e in edges)
    
    # SUMMARIZATION -> END
    assert any(e.source == SUMMARIZATION and e.target == END for e in edges)

def test_should_refine_logic_v3_2():
    """Test the conditional routing logic with SUMMARIZATION."""
    
    # Case 1: No blocking issues -> SUMMARIZATION
    state_no_issues = {"blocking_issues": [], "refinement_count": 0}
    assert should_refine(state_no_issues) == SUMMARIZATION
    
    # Case 2: Blocking issues, limit reached -> SUMMARIZATION
    state_issues_limit = {"blocking_issues": ["Issue 1"], "refinement_count": 2}
    assert should_refine(state_issues_limit) == SUMMARIZATION

@patch("Perevod.graph_runner.os.makedirs")
@patch("Perevod.graph_runner._release_workflow_lock")
@patch("Perevod.graph_runner._acquire_workflow_lock", return_value="lock")
@patch("Perevod.graph_runner.os.path.exists", return_value=False)
@patch("Perevod.graph_runner.os.path.isfile", return_value=True)
@patch("Perevod.graph_runner.os.listdir", return_value=["chapter1.txt"])
@patch("Perevod.graph_runner.DatabaseManager")
@patch("Perevod.graph_runner.KnowledgeBaseManager")
@patch("Perevod.graph_runner.LLMProvider")
@patch("Perevod.graph_runner.context_retrieval_node")
@patch("Perevod.graph_runner.translation_node")
@patch("Perevod.graph_runner.judge_node")
@patch("Perevod.graph_runner.refine_node")
@patch("Perevod.graph_runner.summarization_node")
@patch("Perevod.graph_runner.analysis_node")
@patch("Perevod.graph_runner.autonomous_curation_node")
def test_full_workflow_execution_v3_2(
    mock_curation,
    mock_analysis,
    mock_summarize,
    mock_refine,
    mock_judge,
    mock_translate,
    mock_context,
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
    
    # Mock nodes
    mock_context.side_effect = lambda s: {"rag_context": "some context", "chapter_summaries": []}
    mock_analysis.side_effect = lambda s: {"analysis_results": []}
    mock_curation.side_effect = lambda s: {"unification_verdicts": []}
    mock_translate.side_effect = lambda s: {"processed_chapters": [{"title": "chapter1", "input_path": "in", "output_path": "out"}]}
    mock_judge.side_effect = lambda s: {"blocking_issues": [], "judge_results": [{"pass_check": True}]}
    mock_summarize.side_effect = lambda s: {"chapter_summaries": [{"title": "chapter1", "summary": "done"}]}
    
    project_settings = {
        "input_dir": "input",
        "output_dir": "output",
        "GOOGLE_API_KEY": "AIza-real-looking-key"
    }
    
    with patch("Perevod.graph_runner._write_workflow_report"):
        final_state = run_translation_workflow("test_proj", project_settings)
    
    assert "rag_context" in final_state
    assert "chapter_summaries" in final_state
    assert mock_context.called
    assert mock_translate.called
    assert mock_judge.called
    assert mock_summarize.called
    
    assert mock_analysis.called
    assert mock_curation.called
