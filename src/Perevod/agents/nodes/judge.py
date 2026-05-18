# src/Perevod/agents/nodes/judge.py
import logging
import json

from Perevod.agents.state import AgentState
from Perevod.schemas import JudgeResult
from Perevod.utils.llm import safe_json_loads
from Perevod.utils.translation_quality import evaluate_translation_sanity, merge_severity
from Perevod.agents.nodes.translation import _load_canonical_dictionary

logger = logging.getLogger("NovelTranslator.AgentNodes.Judge")


def judge_node(state: AgentState) -> dict:
    """Analyzes translation quality and identifies blocking issues."""
    from Perevod.agents.nodes import tool_read_chapter
    logger.info("Узел [Судья]: Оценка качества перевода...")
    if state.get("error"):
        return {"error": state["error"]}

    context = state["app_context"]
    processed_chapters = state.get("processed_chapters", [])
    verdicts = state.get("unification_verdicts", [])
    canonical_dictionary = _load_canonical_dictionary(context["db_manager"], verdicts)

    # Try to get 'judge' model, fallback to 'qa'
    try:
        judge_model = context["llm_provider"].get_model("judge")
    except ValueError:
        judge_model = context["llm_provider"].get_model("qa")

    judge_results = []
    all_blocking_issues = []
    workflow_error = None

    judge_prompt_template = """You are the Quality Judge for an English to Russian fantasy novel translation.
Compare the English Original and the Russian Translation.

CRITERIA:
1. Omissions: No sentences or paragraphs should be missing.
2. Terms: Use the translations provided in the dictionary.
3. Synonym Detection (Self-Learning): If the translation did not use the literal term from the dictionary, but instead translated it using an elegant, correct, and highly literary contextual synonym/alternative in Russian, you MUST detect it, approve it (do not mark as an error/blocking issue), and report it in the "synonym_updates" field.
4. Names/Gender: Ensure characters have correct names and gender consistency.
5. Fluency: The Russian text should be literary, natural, and fluent.
6. AI-slop: Avoid phrases like "Стоит отметить", "Важно понимать", "Неудивительно, что", "в заключение".

CANONICAL DICTIONARY:
{dictionary}

RELEVANT CONTEXT:
{context}

ENGLISH ORIGINAL:
{original_text}

RUSSIAN TRANSLATION:
{translated_text}

Return ONLY valid JSON with this shape:
{{
  "pass_check": bool,
  "severity": "low|medium|high|critical",
  "blocking_issues": ["list of issues"],
  "suggestions": ["style improvements"],
  "score": 0-10,
  "synonym_updates": [
    {{
      "english_term": "English term from dictionary",
      "found_translation": "The actual valid, high-quality Russian synonym used in the translation"
    }}
  ]
}}
"""

    for chapter_data in processed_chapters:
        title = chapter_data.get("title", "Untitled")
        try:
            original_text = tool_read_chapter(chapter_data["input_path"])
            translated_text = tool_read_chapter(chapter_data["output_path"])
            sanity_result = evaluate_translation_sanity(
                original_text,
                translated_text,
                canonical_dictionary,
            )

            prompt = judge_prompt_template.format(
                dictionary=json.dumps(
                    canonical_dictionary, ensure_ascii=False, indent=2
                ),
                context=chapter_data.get("relevant_context", ""),
                original_text=original_text,
                translated_text=translated_text,
            )

            response = judge_model.generate_content(prompt)
            parsed_response = safe_json_loads(getattr(response, "text", ""), default={})
            judge_result = JudgeResult.model_validate(parsed_response)

            result_dict = judge_result.model_dump()

            # --- Auto-Update Synonyms & Re-evaluate Sanity ---
            synonym_updates = result_dict.get("synonym_updates", [])
            has_db_updates = False
            for update in synonym_updates:
                eng_term = update.get("english_term")
                found_trans = update.get("found_translation")
                if eng_term and found_trans:
                    # Verify that the synonym occurs in the translated text
                    from Perevod.utils.translation_quality import _term_occurs_in_text
                    if _term_occurs_in_text(found_trans, translated_text):
                        current_val = canonical_dictionary.get(eng_term, "")
                        if current_val:
                            import re
                            existing_variants = [v.strip().lower() for v in re.split(r"/|;", current_val) if v.strip()]
                            if found_trans.strip().lower() not in existing_variants:
                                new_val = f"{current_val} / {found_trans.strip()}"
                                context["db_manager"].add_or_update_term(eng_term, new_val)
                                logger.info(
                                    "AUTO-GLOSSARY: Добавлен новый синоним '%s' -> '%s' (полное значение: '%s')",
                                    eng_term, found_trans, new_val
                                )
                                canonical_dictionary[eng_term] = new_val
                                has_db_updates = True

            # If there were synonym updates, re-evaluate sanity check with updated dictionary
            if has_db_updates:
                sanity_result = evaluate_translation_sanity(
                    original_text,
                    translated_text,
                    canonical_dictionary,
                )

            blocking_issues = list(
                dict.fromkeys(
                    sanity_result.blocking_issues + result_dict["blocking_issues"]
                )
            )
            suggestions = list(
                dict.fromkeys(sanity_result.suggestions + result_dict["suggestions"])
            )
            result_dict["pass_check"] = judge_result.pass_check and not blocking_issues
            result_dict["severity"] = merge_severity(
                sanity_result.severity,
                judge_result.severity,
            )
            result_dict["blocking_issues"] = blocking_issues
            result_dict["suggestions"] = suggestions
            result_dict["score"] = min(result_dict["score"], sanity_result.score_cap)
            result_dict["title"] = title
            judge_results.append(result_dict)

            # Store issues per chapter
            chapter_data["blocking_issues"] = blocking_issues

            if blocking_issues:
                all_blocking_issues.extend(blocking_issues)
                if (
                    chapter_data.get("cache_key")
                    and chapter_data.get("translation_source") == "cache"
                ):
                    try:
                        context["db_manager"].delete_from_cache(
                            chapter_data["cache_key"]
                        )
                        logger.warning(
                            "QA заблокировала cached перевод главы '%s'; запись удалена из кэша.",
                            title,
                        )
                    except Exception as exc:
                        logger.warning(
                            "Не удалось удалить QA-blocked перевод главы '%s' из кэша: %s",
                            title,
                            exc,
                            exc_info=True,
                        )
            elif (
                chapter_data.get("cache_key")
                and chapter_data.get("translation_source") in {"api", "existing_file"}
            ):
                try:
                    context["db_manager"].add_to_cache(
                        chapter_data["cache_key"],
                        translated_text,
                    )
                    logger.info(
                        "QA пройдена: перевод главы '%s' сохранен в кэш.",
                        title,
                    )
                except Exception as exc:
                    logger.warning(
                        "Не удалось сохранить QA-approved перевод главы '%s' в кэш: %s",
                        title,
                        exc,
                        exc_info=True,
                    )

        except Exception as e:
            workflow_error = f"Ошибка Судьи для главы '{title}': {e}"
            logger.error(workflow_error, exc_info=True)
            break

    return {
        "processed_chapters": processed_chapters,
        "judge_results": judge_results,
        "blocking_issues": list(dict.fromkeys(all_blocking_issues)),
        "error": workflow_error,
    }
