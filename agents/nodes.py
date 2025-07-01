import os
import logging
import google.generativeai as genai
import json
from .state import AgentState
from . import tools

logger = logging.getLogger("NovelTranslator.AgentNodes")

def initialize_project_node(state: AgentState, config: dict) -> dict:
    """
    Узел инициализации. Загружает список глав для обработки.
    """
    logger.info(f"Узел: Инициализация проекта '{state['project_name']}'")
    db_manager = config["configurable"]["db_manager"]
    
    # Получаем актуальные настройки
    settings = db_manager.get_project_settings()
    
    # Собираем список файлов для обработки
    input_dir = settings.get('input_dir')
    output_dir = settings.get('output_dir')
    
    if not input_dir or not output_dir:
        return {"error": "Директории ввода/вывода не указаны в настройках проекта."}
        
    try:
        filenames = sorted([f for f in os.listdir(input_dir) if f.lower().endswith('.txt')])
    except FileNotFoundError:
        return {"error": f"Директория '{input_dir}' не найдена."}
    
    chapters_to_process = []
    for filename in filenames:
        if not settings.get('overwrite_existing') and os.path.exists(os.path.join(output_dir, f"ru_{filename}")):
            continue
        chapters_to_process.append({
            "input_path": os.path.join(input_dir, filename),
            "output_path": os.path.join(output_dir, f"ru_{filename}"),
            "title": filename
        })
        
    logger.info(f"Найдено {len(chapters_to_process)} глав для обработки.")
    return {"chapters_to_process": chapters_to_process, "project_settings": settings, "processed_chapters": []}

def chapter_processing_node(state: AgentState, config: dict) -> dict:
    """
    Основной рабочий узел. Обрабатывает одну главу из очереди.
    """
    db_manager = config["configurable"]["db_manager"]
    kb_manager = config["configurable"]["kb_manager"]
    chapters_queue = state['chapters_to_process']
    
    if not chapters_queue:
        logger.warning("Узел обработки глав вызван с пустой очередью.")
        return {}

    # Берем одну главу из начала очереди
    current_chapter = chapters_queue.pop(0)
    title = current_chapter['title']
    logger.info(f"Узел: Начало обработки главы '{title}'")
    
    try:
        # 1. Чтение (Инструмент ScribeAgent)
        eng_text = tools.tool_read_chapter(current_chapter['input_path'])
        
        # 2. Перевод (Инструмент TranslatorAgent) с циклом критики и уточнения
        api_key = state['project_settings']['api_key']
        model_name = state['project_settings']['model_name']
        genai.configure(api_key=api_key)
        model = genai.GenerativeModel(model_name)
        
        # Исходный текст для цикла
        current_eng_text = eng_text
        current_rus_text = ""
        
        # Цикл генерации, критики и уточнения
        max_refinement_iterations = 2 # Можно настроить
        for i in range(max_refinement_iterations):
            logger.info(f"Итерация уточнения {i+1}/{max_refinement_iterations} для главы '{title}'")
            
            # Генерация (или уточнение)
            if i == 0:
                # Первая генерация
                current_rus_text = tools.tool_translate_chapter_logic(
                    eng_text=current_eng_text,
                    title=title,
                    settings=state['project_settings'],
                    db_manager=db_manager,
                    kb_manager=kb_manager,
                    model=model
                )
            else:
                # Уточнение на основе критики
                # Для простоты, здесь мы используем tool_translate_chunk с расширенным промптом
                # В реальной реализации tool_translate_chapter_logic должен быть адаптирован
                # для приема критики и повторного перевода.
                # Пока что, для демонстрации, мы просто передадим критику в промпт.
                prompt_for_refinement = f"""# Task: Professional Translation (English to Russian)
## Context:
- Novel Chapter: "{title}"

## Instructions:
1. You have already produced a first-draft translation of the following passage, but an editor has provided feedback. Your task is to revise your translation to address the specific points in the critique.
2. Use dictionary hints like `{{term}}[translate as: X]` and then remove the hint.
3. Return ONLY the revised translated Russian text.

Original Text:
{current_eng_text}

Your First Draft:
{current_rus_text}

Editor's Critique:
{critique_feedback}

Your Revised, Improved Translation:"""
                current_rus_text = tools.tool_translate_chunk(model, prompt_for_refinement, state['project_settings'])
                current_rus_text = tools.clean_translation_output(current_rus_text)

            if not current_rus_text.strip():
                logger.warning(f"Перевод пуст после итерации {i+1}. Прерывание цикла.")
                break

            # Критика (только если это не последняя итерация)
            if i < max_refinement_iterations - 1:
                critique_feedback = tools.tool_critique_translation(
                    original_text=current_eng_text,
                    translation=current_rus_text,
                    model=model,
                    settings=state['project_settings']
                )
                if not critique_feedback.strip() or "проблем не обнаружено" in critique_feedback.lower():
                    logger.info(f"Критика не выявила проблем на итерации {i+1}. Завершение цикла уточнения.")
                    break
                logger.info(f"Получена критика на итерации {i+1}: {critique_feedback[:100]}...")
            
        rus_text = current_rus_text
        
        # 3. Запись (Инструмент ScribeAgent)
        tools.tool_write_chapter(current_chapter['output_path'], rus_text)
        
        processed_data = {
            "title": title,
            "eng_text": eng_text,
            "rus_text": rus_text
        }
        
        return {
            "chapters_to_process": chapters_queue, 
            "processed_chapters": [processed_data] # LangGraph сам объединит списки
        }

    except Exception as e:
        logger.error(f"Критическая ошибка при обработке главы '{title}': {e}")
        return {"error": f"Ошибка в главе {title}: {e}"}

# --- НОВЫЕ УЗЛЫ ДЛЯ ФАЗЫ 3 ---

def quality_assurance_node(state: AgentState, config: dict) -> dict:
    """
    Узел контроля качества. Запускает анализ после завершения перевода.
    """
    logger.info("Узел: Контроль качества (QA)")
    db_manager = config["configurable"]["db_manager"]
    processed_chapters = state['processed_chapters']
    settings = state['project_settings']
    
    # Инициализируем модель Gemini для анализа
    api_key = settings['api_key']
    model_name = settings['model_name']
    genai.configure(api_key=api_key)
    model = genai.GenerativeModel(model_name)
    
    report = tools.tool_analyze_inconsistencies(
        processed_texts=processed_chapters,
        dictionary=db_manager.get_terms_dictionary(),
        model=model,
        settings=settings
    )
    
    return {"quality_assurance_report": report}


def human_in_the_loop_node(state: AgentState) -> dict:
    """
    Узел взаимодействия с человеком. Этот узел сам по себе ничего не делает.
    Граф будет сконфигурирован так, чтобы останавливаться ПЕРЕД его выполнением.
    После возобновления он просто принимает решение пользователя и передает его дальше.
    """
    logger.info("Узел: Взаимодействие с пользователем (HITL)")
    user_decision = state.get("user_decision")
    
    if not user_decision:
        return {"error": "Процесс возобновлен, но решение от пользователя не было получено."}

    # В этой фазе мы не будем реализовывать исправления, а просто залогируем решение.
    # Реализация исправлений будет в Фазе 4.
    logger.info(f"Получено решение от пользователя: {user_decision}")
    
    # Очищаем отчет и решение, чтобы не попасть в цикл
    return {
        "quality_assurance_report": None,
        "user_decision": None
    }

# --- НОВЫЕ УЗЛЫ ДЛЯ ФАЗЫ 4 ---

def evaluation_node(state: AgentState, config: dict) -> dict:
    """
    Узел "Коллегии Судей". Анализирует отчет о качестве и выносит вердикты.
    """
    logger.info("Узел: Оценка и вынесение вердикта (Evaluation)")
    report = state['quality_assurance_report']
    db_manager = config["configurable"]["db_manager"]
    settings = state['project_settings']
    
    # Инициализируем модель Gemini
    api_key = settings['api_key']
    model_name = settings['model_name']
    genai.configure(api_key=api_key)
    model = genai.GenerativeModel(model_name)
    
    # В этой версии мы обрабатываем только первую проблему для простоты
    issue = report.get('inconsistencies', [])[0]
    
    verdict = tools.tool_evaluate_inconsistency(
        issue=issue,
        dictionary=db_manager.get_terms_dictionary(),
        model=model,
        settings=settings
    )
    
    # Добавляем исходные варианты в вердикт для инструмента исправления
    verdict['russian_variants'] = issue['russian_variants']
    
    return {"evaluation_verdict": verdict}


def apply_fixes_node(state: AgentState, config: dict) -> dict:
    """
    Узел применения исправлений. Обновляет файлы и Базу Знаний.
    """
    logger.info("Узел: Применение автоматических исправлений")
    verdict = state['evaluation_verdict']
    db_manager = config["configurable"]["db_manager"]
    
    if not verdict:
        return {"error": "Узел применения исправлений вызван без вердикта."}
        
    # 1. Обновляем Базу Знаний
    tools.tool_update_knowledge_base(verdict, db_manager)
    
    # 2. Исправляем тексты на диске
    tools.tool_apply_unification_fix(verdict, state['processed_chapters'])

    # Очищаем отчет, чтобы не зациклиться
    return {"quality_assurance_report": None, "evaluation_verdict": None}

def supervisor_node(state: AgentState, config: dict) -> str:
    """
    Узел-супервизор, который использует LLM для динамической маршрутизации.
    """
    logger.info("Узел: Супервизор LLM")
    api_key = state['project_settings']['api_key']
    model_name = state['project_settings']['model_name']
    genai.configure(api_key=api_key)
    model = genai.GenerativeModel(model_name)

    # Формируем историю сообщений для LLM
    messages = []
    if state.get("user_input"):
        messages.append({"role": "user", "parts": [state["user_input"]]})
    if state.get("processed_chapters"):
        messages.append({"role": "assistant", "parts": [f"Processed {len(state['processed_chapters'])} chapters."]})
    if state.get("quality_assurance_report"):
        messages.append({"role": "assistant", "parts": [f"QA Report: {state['quality_assurance_report']}"]})
    if state.get("evaluation_verdict"):
        messages.append({"role": "assistant", "parts": [f"Evaluation Verdict: {state['evaluation_verdict']}"]})

    # Системный промпт для супервизора
    system_prompt = """You are a master supervisor for an AI-powered novel translation system. Your role is to orchestrate a team of specialist agents to fulfill the user's request. Based on the current conversation history, you must decide which agent should act next.

Available Agents (Tools):
- 'initialize': Use to initialize the project and load chapters for processing.
- 'process_chapter': Use for translating a novel chapter or passage.
- 'quality_assurance': Use to perform a quality assurance check on completed translations.
- 'evaluate': Use to review a QA report and decide on a definitive fix for an inconsistency.
- 'apply_fixes': Use to apply automatic fixes based on evaluation verdict.

Your decision must be based on the most recent state and the overall goal. Respond ONLY with a JSON object containing a single key "next_agent" whose value is the name of the agent to call next. If the task is fully complete and no further action is needed, respond with {"next_agent": "FINISH"}.

Example: {"next_agent": "process_chapter"}"""

    try:
        response = model.generate_content(
            contents=[{"role": "user", "parts": [system_prompt + "\n\n" + json.dumps(messages)]}],
            generation_config=genai.types.GenerationConfig(temperature=0.0)
        )
        response_text = response.text.strip()
        
        # Извлечение JSON
        json_match = re.search(r'```json\s*({.+?})\s*```', response_text, re.DOTALL | re.IGNORECASE) or re.search(r'({.*?})', response_text, re.DOTALL)
        if json_match:
            decision = json.loads(json_match.group(1))
            next_agent = decision.get("next_agent")
            logger.info(f"Супервизор выбрал следующего агента: {next_agent}")
            return {"next_agent": next_agent}
        else:
            logger.error(f"Супервизор не смог извлечь JSON: {response_text}")
            return {"next_agent": "FINISH"} # Fallback
    except Exception as e:
        logger.error(f"Ошибка в узле супервизора: {e}")
        return {"next_agent": "FINISH"} # Fallback