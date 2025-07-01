# graph_runner.py

import logging
from langgraph.graph import StateGraph, END
from agents.state import AgentState
# [ИЗМЕНЕНО] Импортируем все узлы
from agents.nodes import (initialize_project_node, chapter_processing_node, 
                          quality_assurance_node, human_in_the_loop_node,
                          evaluation_node, apply_fixes_node, supervisor_node) # <-- НОВЫЕ
from database.database_manager import DatabaseManager
from knowledge_base.knowledge_base_manager import KnowledgeBaseManager
from typing import Any, Dict

logger = logging.getLogger("NovelTranslator.GraphRunner")

# --- Маршрутизаторы ---

def should_continue_processing(state: AgentState) -> str:
    """Определяет, остались ли главы для обработки."""
    if state.get("error"): return "end"
    return "continue" if state["chapters_to_process"] else "qa"

def qa_router(state: AgentState) -> str:
    """Направляет поток после контроля качества."""
    report = state.get("quality_assurance_report")
    if not report or not report.get("inconsistencies"):
        return "end"
    else:
        # [ИЗМЕНЕНО] Вместо HITL, переходим к автономной оценке
        logger.info(f"Найдено {len(report['inconsistencies'])} проблем. Переход к автоматической оценке.")
        return "evaluate"

# --- Сборка графа ---

def build_graph():
    """Собирает и компилирует финальную версию графа."""
    workflow = StateGraph(AgentState)

    # Добавляем все узлы
    workflow.add_node("initialize", initialize_project_node)
    workflow.add_node("process_chapter", chapter_processing_node)
    workflow.add_node("quality_assurance", quality_assurance_node)
    workflow.add_node("evaluate", evaluation_node)
    workflow.add_node("apply_fixes", apply_fixes_node)
    workflow.add_node("human_in_the_loop", human_in_the_loop_node) # Оставляем для будущих нужд
    workflow.add_node("supervisor", supervisor_node)

    workflow.set_entry_point("supervisor")

    # Динамическая маршрутизация через супервизора
    workflow.add_conditional_edges(
        "supervisor",
        lambda x: x["next_agent"],
        {
            "initialize": "initialize",
            "process_chapter": "process_chapter",
            "quality_assurance": "quality_assurance",
            "evaluate": "evaluate",
            "apply_fixes": "apply_fixes",
            "human_in_the_loop": "human_in_the_loop",
            "FINISH": END
        }
    )

    # После каждого узла возвращаемся к супервизору для следующего решения
    workflow.add_edge("initialize", "supervisor")
    workflow.add_edge("process_chapter", "supervisor")
    workflow.add_edge("quality_assurance", "supervisor")
    workflow.add_edge("evaluate", "supervisor")
    workflow.add_edge("apply_fixes", "supervisor")
    workflow.add_edge("human_in_the_loop", "supervisor")

    app = workflow.compile(
        # Точка прерывания все еще может быть полезна для отладки
        interrupt_before=["human_in_the_loop"] 
    )
    logger.info("Финальная версия графа LangGraph успешно скомпилирована.")
    return app

# --- Функция выполнения графа (остается без изменений) ---
# Наша функция `execute_graph_step_by_step` уже готова обрабатывать
# как прерываемые, так и полностью автономные графы.
def execute_graph_step_by_step(project_name: str, settings_overrides: dict = None, user_input: Dict[str, Any] = None, current_state: Dict = None):
    """
    [НОВОЕ] Выполняет граф по шагам, чтобы обрабатывать прерывания.
    """
    # 1. Инициализация менеджеров (только при первом запуске)
    db_manager = DatabaseManager(project_name=project_name)
    project_settings = db_manager.get_project_settings()
    if settings_overrides:
        project_settings.update(settings_overrides)
    
    kb_manager = KnowledgeBaseManager(
        project_name=project_name,
        api_key=project_settings.get('api_key'),
        embedding_model_name=project_settings.get('embedding_model_name')
    )
    configurable = {"db_manager": db_manager, "kb_manager": kb_manager}

    if current_state is None:
        initial_state = {"project_name": project_name, "project_settings": project_settings}
    else:
        initial_state = current_state
        initial_state["project_settings"] = project_settings # Ensure project_settings is always in state
        if user_input:
            initial_state["user_input"] = user_input # Use user_input for supervisor

    # 2. Сборка графа
    app = build_graph()
    
    # 3. Запуск или возобновление работы графа
    state = app.invoke(initial_state, configurable=configurable)
    
    return state