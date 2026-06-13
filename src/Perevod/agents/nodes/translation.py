# src/Perevod/agents/nodes/translation.py
import logging
import json
import re

from Perevod.agents.state import AgentState
from Perevod.utils.text_planning import estimate_token_count, split_text_by_token_budget
from Perevod.utils.translation_quality import evaluate_translation_sanity
from Perevod.agents import nodes
from Perevod.utils.llm import clean_translation_output

logger = logging.getLogger("NovelTranslator.AgentNodes.Translation")


def _report_progress(callback, stage: str, current: int, total: int, message: str) -> None:
    if not callback:
        return
    try:
        callback(stage, current, total, message)
    except TypeError:
        try:
            percent = int((current / total) * 100) if total else 0
            callback(percent, message)
        except Exception:
            logger.warning("Progress callback failed.", exc_info=True)
    except Exception:
        logger.warning("Progress callback failed.", exc_info=True)


def _chapter_index_from_title(title: str) -> int | None:
    match = re.search(
        r"(?:^|[^A-Za-z])(?:chapter|ch)[\s._-]*(\d+)\b",
        title,
        flags=re.IGNORECASE,
    )
    return int(match.group(1)) if match else None


def _translation_chunk_budget(state: AgentState, context: dict) -> int:
    raw_budget = state.get("project_settings", {}).get(
        "translation_chunk_token_budget",
        getattr(context["settings"], "translation_chunk_token_budget", 120_000),
    )
    if isinstance(raw_budget, bool) or not isinstance(raw_budget, int | float | str):
        logger.warning(
            "Некорректный translation_chunk_token_budget=%r; используется 120000.",
            raw_budget,
        )
        return 120_000
    try:
        budget = int(raw_budget)
    except (TypeError, ValueError):
        logger.warning(
            "Некорректный translation_chunk_token_budget=%r; используется 120000.",
            raw_budget,
        )
        return 120_000
    if budget <= 0:
        logger.warning(
            "Некорректный translation_chunk_token_budget=%r; используется 120000.",
            raw_budget,
        )
        return 120_000
    return budget


def _build_translation_prompt(
    template: str,
    *,
    dictionary: str,
    context: str,
    style_section: str,
    chapter_text: str,
    chunk_notice: str = "",
) -> str:
    return template.format(
        dictionary=dictionary,
        context=context,
        style_section=style_section,
        chunk_notice=chunk_notice,
        chapter_text=chapter_text,
    )


def _plan_translation_chunks(
    template: str,
    *,
    dictionary: str,
    context: str,
    style_section: str,
    chapter_text: str,
    token_budget: int,
) -> list[str]:
    full_prompt = _build_translation_prompt(
        template,
        dictionary=dictionary,
        context=context,
        style_section=style_section,
        chapter_text=chapter_text,
    )
    if estimate_token_count(full_prompt) <= token_budget:
        return [chapter_text]

    prompt_overhead = _build_translation_prompt(
        template,
        dictionary=dictionary,
        context=context,
        style_section=style_section,
        chapter_text="",
    )
    text_budget = max(1, token_budget - estimate_token_count(prompt_overhead))
    return split_text_by_token_budget(chapter_text, text_budget)


def _tokenize_for_overlap(text: str) -> set[str]:
    return {
        token.lower()
        for token in re.findall(r"[A-Za-zА-Яа-яЁё0-9]{3,}", text)
    }


def _lexical_rerank(query_text: str, candidate_docs: list[dict]) -> list[dict]:
    query_tokens = _tokenize_for_overlap(query_text)
    if not query_tokens:
        return candidate_docs

    def score(doc: dict) -> int:
        return len(query_tokens & _tokenize_for_overlap(doc.get("text", "")))

    return sorted(candidate_docs, key=score, reverse=True)


def _load_canonical_dictionary(db_manager, verdicts: list[dict]) -> dict[str, str]:
    canonical_dictionary: dict[str, str] = {}
    try:
        existing_terms = db_manager.get_terms_dictionary()
    except Exception as exc:
        logger.warning("Не удалось загрузить словарь проекта из БД: %s", exc)
        existing_terms = {}

    if isinstance(existing_terms, dict):
        for english_term, term_data in existing_terms.items():
            if isinstance(term_data, dict):
                russian_term = (
                    term_data.get("russian_term")
                    or term_data.get("correct_variant")
                    or term_data.get("russian_translation")
                )
            else:
                russian_term = term_data
            if english_term and russian_term:
                canonical_dictionary[str(english_term)] = str(russian_term)

    canonical_dictionary.update(
        {
            verdict["english_term"]: verdict["correct_variant"]
            for verdict in verdicts
            if verdict.get("english_term") and verdict.get("correct_variant")
        }
    )
    return canonical_dictionary


def _dictionary_for_chapter(
    canonical_dictionary: dict[str, str],
    chapter_text: str,
    *,
    always_include_terms: set[str] | None = None,
) -> dict[str, str]:
    always_include_terms = always_include_terms or set()
    return {
        english_term: russian_term
        for english_term, russian_term in canonical_dictionary.items()
        if english_term in always_include_terms
        or _english_term_occurs_in_text(english_term, chapter_text)
    }


def _english_term_occurs_in_text(term: str, text: str) -> bool:
    normalized_term = (term or "").strip()
    if not normalized_term:
        return False

    escaped_term = re.escape(normalized_term)
    starts_with_word_char = bool(re.match(r"[A-Za-z0-9]", normalized_term[0]))
    ends_with_word_char = bool(re.match(r"[A-Za-z0-9]", normalized_term[-1]))
    prefix = r"(?<![A-Za-z0-9])" if starts_with_word_char else ""
    suffix = r"(?![A-Za-z0-9])" if ends_with_word_char else ""
    return re.search(f"{prefix}{escaped_term}{suffix}", text, flags=re.IGNORECASE) is not None


def translation_node(state: AgentState) -> dict:
    """
    Переводит каждую главу целиком, используя финальный словарь и контекст из базы знаний.
    """
    logger.info("Узел [Перевод]: Выполнение перевода целыми главами...")
    context = state['app_context']
    verdicts = state.get('unification_verdicts', [])
    chapters_to_process = state['chapters_to_process']
    rag_context = state.get('rag_context', '')

    db_manager = context['db_manager']
    kb_manager = context['kb_manager']
    translation_model = context['llm_provider'].get_model('translation')

    # 1. Формируем финальный канонический словарь
    canonical_dictionary = _load_canonical_dictionary(db_manager, verdicts)
    # Обновляем БД и БЗ на основе вердиктов (если они есть)
    if verdicts:
        for verdict in verdicts:
            db_manager.add_or_update_term(
                verdict['english_term'],
                verdict['correct_variant'],
                verdict.get('category', 'other'),
            )
        logger.info(f"Словарь в БД обновлен {len(verdicts)} терминами.")
        try:
            kb_manager.upsert_from_verdicts(verdicts)
            logger.info("Семантический индекс инкрементально обновлен.")
        except Exception as exc:
            logger.warning(
                "Не удалось обновить семантический индекс; перевод будет продолжен: %s",
                exc,
                exc_info=True,
            )

    # 2. Готовим шаблон промпта для перевода главы
    style_guide = (state.get("project_settings", {}).get("style_guide") or "").strip()
    style_section = (
        f"PROJECT STYLE GUIDE:\n<style_guide>\n{style_guide}\n</style_guide>\n"
        if style_guide
        else ""
    )
    new_verdict_terms = {
        verdict["english_term"] for verdict in verdicts if verdict.get("english_term")
    }
    chunk_token_budget = _translation_chunk_budget(state, context)
    translation_prompt_template = """Translate the following chapter of a fantasy novel from English to Russian.

CRITICAL INSTRUCTIONS:
1. Strictly Adhere to the Dictionary: You MUST use the translations provided in the <canonical_dictionary> for all listed terms. If a term has multiple alternative translations separated by a slash (/) or semicolon (;), you MUST choose the most natural, premium literary Russian variant suited for the context. This is the highest priority.
2. Use Knowledge Base: Use the information in <relevant_context> to maintain consistency with characters, locations, and lore.
3. Literary Quality: The translation must be literary, natural-sounding, and elegant (Senior Editor style), not a literal word-for-word machine translation. Preserve the original tone, pacing, and style.
4. RUSSIAN STYLE (HARD RULES) — these markers instantly betray machine translation and are forbidden:
   a) Em-dash (—) discipline. Do NOT use "—" as a substitute for a comma, colon, or period. Keep narrative em-dashes under ~3 per 1000 words. Restructure the sentence instead.
      BAD:  "его защита — самая сильная среди нас"   GOOD: "его защита крепче всех"
      BAD:  "энергия была чистой — гораздо лучше, чем в пещере"  GOOD: "энергия была куда чище, чем в его пещере"
      (A dash opening a dialogue line "— ..." is correct and allowed.)
   b) Gerunds (деепричастные обороты). Do NOT stack them and avoid starting sentences with "Покинув...", "Сосредоточив...", "Завершив...". Convert most gerunds to finite verbs or split into separate sentences. Keep gerund density under ~2.5 per 1000 words.
      BAD:  "Сосредоточив внимание на лапе, Лу Сюань увидел..."
      GOOD: "Лу Сюань сосредоточил внимание на лапе и увидел..."
   c) Filler connectors. Cut "впрочем", "однако", "тем не менее", "разумеется", "к слову", "к счастью", "ведь" wherever the sentence works without them.
   d) Similes. At most ONE comparison (словно / будто / подобно) per paragraph. Prefer concrete verbs over "like a ..." imagery.
   e) Do NOT invent content. Never add emotions, adverbs, or assessments absent from the source — no "ликуя про себя", "невольно вздохнул", "воодушевлённый", "эмоционально прокомментировал" unless the English explicitly carries that meaning.
   f) Do NOT convert narrative into dialogue. If the original sentence is narration (e.g. "Lu Xuan respectfully greeted the cultivator."), keep it narration — do NOT turn it into a spoken line ("— Приветствую вас, — сказал...").
   g) No aphoristic "punchline" sentences that the source did not have, and no "не только... но и" / "с одной стороны... с другой" formulas unless present in the source.
   Also NEVER use these literal phrases: "стоит отметить", "важно понимать", "следует отметить", "неудивительно, что", "в заключение".
5. Completeness: Translate every sentence, paragraph, dialogue line, number, name, and detail. Do not summarize, omit, merge, or explain the chapter.
6. Formatting: Preserve paragraph breaks and original formatting.

<canonical_dictionary>
{dictionary}
</canonical_dictionary>

<relevant_context>
{context}
</relevant_context>

{style_section}
{chunk_notice}
<english_chapter_to_translate>
{chapter_text}
</english_chapter_to_translate>

FULL RUSSIAN TRANSLATION OF THE CHAPTER (Output ONLY the translated text, no explanations, no tags):
"""
    processed_chapters = []
    progress_callback = state.get("progress_callback")
    total_chapters = len(chapters_to_process)
    workflow_error = None
    nodes._report_progress(
        progress_callback,
        "translation",
        0,
        total_chapters,
        "Запуск перевода глав",
    )
    # 3. Итерируемся по главам и переводим каждую
    for i, chapter_data in enumerate(chapters_to_process):
        title = chapter_data['title']
        logger.info(f"--- Обработка главы [{i+1}/{len(chapters_to_process)}]: {title} ---")

        try:
            nodes._report_progress(
                progress_callback,
                "translation",
                i,
                total_chapters,
                f"Перевод главы '{title}'",
            )

            # Читаем текст главы
            eng_text = nodes.tool_read_chapter(chapter_data['input_path'])
            if not eng_text.strip():
                logger.warning(f"Глава '{title}' пуста, пропускаем.")
                nodes._report_progress(
                    progress_callback,
                    "translation",
                    i + 1,
                    total_chapters,
                    f"Глава '{title}' пропущена",
                )
                continue

            chapter_dictionary = nodes._dictionary_for_chapter(
                canonical_dictionary,
                eng_text,
                always_include_terms=new_verdict_terms,
            )
            dictionary_text = json.dumps(
                chapter_dictionary,
                ensure_ascii=False,
                indent=2,
            )
            if "chapter_contexts" in state:
                chapter_context = state.get("chapter_contexts", {}).get(title) or ""
            else:
                chapter_context = rag_context
            raw_temp = state.get("project_settings", {}).get(
                "temperature",
                getattr(context["settings"], "temperature", 0.7),
            )
            if hasattr(raw_temp, "_mock_return_value") or not isinstance(raw_temp, int | float | str):
                temperature = 0.7
            else:
                try:
                    temperature = float(raw_temp)
                except (TypeError, ValueError):
                    temperature = 0.7

            raw_top_p = state.get("project_settings", {}).get(
                "top_p",
                getattr(context["settings"], "top_p", 0.9),
            )
            if hasattr(raw_top_p, "_mock_return_value") or not isinstance(raw_top_p, int | float | str):
                top_p = 0.9
            else:
                try:
                    top_p = float(raw_top_p)
                except (TypeError, ValueError):
                    top_p = 0.9

            generation_settings = {
                "temperature": temperature,
                "top_p": top_p,
            }
            model_name = context['llm_provider'].model_configs.get('translation', '')
            chapter_cache_key = nodes.generate_translation_cache_key(
                {
                    "original_chunk": eng_text,
                    "dictionary": chapter_dictionary,
                    "relevant_context": chapter_context,
                    "style_guide": style_guide,
                    "generation_settings": generation_settings,
                },
                model_name,
            )
            cached_translation = db_manager.get_from_cache(chapter_cache_key)

            reused_existing_translation = False
            translation_source = "api"
            translation_mode = "whole_chapter"
            translation_chunk_count = 1
            if chapter_data.get("reuse_existing_translation"):
                translated_text = nodes.tool_read_chapter(chapter_data["output_path"])
                reused_existing_translation = True
                translation_source = "existing_file"
                translation_mode = "existing_file"
                translation_chunk_count = 0
                logger.info(
                    "Повторный запуск: используем существующий перевод главы '%s'.",
                    title,
                )
            elif cached_translation:
                logger.info(f"КЭШ: Найден готовый перевод для главы '{title}'.")
                translated_text = cached_translation
                translation_source = "cache"
                translation_mode = "cache"
                translation_chunk_count = 0
            else:
                # Если в кэше нет, делаем запрос к API
                logger.info(f"Перевод главы '{title}' (запрос к API)...")

                translation_chunks = nodes._plan_translation_chunks(
                    translation_prompt_template,
                    dictionary=dictionary_text,
                    context=chapter_context,
                    style_section=style_section,
                    chapter_text=eng_text,
                    token_budget=chunk_token_budget,
                )
                translation_chunk_count = len(translation_chunks)
                translation_mode = (
                    "chunked" if translation_chunk_count > 1 else "whole_chapter"
                )
                if len(translation_chunks) > 1:
                    logger.info(
                        "Глава '%s' превышает token budget; переводим в %s частях.",
                        title,
                        translation_chunk_count,
                    )

                translated_parts = []
                for chunk_index, chunk_text in enumerate(translation_chunks, start=1):
                    chunk_notice = ""
                    if len(translation_chunks) > 1:
                        chunk_notice = (
                            f"NOTE: This is part {chunk_index}/{len(translation_chunks)} "
                            "of the same chapter. Translate only this part, preserve "
                            "continuity, and do not summarize or add commentary.\n"
                        )
                    prompt = _build_translation_prompt(
                        translation_prompt_template,
                        dictionary=dictionary_text,
                        context=chapter_context,
                        style_section=style_section,
                        chunk_notice=chunk_notice,
                        chapter_text=chunk_text,
                    )
                    translated_part = clean_translation_output(
                        nodes.tool_translate_chunk(
                            translation_model,
                            prompt,
                            context['settings'],
                        )
                    )
                    if len(translation_chunks) > 1 and not translated_part:
                        raise ValueError(
                            f"часть {chunk_index}/{len(translation_chunks)} "
                            "вернула пустой перевод"
                        )
                    translated_parts.append(translated_part)

                translated_text = "\n\n".join(
                    part for part in translated_parts if part
                )

            # Записываем результат в файл
            if not translated_text or not translated_text.strip():
                if reused_existing_translation:
                    source_label = "существующий файл"
                elif translation_source == "cache":
                    source_label = "кэш"
                else:
                    source_label = "API"
                workflow_error = (
                    f"Ошибка перевода главы '{title}': {source_label} вернул пустой перевод"
                )
                logger.error(workflow_error)
                if translation_source == "cache":
                    try:
                        db_manager.delete_from_cache(chapter_cache_key)
                    except Exception as exc:
                        logger.warning(
                            "Не удалось удалить пустой перевод главы '%s' из кэша: %s",
                            title,
                            exc,
                            exc_info=True,
                        )
                nodes._report_progress(
                    progress_callback,
                    "translation",
                    i,
                    total_chapters,
                    workflow_error,
                )
                break

            if translation_source == "api":
                sanity_result = evaluate_translation_sanity(
                    eng_text,
                    translated_text,
                    chapter_dictionary,
                )
                if sanity_result.blocking_issues:
                    logger.warning(
                        "Перевод главы '%s' не будет сохранен в кэш до прохождения QA: %s",
                        title,
                        "; ".join(sanity_result.blocking_issues),
                    )

            backup_path = None
            if not reused_existing_translation:
                if chapter_data.get("backup_existing_output"):
                    backup_path = nodes.tool_backup_file(chapter_data['output_path'])
                nodes.tool_write_chapter(chapter_data['output_path'], translated_text)
                logger.info(f"Глава '{title}' успешно переведена и записана in {chapter_data['output_path']}")

            from Perevod.agents.checkpoints import mark_chapter_stage
            mark_chapter_stage(db_manager, title, "translation_done", "done")
            mark_chapter_stage(db_manager, title, "output_written", "done")

            processed_chapters.append(
                {
                    **chapter_data,
                    "cache_key": chapter_cache_key,
                    "relevant_context": chapter_context,
                    "output_backup_path": backup_path,
                    "reused_existing_translation": reused_existing_translation,
                    "translation_source": translation_source,
                    "translation_mode": translation_mode,
                    "translation_chunk_count": translation_chunk_count,
                }
            )
            nodes._report_progress(
                progress_callback,
                "translation",
                i + 1,
                total_chapters,
                f"Глава '{title}' переведена",
            )
        except Exception as e:
            workflow_error = f"Ошибка перевода главы '{title}': {e}"
            logger.error(workflow_error, exc_info=True)
            nodes._report_progress(
                progress_callback,
                "translation",
                i,
                total_chapters,
                workflow_error,
            )
            break

    return {"processed_chapters": processed_chapters, "error": workflow_error}
