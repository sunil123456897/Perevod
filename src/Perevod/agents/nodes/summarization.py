# src/Perevod/agents/nodes/summarization.py
import logging

from Perevod.agents.state import AgentState
from Perevod.agents.checkpoints import (
    chapter_stage_done,
    mark_chapter_stage,
    update_chapter_summary_result,
)
from Perevod.utils.llm import generate_text, safe_json_loads
from Perevod.utils.api_errors import gemini_api_error_metadata
from Perevod.schemas import ChapterSummary
from Perevod.agents.nodes.translation import _chapter_index_from_title

logger = logging.getLogger("NovelTranslator.AgentNodes.Summarization")


def summarization_node(state: AgentState) -> dict:
    """Generates a summary for each translated chapter and stores it in the KB."""
    from Perevod.agents.nodes import tool_read_chapter
    logger.info("Узел [Саммари]: Генерация саммари глав...")
    if state.get("error"):
        return {"error": state["error"]}

    context = state["app_context"]
    processed_chapters = state.get("processed_chapters", [])
    kb_manager = context["kb_manager"]
    db_manager = context.get("db_manager")
    project_name = state.get("project_name", "default")

    summaries = []
    summary_errors = []

    if not processed_chapters:
        return {
            "chapter_summaries": [],
            "summary_errors": []
        }

    if not kb_manager:
        error = "Knowledge base manager is unavailable; chapter memory was not updated."
        for chapter_data in processed_chapters:
            title = chapter_data.get("title", "Untitled")
            summary_errors.append({"title": title, "error": error})
            mark_chapter_stage(
                db_manager,
                title,
                "summary_done",
                "failed",
                error=error,
            )
            mark_chapter_stage(
                db_manager,
                title,
                "memory_updated",
                "failed",
                error=error,
            )
        return {
            "chapter_summaries": summaries,
            "summary_errors": summary_errors,
        }

    # Try to get 'summarization' model, fallback to 'qa'
    try:
        summary_model = context["llm_provider"].get_model("summarization")
    except ValueError:
        summary_model = context["llm_provider"].get_model("qa")

    summarization_prompt_template = """You are a professional editor. Summarize the following chapter.
Return ONLY a valid JSON object matching this schema:
{
    "title": "Chapter title",
    "summary": "Brief summary",
    "key_events": ["Event 1", "Event 2"],
    "active_characters": ["Char 1", "Char 2"]
}
"""

    for chapter_data in processed_chapters:
        title = chapter_data.get("title", "Untitled")
        output_path = chapter_data.get("output_path")
        checkpoint_summary_result = (
            ((state.get("chapter_runs") or {}).get(title) or {}).get("summary_result")
            or {}
        )

        if chapter_stage_done(state, title, "summary_done") and chapter_stage_done(
            state,
            title,
            "memory_updated",
        ):
            logger.info("Checkpoint: саммари главы '%s' уже выполнено, пропуск.", title)
            if checkpoint_summary_result:
                summaries.append(
                    {
                        **checkpoint_summary_result,
                        "checkpoint_reused": True,
                    }
                )
            else:
                summaries.append({"title": title, "checkpoint_reused": True})
            continue

        try:
            translated_text = tool_read_chapter(output_path)
            if not translated_text.strip():
                error = "Translated output is empty; chapter memory was not updated."
                summary_errors.append({
                    "title": title,
                    "error": error
                })
                mark_chapter_stage(
                    db_manager,
                    title,
                    "summary_done",
                    "failed",
                    error=error,
                )
                continue

            response_text = generate_text(
                summary_model,
                summarization_prompt_template + f"\nCHAPTER TEXT:\n{translated_text}",
                context.get("settings", {}),
            )
            parsed_response = safe_json_loads(response_text, default={})
            
            # Если в ответе нет title, подставляем текущий title главы
            if not parsed_response.get("title"):
                parsed_response["title"] = title
                
            chapter_summary = ChapterSummary.model_validate(parsed_response)
            summary_dict = chapter_summary.model_dump()
            summaries.append(summary_dict)

            # Формируем текст саммари для записи в базу знаний
            summary_text = (
                f"Chapter Summary: {chapter_summary.title}\n"
                f"Summary: {chapter_summary.summary}\n"
                f"Key Events: {', '.join(chapter_summary.key_events)}\n"
                f"Active Characters: {', '.join(chapter_summary.active_characters)}"
            )

            # Вычисляем индекс главы
            ch_idx = _chapter_index_from_title(title)
            if ch_idx is None:
                # Ищем максимальный индекс в существующих воспоминаниях
                existing_indices = []
                if kb_manager and kb_manager.collection:
                    try:
                        existing = kb_manager.collection.get(where={"type": "chapter_memory"})
                        if existing and "metadatas" in existing:
                            for m in existing["metadatas"]:
                                if m and "chapter_index" in m:
                                    existing_indices.append(m["chapter_index"])
                    except Exception as kb_get_err:
                        logger.warning(f"Не удалось получить существующие воспоминания: {kb_get_err}")
                
                max_existing = max(existing_indices) if existing_indices else -1
                ch_idx = max_existing + 1

            if kb_manager:
                kb_manager.add_or_update_entries(
                    documents=[summary_text],
                    metadatas=[
                        {
                            "type": "chapter_memory",
                            "title": title,
                            "chapter_index": ch_idx,
                        }
                    ],
                    ids=[f"memory_{project_name}_{title}"],
                )
                logger.info(f"Саммари для главы '{title}' добавлено в БЗ.")
                mark_chapter_stage(db_manager, title, "summary_done", "done")
                mark_chapter_stage(db_manager, title, "memory_updated", "done")
                update_chapter_summary_result(
                    db_manager,
                    title,
                    {
                        **summary_dict,
                        "chapter_index": ch_idx,
                    },
                )

        except Exception as e:
            logger.error(f"Ошибка Саммари для главы '{title}': {e}", exc_info=True)
            summary_errors.append(
                {
                    "title": title,
                    "error": str(e),
                    **gemini_api_error_metadata(e),
                }
            )
            mark_chapter_stage(
                db_manager,
                title,
                "summary_done",
                "failed",
                error=str(e),
            )

    return {
        "chapter_summaries": summaries,
        "summary_errors": summary_errors
    }

