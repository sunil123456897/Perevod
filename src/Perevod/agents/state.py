# src/Perevod/agents/state.py
from typing import TypedDict, List, Dict, Any
from Perevod.config import Settings
from Perevod.schemas import TermAnalysis, InconsistencyVerdict
from Perevod.llm_provider import LLMProvider


class AppContext(TypedDict):
    """Контейнер для долгоживущих, неизменяемых ресурсов."""

    db_manager: Any
    kb_manager: Any
    llm_provider: LLMProvider  # <-- ЗАМЕНА
    settings: Settings


class AgentState(TypedDict):
    """
    Основное состояние графа. Передает данные между узлами.
    """

    # --- Контекст (передается без изменений) ---
    app_context: AppContext

    # --- Данные (трансформируются узлами) ---
    project_name: str
    project_settings: Dict[str, Any]
    chapters_to_process: List[Dict[str, str]]
    processed_chapters: List[Dict[str, str]]

    # Результаты анализа (список Pydantic-объектов)
    analysis_results: List[TermAnalysis]
    analysis_errors: List[Dict[str, str]]

    # Финальные вердикты от куратора (список Pydantic-объектов)
    unification_verdicts: List[InconsistencyVerdict]

    judge_results: List[Dict[str, Any]]  # Results per chapter
    refinement_count: int  # Current iteration counter
    blocking_issues: List[str]  # Current active issues to fix

    # --- Sprint 2: Rolling Memory & RAG ---
    rag_context: str
    chapter_contexts: Dict[str, str]
    context_errors: List[Dict[str, str]]
    context_warnings: List[Dict[str, str]]
    chapter_summaries: List[Dict[str, Any]]
    summary_errors: List[Dict[str, str]]
    chapter_runs: Dict[str, Dict[str, Any]]

    # --- Служебные поля ---
    error: str | None
    progress_callback: Any
