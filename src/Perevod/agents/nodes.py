# agents/nodes.py

import logging
import google.generativeai as genai
from Perevod.agents.state import AgentState
from Perevod.agents import tools
from Perevod.utils.file_io import tool_read_chapter, tool_write_chapter
import os # Keep os for os.makedirs and os.listdir

logger = logging.getLogger("NovelTranslator.AgentNodes")

def initialize_project_node(state: AgentState) -> dict:
    """Инициализирует проект, загружает настройки и формирует список глав."""
    logger.info(f"Узел: Инициализация проекта '{state['project_name']}'")
    settings = state['project_settings']
    input_dir, output_dir = settings.get('input_dir'), settings.get('output_dir')
    if not input_dir or not output_dir:
        return {"error": "Директории ввода/вывода не указаны."}
    try:
        os.makedirs(output_dir, exist_ok=True)
        all_files = sorted([f for f in os.listdir(input_dir) if os.path.isfile(os.path.join(input_dir, f)) and f.lower().endswith(('.txt', '.md'))])
    except FileNotFoundError:
        return {"error": f"Директория ввода '{input_dir}' не найдена."}
    
    chapters_to_process = [
        {"title": os.path.splitext(f)[0], "input_path": os.path.join(input_dir, f), "output_path": os.path.join(output_dir, f"{os.path.splitext(f)[0]}.txt")}
        for f in all_files if settings.get('overwrite_existing') or not os.path.exists(os.path.join(output_dir, f"{os.path.splitext(f)[0]}.txt"))
    ]
    logger.info(f"Найдено глав для обработки: {len(chapters_to_process)}")
    return {"chapters_to_process": chapters_to_process, "processed_chapters": [], "unification_verdicts": []}

def process_all_chapters_node(state: AgentState) -> dict:
    """Обрабатывает ВСЕ главы в цикле, решая проблему с лимитом рекурсии."""
    logger.info("Узел: Начало пакетной обработки всех глав...")
    db_manager, kb_manager = state['db_manager'], state['kb_manager']
    chapters_queue, settings = state['chapters_to_process'], state['project_settings']
    progress_callback = state.get('progress_callback')
    total_chapters = len(chapters_queue)

    try:
        model = state['model']
        processed_chapters_list = []

        for i, chapter_data in enumerate(chapters_queue):
            title = chapter_data['title']
            logger.info(f"Обработка главы {i+1}/{total_chapters}: '{title}'")
            if progress_callback:
                progress_callback(i / total_chapters * 100, f"Перевод главы {i+1}/{total_chapters}: {title}")

            raw_text = tool_read_chapter(chapter_data['input_path'])
            eng_text = raw_text # tool_sanitize_text is a placeholder and not needed for now
            rus_text = tools.tool_translate_chapter_logic(eng_text, title, settings, db_manager, kb_manager, model)
            tool_write_chapter(chapter_data['output_path'], rus_text)
            
            # Генерация предложений для словаря и Библии Вселенной
            tools.tool_generate_dictionary_proposals(eng_text, rus_text, db_manager, model, settings)
            tools.tool_generate_world_bible_proposals(eng_text, rus_text, db_manager, model, settings)

            processed_chapters_list.append({"title": title, "input_path": chapter_data['input_path'], "output_path": chapter_data['output_path'], "rus_text": rus_text})

        logger.info("Пакетная обработка всех глав завершена.")
        if progress_callback:
            progress_callback(100, "Все главы переведены. Переход к контролю качества...")
        return {"processed_chapters": processed_chapters_list}
    except Exception as e:
        logger.error(f"Критическая ошибка во время пакетной обработки глав: {e}", exc_info=True)
        if progress_callback: progress_callback(100, f"Критическая ошибка: {e}")
        return {"error": f"Ошибка при обработке глав: {e}"}

def quality_assurance_node(state: AgentState) -> dict:
    """Узел контроля качества. Составляет единый отчет о проблемах."""
    logger.info("Узел: Контроль качества (Аудит консистентности)")
    settings = state['project_settings']
    model = state['model']
    
    report = tools.tool_analyze_inconsistencies(
        processed_texts=state['processed_chapters'],
        dictionary=state['db_manager'].get_terms_dictionary(),
        model=model, settings=settings
    )
    return {"quality_assurance_report": report}

def evaluation_node(state: AgentState) -> dict:
    """
    [АРХИТЕКТУРНОЕ ИЗМЕНЕНИЕ] Узел "Коллегия Судей".
    Работает как цикл: получает полный отчет, выносит вердикт по КАЖДОЙ проблеме
    и собирает все вердикты в единый пакет для "Бригады Ремонта".
    """
    logger.info("Узел: Оценка и вынесение вердиктов (Коллегия Судей)")
    report = state['quality_assurance_report']
    if not report or not report.get('inconsistencies'):
        return {"unification_verdicts": []}

    db_manager, settings = state['db_manager'], state['project_settings']
    model = state['model']
    
    all_verdicts = []
    issues = report['inconsistencies']
    total_issues = len(issues)
    logger.info(f"Начинается оценка {total_issues} проблем консистентности...")

    for i, issue in enumerate(issues):
        logger.info(f"Оценка проблемы {i+1}/{total_issues}: {issue['english_term']}")
        verdict = tools.tool_evaluate_inconsistency(
            issue=issue, dictionary=db_manager.get_terms_dictionary(),
            model=model, settings=settings
        )
        # Дополняем вердикт исходными вариантами для последующей замены
        verdict['russian_variants'] = issue['russian_variants']
        all_verdicts.append(verdict)

    logger.info(f"Оценка всех проблем завершена. Сформировано {len(all_verdicts)} вердиктов.")
    return {"unification_verdicts": all_verdicts}

def apply_fixes_node(state: AgentState) -> dict:
    """
    [АРХИТЕКТУРНОЕ ИЗМЕНЕНИЕ] Узел "Бригада Ремонта".
    Получает полный пакет вердиктов и за один проход исправляет все главы.
    """
    logger.info("Узел: Применение автоматических исправлений (Бригада Ремонта)")
    verdicts = state.get('unification_verdicts')
    if not verdicts:
        logger.info("Нет вердиктов для применения. Пропускаем.")
        return {"processed_chapters": state['processed_chapters']}
        
    logger.info(f"Применение {len(verdicts)} пакетов исправлений ко всем главам...")
    tools.tool_apply_unification_batch(verdicts, state['processed_chapters'])
    
    # После исправления текстов, обновляем Базу Знаний
    logger.info("Обновление Базы Знаний на основе принятых вердиктов...")
    for verdict in verdicts:
        tools.tool_update_knowledge_base(verdict, state['db_manager'])

    return {"quality_assurance_report": None, "unification_verdicts": [], "processed_chapters": state['processed_chapters']}

def knowledge_base_audit_node(state: AgentState) -> dict:
    """Узел для автоматической проверки и очистки Базы Знаний."""
    logger.info("Узел: Финальный аудит и очистка Базы Знаний")
    try:
        audit_report = tools.tool_audit_knowledge_base(state['db_manager'])
        logger.info(f"Аудит Базы Знаний завершен с результатом: {audit_report}")
        return {"knowledge_base_audit_report": audit_report}
    except Exception as e:
        logger.error(f"Ошибка во время аудита Базы Знаний: {e}", exc_info=True)
        return {"error": f"Ошибка аудита БЗ: {e}"}