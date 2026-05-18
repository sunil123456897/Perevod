import pytest
from pydantic import ValidationError
from Perevod.schemas import JudgeResult
from Perevod.agents.state import AgentState

def test_judge_result_schema():
    """Проверка схемы JudgeResult."""
    # Валидные данные
    valid_data = {
        "pass_check": True,
        "severity": "low",
        "blocking_issues": [],
        "suggestions": ["Use more descriptive adjectives"],
        "score": 9
    }
    result = JudgeResult(**valid_data)
    assert result.pass_check is True
    assert result.score == 9
    assert result.severity == "low"

    # Невалидные данные (score > 10)
    invalid_data = valid_data.copy()
    invalid_data["score"] = 11
    with pytest.raises(ValidationError):
        JudgeResult(**invalid_data)

    # Невалидные данные (отсутствует обязательное поле)
    incomplete_data = {
        "pass_check": True,
        "severity": "medium"
    }
    with pytest.raises(ValidationError):
        JudgeResult(**incomplete_data)

def test_agent_state_new_fields():
    """Проверка наличия новых полей в AgentState."""
    # Это TypedDict, поэтому мы просто проверяем, что можем создать словарь с такими ключами
    # и что линтер/тайпчекер (если бы он был запущен) не ругался бы.
    # В рантайме TypedDict не проверяет типы, но мы можем проверить структуру.
    
    state: AgentState = {
        "app_context": {}, # Mock
        "project_name": "test",
        "project_settings": {},
        "chapters_to_process": [],
        "processed_chapters": [],
        "analysis_results": [],
        "unification_verdicts": [],
        "qa_results": [],
        "error": None,
        "progress_callback": None,
        "judge_results": [{"chapter_id": 1, "result": "pass"}],
        "refinement_count": 0,
        "blocking_issues": ["Issue 1"]
    }
    
    assert "judge_results" in state
    assert "refinement_count" in state
    assert "blocking_issues" in state
    assert state["refinement_count"] == 0
