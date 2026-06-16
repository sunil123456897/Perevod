# src/Perevod/agents/nodes/analysis.py
import logging
import json
import hashlib
from collections import defaultdict

from Perevod.agents.state import AgentState
from Perevod.agents.checkpoints import chapter_stage_done, mark_chapter_stage
from Perevod.schemas import TermAnalysis
from Perevod.agents import nodes
from Perevod.utils.llm import generate_text
from Perevod.utils.api_errors import gemini_api_error_metadata

logger = logging.getLogger("NovelTranslator.AgentNodes.Analysis")


def analysis_node(state: AgentState) -> dict:
    logger.info("Узел [Анализ]: Запуск двухэтапного анализа...")
    context = state['app_context']
    chapters_to_process = state.get('chapters_to_process', [])
    analysis_model = context['llm_provider'].get_model('analysis')
    db_manager = context['db_manager']
    all_found_terms = []
    analysis_errors = []

    analysis_prompt_template = """Analyze the following fantasy novel chapter before translation.

Extract important named entities and recurring terminology that should stay consistent in Russian:
characters, factions, places, artifacts, techniques, titles, and setting-specific concepts.

Return only JSON with this exact shape:
{{"found_terms": [
  {{
    "english_term": "exact English term",
    "russian_translation": "best Russian translation",
    "category": "Person|Location|Faction|Item|Technique|Title|Concept|Other",
    "description": "one short English sentence explaining the term in context"
  }}
]}}

If there are no important terms, return {{"found_terms": []}}.

CHAPTER TITLE: {title}

CHAPTER TEXT:
{chapter_text}
"""

    for chapter_data in chapters_to_process:
        title = chapter_data.get('title') or chapter_data.get('input_path', 'Untitled')
        if chapter_stage_done(state, title, "analysis_done"):
            logger.info("Checkpoint: анализ главы '%s' уже выполнен, пропуск.", title)
            continue
        try:
            chapter_text = nodes.tool_read_chapter(chapter_data['input_path'])
            if not chapter_text.strip():
                logger.info(f"Глава '{title}' пуста, анализ пропущен.")
                continue

            # --- ДОБАВЛЕНО: Кэширование pre_analysis для экономии API Gemini ---
            cache_key = "analysis_" + hashlib.sha256(chapter_text.encode('utf-8')).hexdigest()
            cached_results_str = db_manager.get_from_cache(cache_key)
            if cached_results_str:
                logger.info(f"КЭШ [Анализ]: Найден кэш анализа для главы '{title}'.")
                try:
                    found_terms_for_chapter = json.loads(cached_results_str)
                    for term in found_terms_for_chapter:
                        if isinstance(term, dict):
                            term.setdefault("source_chapter", title)
                    all_found_terms.extend(found_terms_for_chapter)
                    mark_chapter_stage(db_manager, title, "analysis_done", "done")
                    continue
                except Exception as cache_err:
                    logger.warning(f"Ошибка при декодировании кэша анализа главы '{title}': {cache_err}. Выполняется запрос к API.")

            # Если кэша нет, запрашиваем LLM
            logger.info(f"Запрос к API для анализа главы '{title}'...")
            response_text = generate_text(
                analysis_model,
                analysis_prompt_template.format(title=title, chapter_text=chapter_text),
                context.get("settings", {}),
            )
            parsed_response = nodes.safe_json_loads(response_text, default={})
            raw_terms = parsed_response.get("found_terms", []) if isinstance(parsed_response, dict) else []
            # Validate each term individually so one malformed term (e.g. an LLM
            # entry missing russian_translation) is skipped with a warning
            # instead of crashing the whole workflow. Only well-formed terms
            # reach the dictionary/curation stages.
            found_terms_list = []
            for idx, raw in enumerate(raw_terms):
                if not isinstance(raw, dict):
                    logger.warning(
                        "Анализ главы '%s': термин #%d проигнорирован (не объект).",
                        title,
                        idx,
                    )
                    continue
                try:
                    term = TermAnalysis.model_validate(raw)
                except Exception as term_err:
                    logger.warning(
                        "Анализ главы '%s': термин #%d (%r) проигнорирован: %s",
                        title,
                        idx,
                        raw.get("english_term", "?"),
                        term_err,
                    )
                    continue
                found_terms_list.append({**term.model_dump(), "source_chapter": title})
            all_found_terms.extend(found_terms_list)
            mark_chapter_stage(db_manager, title, "analysis_done", "done")

            # Сохраняем в кэш
            try:
                db_manager.add_to_cache(cache_key, json.dumps(found_terms_list, ensure_ascii=False))
                logger.info(f"Кэш анализа для главы '{title}' успешно сохранен.")
            except Exception as cache_save_err:
                logger.warning(f"Не удалось сохранить кэш анализа для главы '{title}': {cache_save_err}")

        except Exception as e:
            logger.error(f"Ошибка анализа главы '{title}': {e}", exc_info=True)
            analysis_errors.append(
                {
                    "title": title,
                    "error": str(e),
                    **gemini_api_error_metadata(e),
                }
            )
            mark_chapter_stage(
                db_manager,
                title,
                "analysis_done",
                "failed",
                error=str(e),
            )

    logger.info(f"Анализ завершен. Найдено терминов: {len(all_found_terms)}")
    return {"analysis_results": all_found_terms, "analysis_errors": analysis_errors}


def autonomous_curation_node(state: AgentState) -> dict:
    """
    Обрабатывает результаты анализа.
    1. Находит термины с несколькими вариантами перевода и использует LLM для выбора каноничного.
    2. Пропускает все новые, безальтернативные термины дальше как есть.
    """
    logger.info("Узел [Курирование]: Обработка результатов анализа...")
    analysis_results = state.get('analysis_results', [])
    context = state.get('app_context', {})
    llm_provider = context.get('llm_provider')
    db_manager = context.get("db_manager")
    chapters_to_process = state.get("chapters_to_process", [])

    if chapters_to_process and all(
        chapter_stage_done(state, chapter.get("title"), "glossary_updated")
        for chapter in chapters_to_process
    ):
        logger.info("Checkpoint: словарь уже обновлен для всех глав, курирование пропущено.")
        return {"unification_verdicts": []}

    if not analysis_results:
        logger.info("Результаты анализа пусты. Пропускаем курирование.")
        return {"unification_verdicts": []}

    term_variants = defaultdict(list)
    term_categories = {}
    # Собираем все варианты перевода для каждого термина
    for term_data in analysis_results:
        term_variants[term_data['english_term']].append(term_data['russian_translation'])
        term_categories.setdefault(term_data['english_term'], term_data.get('category', 'other'))

    final_verdicts = []
    conflicting_terms = {}

    # Разделяем термины на конфликтные и безальтернативные
    for term, variants in term_variants.items():
        unique_variants = set(v.lower() for v in variants)
        if len(unique_variants) > 1:
            conflicting_terms[term] = list(dict.fromkeys(variants))
        else:
            # Для безальтернативных терминов сразу создаем вердикт
            verdict = {
                "english_term": term,
                "correct_variant": variants[0],
                "category": term_categories.get(term, "other"),
                "reasoning": "New term, single option."
            }
            final_verdicts.append(verdict)

    # Если есть конфликты, разрешаем их с помощью LLM
    if conflicting_terms:
        logger.info(f"Обнаружено {len(conflicting_terms)} терминов с конфликтами. Запрос к LLM для разрешения...")

        if not llm_provider:
            logger.error("LLM провайдер не найден в контексте. Невозможно разрешить конфликты.")
            # В качестве фолбэка, выбираем самый частый вариант
            for term, variants in conflicting_terms.items():
                 chosen_variant = max(set(variants), key=variants.count)
                 verdict = {
                    "english_term": term,
                    "correct_variant": chosen_variant,
                    "category": term_categories.get(term, "other"),
                    "reasoning": "Conflict resolved by fallback (most frequent) due to missing LLM provider."
                 }
                 final_verdicts.append(verdict)
            return {"unification_verdicts": final_verdicts}

        curation_model = llm_provider.get_model('curation')

        for term, variants in conflicting_terms.items():
            prompt = f"""
            As an expert editor, you need to choose the best Russian translation for an English term from a fantasy novel.

            English Term: "{term}"

            Translation Variants: {json.dumps(variants, ensure_ascii=False)}

            Which is the most contextually appropriate and natural-sounding translation?

            Return your choice in a JSON format: {{"chosen_variant": "Your Choice"}}
            """

            try:
                response_text = generate_text(
                    curation_model,
                    prompt,
                    context.get("settings", {}),
                )
                choice_data = nodes.safe_json_loads(response_text, default={})
                chosen_variant = choice_data.get("chosen_variant")

                if chosen_variant and chosen_variant in variants:
                    reason = f"Conflict resolved by LLM. Chosen from {variants}."
                    logger.info(f"Конфликт для '{term}' разрешен LLM. Выбран вариант: '{chosen_variant}'.")
                else:
                    # Фоллбэк, если LLM вернул некорректный или отсутствующий вариант
                    chosen_variant = max(set(variants), key=variants.count)
                    reason = f"Conflict resolved by fallback (most frequent). LLM response was invalid. Variants: {variants}."
                    logger.warning(f"LLM вернул некорректный ответ для '{term}'. Выбран самый частый вариант: '{chosen_variant}'.")

            except (json.JSONDecodeError, AttributeError, Exception) as e:
                logger.error(f"Ошибка при обработке ответа LLM для термина '{term}': {e}")
                # Фоллбэк в случае любой ошибки
                chosen_variant = max(set(variants), key=variants.count)
                reason = f"Conflict resolved by fallback (most frequent) due to an error. Variants: {variants}."

            verdict = {
                "english_term": term,
                "correct_variant": chosen_variant,
                "category": term_categories.get(term, "other"),
                "reasoning": reason
            }
            final_verdicts.append(verdict)

    if not final_verdicts:
        logger.warning("После курирования не осталось вердиктов. Словарь не будет обновлен.")
    else:
        logger.info(f"Курирование завершено. Сформировано {len(final_verdicts)} вердиктов для обновления словаря.")

    for chapter_data in chapters_to_process:
        mark_chapter_stage(
            db_manager,
            chapter_data.get("title"),
            "glossary_updated",
            "done",
        )

    return {"unification_verdicts": final_verdicts}
