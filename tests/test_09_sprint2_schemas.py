import pytest
from pydantic import ValidationError
from Perevod.schemas import ChapterSummary
from Perevod.agents.state import AgentState

def test_chapter_summary_schema():
    """Проверка схемы ChapterSummary."""
    data = {
        "title": "Chapter 1: The Beginning",
        "summary": "The protagonist discovers a mysterious artifact in the forest. It glows with an eerie blue light.",
        "key_events": ["Found artifact", "Met mysterious stranger"],
        "active_characters": ["Protagonist", "Stranger"]
    }
    summary = ChapterSummary(**data)
    assert summary.title == data["title"]
    assert summary.summary == data["summary"]
    assert summary.key_events == data["key_events"]
    assert summary.active_characters == data["active_characters"]

def test_chapter_summary_validation():
    """Проверка валидации ChapterSummary."""
    with pytest.raises(ValidationError):
        # Missing title
        ChapterSummary(summary="Some summary")

def test_agent_state_sprint2_fields():
    """Проверка наличия новых полей в AgentState."""
    # Мы не можем напрямую инстанцировать TypedDict для проверки типов в рантайме без сторонних библиотек,
    # но мы можем проверить наличие ключей в аннотациях.
    annotations = AgentState.__annotations__
    assert "rag_context" in annotations
    assert annotations["rag_context"] is str
    assert "context_errors" in annotations
    assert "chapter_summaries" in annotations
    # Проверяем, что это список словарей
    attr_str = str(annotations["chapter_summaries"])
    assert "List" in attr_str
    assert "Dict" in attr_str
