# graph_runner.py

import logging
from langgraph.graph import StateGraph, END
from Perevod.agents.state import AgentState
from Perevod.agents.nodes import (initialize_project_node, process_all_chapters_node, 
                          quality_assurance_node, evaluation_node, 
                          apply_fixes_node, knowledge_base_audit_node)
from Perevod.database.database_manager import DatabaseManager
from Perevod.knowledge_base.knowledge_base_manager import KnowledgeBaseManager
from typing import Any, Dict
from . import constants

logger = logging.getLogger("NovelTranslator.GraphRunner")

# ======================================================================================
# Маршрутизаторы (Условные рёбра)
# ======================================================================================

def router_after_initialization(state: AgentState) -> str:
    """Решает, есть ли главы для обработки после инициализации."""
    if state.get("error"): return constants.END
    return constants.PROCESS_ALL_CHAPTERS if state.get("chapters_to_process") else constants.END

def router_after_qa(state: AgentState) -> str:
    """Решает, нужн�� ли запускать конвейер исправлений после контроля качества."""
    report = state.get("quality_assurance_report")
    if not report or not report.get("inconsistencies"):
        logger.info("Контроль качества не выявил проблем. Завершение работы.")
        return constants.END
    else:
        logger.info(f"Найдено {len(report['inconsistencies'])} проблем. Запуск конвейера исправлений.")
        return constants.EVALUATE_AND_FIX_PIPELINE

# ======================================================================================
# Сборка и компиляция графа
# ======================================================================================

def build_graph():
    """Собирает и компилирует финальный, автономный граф с конвейером исправлений."""
    workflow = StateGraph(AgentState)

    # 1. Добавляем основные узлы-этапы
    workflow.add_node(constants.INITIALIZE, initialize_project_node)
    workflow.add_node(constants.PROCESS_ALL_CHAPTERS, process_all_chapters_node)
    workflow.add_node(constants.QUALITY_ASSURANCE, quality_assurance_node)

    # 2. [НОВАЯ АРХИТЕКТУРА] Создаем вложенный граф для конвейера исправлений
    fix_pipeline = StateGraph(AgentState)
    fix_pipeline.add_node(constants.EVALUATE, evaluation_node)
    fix_pipeline.add_node(constants.APPLY_FIXES, apply_fixes_node)
    fix_pipeline.set_entry_point(constants.EVALUATE)
    fix_pipeline.add_edge(constants.EVALUATE, constants.APPLY_FIXES)
    # Завершаем вложенный граф, чтобы он вернул управление основному
    fix_pipeline.add_edge(constants.APPLY_FIXES, END) 
    
    # Добавляем скомпилированный конвейер как один узел в основной граф
    workflow.add_node(constants.EVALUATE_AND_FIX_PIPELINE, fix_pipeline.compile())

    # Финальный узел аудита Базы Знаний
    workflow.add_node(constants.KNOWLEDGE_BASE_AUDIT, knowledge_base_audit_node)

    # 3. Определяем точку входа и соединяем узлы
    workflow.set_entry_point(constants.INITIALIZE)

    workflow.add_conditional_edges(
        constants.INITIALIZE,
        router_after_initialization,
        {constants.PROCESS_ALL_CHAPTERS: constants.PROCESS_ALL_CHAPTERS, constants.END: END}
    )
    
    workflow.add_edge(constants.PROCESS_ALL_CHAPTERS, constants.QUALITY_ASSURANCE)

    workflow.add_conditional_edges(
        constants.QUALITY_ASSURANCE,
        router_after_qa,
        {constants.EVALUATE_AND_FIX_PIPELINE: constants.EVALUATE_AND_FIX_PIPELINE, constants.END: END}
    )
    
    # После того как конвейер исправлений отработал, запускаем финальный аудит БЗ
    workflow.add_edge(constants.EVALUATE_AND_FIX_PIPELINE, constants.KNOWLEDGE_BASE_AUDIT)
    workflow.add_edge(constants.KNOWLEDGE_BASE_AUDIT, END)

    app = workflow.compile()
    logger.info("Автономный граф с конвейером контроля качества успешно скомпилирован.")
    return app

# ======================================================================================
# Функция выполнения графа
# ======================================================================================

def run_translation_workflow(project_settings: dict, db_manager: DatabaseManager, kb_manager: KnowledgeBaseManager, model: Any, progress_callback=None):
    """Выполняет граф с полной автоматической обработкой."""
    initial_state = {
        "project_name": project_settings.get('project_name'),
        "project_settings": project_settings,
        "db_manager": db_manager, 
        "kb_manager": kb_manager,
        "model": model,
        "progress_callback": progress_callback
    }

    app = build_graph()
    final_state = app.invoke(initial_state)

    return final_state
