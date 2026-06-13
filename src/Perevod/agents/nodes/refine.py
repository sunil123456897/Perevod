# src/Perevod/agents/nodes/refine.py
import logging

from Perevod.agents.state import AgentState
from Perevod.agents.checkpoints import (
    chapter_stage_done,
    mark_chapter_stage,
    update_chapter_refine_result,
)
from Perevod.agents.nodes.translation import _report_progress
from Perevod.utils.api_errors import gemini_api_error_metadata
from Perevod.utils.llm import generate_text

logger = logging.getLogger("NovelTranslator.AgentNodes.Refine")


def refine_node(state: AgentState) -> dict:
    """Corrects translation errors identified by the Judge."""
    from Perevod.agents.nodes import tool_read_chapter, tool_write_chapter, clean_translation_output
    logger.info("Узел [Редактор]: Исправление ошибок перевода...")
    if state.get("error"):
        return {"error": state["error"]}

    blocking_issues = state.get("blocking_issues", [])
    if not blocking_issues:
        logger.info("Блокирующих ошибок не найдено. Пропуск уточнения.")
        return {}

    context = state["app_context"]
    processed_chapters = state.get("processed_chapters", [])
    db_manager = context["db_manager"]
    kb_manager = context["kb_manager"]
    progress_callback = state.get("progress_callback")

    # Try to get 'editor' model, fallback to 'translation'
    try:
        editor_model = context["llm_provider"].get_model("editor")
    except ValueError:
        editor_model = context["llm_provider"].get_model("translation")

    refinement_count = state.get("refinement_count", 0) + 1

    editor_prompt_template = """You are the Senior Editor for a fantasy novel.
The translation has the following critical issues that MUST be fixed:
{issues}

ORIGINAL ENGLISH:
{original_text}

CURRENT RUSSIAN TRANSLATION:
{translated_text}

DIRECTIONS:
1. Fix all technical and consistency issues.
2. Maintain high literary quality.
3. Return ONLY the full corrected Russian text. No explanations.
"""

    total_chapters = len(processed_chapters)
    workflow_error = None
    workflow_error_metadata = {}
    _report_progress(
        progress_callback,
        "refine",
        0,
        total_chapters,
        f"Запуск уточнения (итерация {refinement_count})",
    )

    for index, chapter_data in enumerate(processed_chapters):
        title = chapter_data.get("title", "Untitled")
        chapter_issues = chapter_data.get("blocking_issues", [])
        checkpoint_refine_result = (
            ((state.get("chapter_runs") or {}).get(title) or {}).get("refine_result")
            or {}
        )

        if not chapter_issues:
            logger.info(f"Глава '{title}' не имеет блокирующих ошибок. Пропуск.")
            continue
        if chapter_stage_done(state, title, "refine_done"):
            logger.info("Checkpoint: редактура главы '%s' уже выполнена, пропуск.", title)
            chapter_data["refined"] = True
            chapter_data["checkpoint_reused"] = True
            if checkpoint_refine_result:
                chapter_data["refine_result"] = checkpoint_refine_result
            continue

        try:
            _report_progress(
                progress_callback,
                "refine",
                index,
                total_chapters,
                f"Уточнение главы '{title}'",
            )

            original_text = tool_read_chapter(chapter_data["input_path"])
            translated_text = tool_read_chapter(chapter_data["output_path"])

            issues_text = "\n".join(f"- {issue}" for issue in chapter_issues)
            prompt = editor_prompt_template.format(
                issues=issues_text,
                original_text=original_text,
                translated_text=translated_text,
            )

            response_text = generate_text(
                editor_model,
                prompt,
                context.get("settings", {}),
            )
            corrected_text = clean_translation_output(response_text)

            if not corrected_text:
                raise ValueError("редактор вернул пустую правку")

            tool_write_chapter(chapter_data["output_path"], corrected_text)
            chapter_data["refined"] = True
            refine_result = {
                "refined": True,
                "refinement_count": refinement_count,
                "issues_fixed": list(chapter_issues),
            }
            chapter_data["refine_result"] = refine_result
            chapter_data["blocking_issues"] = []
            logger.info(f"Редактор исправил главу '{title}'.")

            # Keep editor corrections out of the translation cache until judge_node
            # approves the corrected file on the next QA pass.
            if chapter_data.get("cache_key"):
                cache_key = chapter_data["cache_key"]
                try:
                    # Update KB with correction context
                    kb_manager.add_or_update_entries(
                        documents=[
                            "Editor Correction. "
                            f"Chapter: {title}. "
                            f"Issues fixed: {issues_text}. "
                            f"Corrected Russian text: {corrected_text}"
                        ],
                        metadatas=[{"source": "editor", "name": title}],
                        ids=[f"editor_{cache_key}_{refinement_count}"],
                    )
                except Exception as exc:
                    logger.warning(
                        "Не удалось сохранить память о редакторской правке главы '%s': %s",
                        title,
                        exc,
                        exc_info=True,
                    )

            _report_progress(
                progress_callback,
                "refine",
                index + 1,
                total_chapters,
                f"Уточнение главы '{title}' завершено",
            )
            mark_chapter_stage(db_manager, title, "refine_done", "done")
            update_chapter_refine_result(db_manager, title, refine_result)
        except Exception as e:
            workflow_error = f"Ошибка Редактора для главы '{title}': {e}"
            workflow_error_metadata = gemini_api_error_metadata(e)
            logger.error(workflow_error, exc_info=True)
            mark_chapter_stage(
                db_manager,
                title,
                "refine_done",
                "failed",
                error=workflow_error,
            )
            _report_progress(
                progress_callback,
                "refine",
                index,
                total_chapters,
                workflow_error,
            )
            break

    result = {
        "processed_chapters": processed_chapters,
        "blocking_issues": [],
        "refinement_count": refinement_count,
    }
    if workflow_error:
        result["error"] = workflow_error
        result.update(workflow_error_metadata)
    return result
