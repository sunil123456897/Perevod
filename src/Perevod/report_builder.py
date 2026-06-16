# src/Perevod/report_builder.py
"""Builds ``translation_report.json`` from the final graph state.

The report is the single source of truth for what happened during a run: per-
chapter stage statuses, judge results, dictionary conflicts, structured API
diagnostics, and the warnings that flag chapters for ``--retry-incomplete``.

All status-derivation logic lives here so that the graph runner only has to
collect state and call :func:`write_workflow_report`. The derivation is pure
(given ``final_state`` + ``chapter_plan`` it always returns the same report),
which makes it easy to unit-test independently of LangGraph.
"""

import json
import logging
import os
import re
from datetime import datetime, timezone

from Perevod.retry_planner import parse_errors_by_title, report_error_text
from Perevod.utils.api_errors import gemini_api_error_metadata

logger = logging.getLogger("NovelTranslator.GraphRunner")


def failed_title_from_error(error: str | None) -> str | None:
    if not error:
        return None
    match = re.search(r"(?:глав[аы]|chapter)\s+'([^']+)'", error, flags=re.IGNORECASE)
    return match.group(1) if match else None


def find_error_metadata_by_title(title: str, final_state: dict) -> dict:
    for list_key in ["context_errors", "analysis_errors", "summary_errors"]:
        for item in final_state.get(list_key, []) or []:
            if isinstance(item, dict) and item.get("title") == title:
                return item
    return {}


def _first_unprocessed_title(
    chapter_plan: list[dict], processed_by_title: dict[str, dict]
) -> str | None:
    for chapter in chapter_plan:
        if chapter.get("status") == "pending" and chapter.get("title") not in processed_by_title:
            return chapter.get("title")
    return None


def _chapter_stage_statuses(
    *,
    title: str | None,
    base_status: str,
    processed_chapter: dict | None,
    judge_titles: set[str],
    summary_titles: set[str],
    summary_errors_by_title: dict[str, str | None],
    error: str | None,
    final_state: dict | None = None,
) -> dict[str, str]:
    if base_status.startswith("skipped"):
        return {
            "context": "skipped",
            "analysis": "skipped",
            "glossary": "skipped",
            "translation": "skipped",
            "output": "skipped",
            "judge": "skipped",
            "refine": "skipped",
            "summary": "skipped",
            "memory": "skipped",
        }

    final_state = final_state or {}

    # 1. Загружаем стадии из чекпоинта SQLite (если есть)
    checkpoint_run = (final_state.get("chapter_runs") or {}).get(title) or {}
    checkpoint_stages = checkpoint_run.get("stages") or {}

    db_to_report_stage = {
        "context_retrieved": "context",
        "analysis_done": "analysis",
        "glossary_updated": "glossary",
        "translation_done": "translation",
        "output_written": "output",
        "judge_done": "judge",
        "refine_done": "refine",
        "summary_done": "summary",
        "memory_updated": "memory",
    }

    stages = {
        "context": "not_started",
        "analysis": "not_started",
        "glossary": "not_started",
        "translation": "not_started",
        "output": "not_started",
        "judge": "not_started",
        "refine": "not_started",
        "summary": "not_started",
        "memory": "not_started",
    }

    for db_key, report_key in db_to_report_stage.items():
        if db_key in checkpoint_stages:
            stages[report_key] = checkpoint_stages[db_key]

    # 2. Переопределяем стадии на основе текущих результатов рана в final_state

    # Context
    context_errors = final_state.get("context_errors") or []
    context_errors_by_title = {}
    global_context_errors = []
    for item in context_errors:
        if isinstance(item, dict):
            title_val = item.get("title")
            error_val = item.get("error")
            if title_val == "*":
                global_context_errors.append(report_error_text(error_val))
            elif title_val:
                context_errors_by_title[title_val] = report_error_text(error_val)
        elif isinstance(item, str):
            match = re.search(r"(?:chapter|глав[аы])\s+'([^']+)'", item, flags=re.IGNORECASE)
            if match:
                context_errors_by_title[match.group(1)] = item
            else:
                global_context_errors.append(item)

    if title in context_errors_by_title or global_context_errors:
        stages["context"] = "failed"
    elif title and final_state.get("chapter_contexts") and title in final_state["chapter_contexts"]:
        stages["context"] = "done"
    elif processed_chapter and stages["context"] == "not_started":
        stages["context"] = "done"

    # Analysis & Glossary
    analysis_errors = final_state.get("analysis_errors") or []
    analysis_errors_by_title = parse_errors_by_title(analysis_errors)
    if title in analysis_errors_by_title:
        stages["analysis"] = "failed"
        stages["glossary"] = "failed"
    elif processed_chapter:
        if stages["analysis"] == "not_started":
            stages["analysis"] = "done"
        if stages["glossary"] == "not_started":
            stages["glossary"] = "done"
    elif final_state.get("analysis_results") and any(item.get("source_chapter") == title for item in final_state["analysis_results"]):
        if stages["analysis"] == "not_started":
            stages["analysis"] = "done"
        if stages["glossary"] == "not_started":
            stages["glossary"] = "done"

    # Translation & Output
    if processed_chapter:
        stages["translation"] = "done"
        stages["output"] = "done"

    # Judge
    judge_by_title = {
        result.get("title"): result
        for result in final_state.get("judge_results", [])
        if result.get("title")
    }
    judge_result = judge_by_title.get(title) or (checkpoint_run.get("judge_result") if checkpoint_run else None) or {}

    if title in judge_titles or (processed_chapter and not processed_chapter.get("blocking_issues") and judge_result):
        stages["judge"] = "done"

    # Refine
    if processed_chapter and processed_chapter.get("refined"):
        stages["refine"] = "done"
    elif judge_result:
        if judge_result.get("pass_check"):
            stages["refine"] = "not_needed"
        else:
            stages["refine"] = "needed"

    # Summary & Memory
    if title in summary_errors_by_title:
        stages["summary"] = "failed"
        stages["memory"] = "failed"
    elif title in summary_titles:
        stages["summary"] = "done"
        stages["memory"] = "done"

    # 3. Обработка неперехваченных ошибок воркфлоу
    if title and error:
        failed_title = final_state.get("failed_chapter") or failed_title_from_error(error)
        if not failed_title and base_status == "pending":
            failed_title = title
        if title == failed_title:
            normalized_error = error.casefold()
            if "ошибка перевода" in normalized_error or "translation failed" in normalized_error:
                stages["translation"] = "failed"
            elif "ошибка судьи" in normalized_error or "judge failed" in normalized_error:
                stages["judge"] = "failed"
            elif "ошибка редактора" in normalized_error or "refine failed" in normalized_error:
                stages["refine"] = "failed"
            elif "ошибка анализа" in normalized_error or "analysis failed" in normalized_error:
                stages["analysis"] = "failed"
            elif "ошибка контекста" in normalized_error or "context retrieval failed" in normalized_error:
                stages["context"] = "failed"
            elif "summarization" in normalized_error or "summary" in normalized_error or "ошибка суммаризации" in normalized_error:
                stages["summary"] = "failed"
                stages["memory"] = "failed"

    return stages


def _chapter_warnings(
    *,
    title: str | None,
    context_errors_by_title: dict[str, str | None],
    global_context_errors: list[str | None],
    analysis_errors_by_title: dict[str, str | None],
    summary_errors_by_title: dict[str, str | None],
) -> list[str]:
    warnings = []
    if title in context_errors_by_title:
        warnings.append(
            f"Context retrieval was degraded: {context_errors_by_title[title]}"
        )
    warnings.extend(
        f"Context retrieval was degraded: {error}"
        for error in global_context_errors
        if error
    )
    if title in analysis_errors_by_title:
        warnings.append(
            f"Term analysis was not completed: {analysis_errors_by_title[title]}"
        )
    if title in summary_errors_by_title:
        warnings.append(
            f"Summary memory was not updated: {summary_errors_by_title[title]}"
        )
    return warnings


def remaining_quality_error(final_state: dict) -> str | None:
    active_issues = []
    judge_results = final_state.get("judge_results") or []
    judge_by_title = {r.get("title"): r for r in judge_results if r.get("title")}

    # 1. Сначала соберем ошибки из результатов judge текущего запуска
    for title, jr in judge_by_title.items():
        if jr.get("pass_check") is False:
            issues = jr.get("blocking_issues") or []
            active_issues.extend(issues or ["QA failed without blocking issue details"])

    # 2. Соберем ошибки по остальным главам из чекпоинтов chapter_runs
    chapter_runs = final_state.get("chapter_runs") or {}
    for title, run in chapter_runs.items():
        if title not in judge_by_title:
            jr = run.get("judge_result") or {}
            issues = run.get("blocking_issues") or jr.get("blocking_issues") or []
            is_failed = (
                run.get("status") == "qa_failed"
                or jr.get("pass_check") is False
                or bool(run.get("blocking_issues"))
                or bool(jr.get("blocking_issues"))
            )
            if is_failed:
                active_issues.extend(issues or ["QA failed in checkpoint"])

    # 3. Также добавим непустые blocking_issues из обработанных глав, если они там застряли
    for chapter in final_state.get("processed_chapters", []):
        title = chapter.get("title")
        if title not in judge_by_title:
            # Если это был повторный запуск (retry), и мы скопировали ошибки на вход, то в processed_chapter
            # они могут остаться, если граф был замоккан в тестах. В реальном коде они очищаются в refine/translation.
            if chapter.get("reuse_existing_translation"):
                continue
            issues = chapter.get("blocking_issues") or []
            active_issues.extend(issues)

    # 4. Обработаем старые глобальные блокирующие ошибки для необработанных в текущем запуске глав
    stale_issues = set()
    for title, jr in judge_by_title.items():
        if jr.get("pass_check") is True:
            for issue in final_state.get("blocking_issues") or []:
                if title.casefold() in issue.casefold():
                    stale_issues.add(issue)

    global_issues = final_state.get("blocking_issues") or []
    unjudged_chapters = [
        c.get("title") for c in final_state.get("chapters_to_process", [])
        if c.get("title") not in judge_by_title
    ]
    if unjudged_chapters:
        for issue in global_issues:
            if issue not in stale_issues:
                if any(uc.casefold() in issue.casefold() for uc in unjudged_chapters):
                    active_issues.append(issue)

    active_issues = list(dict.fromkeys(active_issues))
    if not active_issues:
        return None

    return (
        "QA failed after refinement limit: "
        f"{len(active_issues)} blocking issue(s) remain."
    )


def write_workflow_report(
    project_name: str,
    output_dir: str,
    chapter_plan: list[dict],
    final_state: dict,
    run_metadata: dict | None = None,
) -> str | None:
    error_obj = final_state.get("error")
    workflow_error = report_error_text(error_obj)
    error_meta = {}
    if isinstance(error_obj, Exception):
        error_meta = gemini_api_error_metadata(error_obj)
    processed_chapters = final_state.get("processed_chapters", [])
    processed_by_title = {
        chapter.get("title"): chapter for chapter in processed_chapters
    }
    judge_titles = {
        result.get("title")
        for result in final_state.get("judge_results", [])
        if result.get("title")
    }
    judge_by_title = {
        result.get("title"): result
        for result in final_state.get("judge_results", [])
        if result.get("title")
    }
    summary_titles = {
        summary.get("title")
        for summary in final_state.get("chapter_summaries", [])
        if summary.get("title")
    }
    summaries_by_title = {
        summary.get("title"): summary
        for summary in final_state.get("chapter_summaries", [])
        if summary.get("title")
    }

    # Парсим ошибки по главам
    analysis_errors_by_title = parse_errors_by_title(final_state.get("analysis_errors", []))
    summary_errors_by_title = parse_errors_by_title(final_state.get("summary_errors", []))

    context_errors = final_state.get("context_errors") or []
    context_errors_by_title = {}
    global_context_errors = []
    for item in context_errors:
        if isinstance(item, dict):
            title = item.get("title")
            error_val = item.get("error")
            if title == "*":
                global_context_errors.append(report_error_text(error_val))
            elif title:
                context_errors_by_title[title] = report_error_text(error_val)
        elif isinstance(item, str):
            match = re.search(r"(?:chapter|глав[аы])\s+'([^']+)'", item, flags=re.IGNORECASE)
            if match:
                context_errors_by_title[match.group(1)] = item
            else:
                global_context_errors.append(item)

    failed_title = final_state.get("failed_chapter") or failed_title_from_error(
        workflow_error
    )
    if workflow_error and not failed_title:
        failed_title = _first_unprocessed_title(chapter_plan, processed_by_title)

    # Соберем все обработанные главы для отчета
    report_processed_titles = []
    for chapter in chapter_plan:
        title = chapter.get("title")
        processed_chapter = processed_by_title.get(title)
        checkpoint_run = (final_state.get("chapter_runs") or {}).get(title) or {}

        is_processed = False
        if processed_chapter:
            is_processed = True
        elif checkpoint_run:
            stages = checkpoint_run.get("stages") or {}
            if stages.get("translation_done") == "done" or stages.get("translation") == "done":
                is_processed = True

        if is_processed and title:
            report_processed_titles.append(title)
    # Убираем дубликаты
    report_processed_titles = list(dict.fromkeys(report_processed_titles))

    chapter_reports = []
    for chapter in chapter_plan:
        title = chapter.get("title")
        status = chapter.get("status", "pending")
        chapter_error = None
        processed_chapter = processed_by_title.get(title)

        checkpoint_run = (final_state.get("chapter_runs") or {}).get(title) or {}
        judge_result = judge_by_title.get(title) or checkpoint_run.get("judge_result") or {}

        current_summary = summaries_by_title.get(title)
        checkpoint_summary = checkpoint_run.get("summary_result") or {}

        summary_checkpoint_reused = False
        summary_data = {}
        if current_summary:
            summary_checkpoint_reused = False
            summary_data = current_summary
        elif checkpoint_summary:
            summary_checkpoint_reused = True
            summary_data = checkpoint_summary

        refine_res = checkpoint_run.get("refine_result") or {}
        refined = bool(
            (processed_chapter or {}).get("refined")
            or refine_res.get("refined")
        )
        refinement_count = (
            (processed_chapter or {}).get("refinement_count")
            or refine_res.get("refinement_count")
            or 0
        )
        refine_checkpoint_reused = False
        if processed_chapter and (processed_chapter.get("refined") or processed_chapter.get("refinement_count", 0) > 0):
            refine_checkpoint_reused = False
        elif refine_res.get("refined") or refine_res.get("refinement_count", 0) > 0:
            refine_checkpoint_reused = True

        refine_issues_fixed = (
            (processed_chapter or {}).get("refine_issues_fixed")
            or refine_res.get("issues_fixed")
            or []
        )

        # Вычисляем стадии
        chapter_stages = _chapter_stage_statuses(
            title=title,
            base_status=chapter.get("status", "pending"),
            processed_chapter=processed_chapter,
            judge_titles=judge_titles,
            summary_titles=summary_titles,
            summary_errors_by_title=summary_errors_by_title,
            error=workflow_error,
            final_state=final_state,
        )

        # Сбор блокирующих ошибок
        seen_issues = set()
        chapter_blocking_issues = []
        if judge_result and "pass_check" in judge_result:
            source_issues = judge_result.get("blocking_issues") or []
        else:
            source_issues = (
                (processed_chapter.get("blocking_issues") or [] if processed_chapter else []) +
                (checkpoint_run.get("blocking_issues") or [] if checkpoint_run else [])
            )
        for x in source_issues:
            x_str = str(x) if isinstance(x, dict) else x
            if x_str not in seen_issues:
                seen_issues.add(x_str)
                chapter_blocking_issues.append(x)

        if judge_result and "pass_check" in judge_result:
            is_qa_failed = not judge_result.get("pass_check", True)
        else:
            is_qa_failed = (
                (checkpoint_run and checkpoint_run.get("status") == "qa_failed")
                or bool(chapter_blocking_issues)
            )

        failed_stage = None
        for stage_key, val in chapter_stages.items():
            if val == "failed":
                failed_stage = stage_key
                break

        # Специфичные ошибки стадий для этой главы
        specific_stage_error = None
        if title in context_errors_by_title:
            specific_stage_error = context_errors_by_title[title]
        elif global_context_errors:
            specific_stage_error = "; ".join(filter(None, global_context_errors))
        elif title in analysis_errors_by_title:
            specific_stage_error = analysis_errors_by_title[title]
        elif title in summary_errors_by_title:
            specific_stage_error = summary_errors_by_title[title]

        # Определение статуса и ошибок главы
        err_meta = find_error_metadata_by_title(title, final_state)
        chapter_error_category = err_meta.get("error_category") or final_state.get("error_category") or error_meta.get("error_category")
        chapter_error_retryable = err_meta.get("error_retryable")
        if chapter_error_retryable is None:
            chapter_error_retryable = final_state.get("error_retryable")
        if chapter_error_retryable is None:
            chapter_error_retryable = error_meta.get("error_retryable")
        chapter_error_status_code = err_meta.get("error_status_code") or final_state.get("error_status_code") or error_meta.get("error_status_code")
        chapter_error_operation = err_meta.get("error_operation") or final_state.get("error_operation") or error_meta.get("error_operation")
        chapter_error_model = err_meta.get("error_model") or final_state.get("error_model") or error_meta.get("error_model")

        if title == failed_title:
            if failed_stage:
                status = "failed"
                chapter_error = specific_stage_error or workflow_error or f"{failed_stage} stage failed"
            elif is_qa_failed:
                status = "qa_failed"
                if chapter_blocking_issues:
                    chapter_blocking_issues_str = []
                    for issue in chapter_blocking_issues:
                        if isinstance(issue, dict):
                            chapter_blocking_issues_str.append(issue.get("error") or issue.get("english_term") or str(issue))
                        else:
                            chapter_blocking_issues_str.append(str(issue))
                    chapter_error = f"QA failed: {', '.join(chapter_blocking_issues_str)}"
                else:
                    chapter_error = workflow_error or "Judge failed without blocking issue details"
            else:
                status = "failed"
                chapter_error = specific_stage_error or workflow_error
        elif failed_stage:
            status = "failed"
            chapter_error = specific_stage_error or checkpoint_run.get("error") or f"{failed_stage} stage failed"
            if not chapter_error_category:
                chapter_error_category = checkpoint_run.get("error_category")
            if chapter_error_retryable is None:
                chapter_error_retryable = checkpoint_run.get("error_retryable")
            if not chapter_error_status_code:
                chapter_error_status_code = checkpoint_run.get("error_status_code")
            if not chapter_error_operation:
                chapter_error_operation = checkpoint_run.get("error_operation")
            if not chapter_error_model:
                chapter_error_model = checkpoint_run.get("error_model")
        elif is_qa_failed:
            status = "qa_failed"
            if chapter_blocking_issues:
                chapter_blocking_issues_str = []
                for issue in chapter_blocking_issues:
                    if isinstance(issue, dict):
                        chapter_blocking_issues_str.append(issue.get("error") or issue.get("english_term") or str(issue))
                    else:
                        chapter_blocking_issues_str.append(str(issue))
                chapter_error = f"QA failed: {', '.join(chapter_blocking_issues_str)}"
            else:
                chapter_error = "Judge failed without blocking issue details"
        elif processed_chapter:
            status = "translated"
        elif checkpoint_run:
            db_status = checkpoint_run.get("status")
            if db_status in {"translated", "done", "summary_done", "memory_updated"}:
                status = "translated"
            elif db_status == "failed":
                status = "failed"
                chapter_error = checkpoint_run.get("error")
                if not chapter_error_category:
                    chapter_error_category = checkpoint_run.get("error_category")
                if chapter_error_retryable is None:
                    chapter_error_retryable = checkpoint_run.get("error_retryable")
                if not chapter_error_status_code:
                    chapter_error_status_code = checkpoint_run.get("error_status_code")
                if not chapter_error_operation:
                    chapter_error_operation = checkpoint_run.get("error_operation")
                if not chapter_error_model:
                    chapter_error_model = checkpoint_run.get("error_model")
            else:
                status = "translated" if chapter_stages.get("translation") == "done" else "pending"
        elif status == "pending" and workflow_error:
            status = "not_started"

        # Конфликты словаря
        chapter_conflicts = []
        if processed_chapter:
            chapter_conflicts.extend(processed_chapter.get("dictionary_conflicts") or [])
        if judge_result:
            chapter_conflicts.extend(judge_result.get("dictionary_conflicts") or [])
        if checkpoint_run:
            chapter_conflicts.extend(checkpoint_run.get("dictionary_conflicts") or [])
        unique_conflicts = {}
        for c in chapter_conflicts:
            if isinstance(c, dict) and c.get("english_term"):
                unique_conflicts[c["english_term"]] = c
        chapter_conflicts = list(unique_conflicts.values())

        # Предупреждения контекста
        chapter_context_warnings = []
        for item in final_state.get("context_warnings", []) or []:
            if isinstance(item, dict) and item.get("title") == title:
                chapter_context_warnings.append(item)
            elif isinstance(item, str):
                match = re.search(r"(?:chapter|глав[аы])\s+'([^']+)'", item, flags=re.IGNORECASE)
                if match and match.group(1) == title:
                    chapter_context_warnings.append({"title": title, "error": item})

        warnings = _chapter_warnings(
            title=title,
            context_errors_by_title=context_errors_by_title,
            global_context_errors=global_context_errors,
            analysis_errors_by_title=analysis_errors_by_title,
            summary_errors_by_title=summary_errors_by_title,
        )
        for cw in chapter_context_warnings:
            cw_err = cw.get("error") or "Context warning"
            warnings.append(f"Context retrieval was degraded: {cw_err}")

        # Добавляем предупреждение о конфликтах словаря
        for c in chapter_conflicts:
            eng = c.get("english_term") or ""
            rus_exist = c.get("existing_russian_term") or ""
            rus_cand = c.get("candidate_russian_term") or ""
            warnings.append(
                f"Dictionary conflict for '{eng}': '{rus_exist}' vs '{rus_cand}'"
            )

        warnings = list(dict.fromkeys(warnings))

        chapter_reports.append(
            {
                "title": title,
                "status": status,
                "stages": chapter_stages,
                "input_path": chapter.get("input_path"),
                "output_path": chapter.get("output_path"),
                "output_backup_path": chapter.get("output_backup_path")
                or (processed_chapter or {}).get("output_backup_path")
                or checkpoint_run.get("output_backup_path"),
                "reused_existing_translation": bool(
                    chapter.get("reuse_existing_translation")
                    or (processed_chapter or {}).get("reused_existing_translation")
                ),
                "translation_source": (processed_chapter or {}).get(
                    "translation_source"
                ),
                "translation_mode": (processed_chapter or {}).get("translation_mode"),
                "translation_chunk_count": (processed_chapter or {}).get(
                    "translation_chunk_count"
                ),
                "refined": refined,
                "refinement_count": refinement_count,
                "refine_checkpoint_reused": refine_checkpoint_reused,
                "refine_issues_fixed": refine_issues_fixed,
                "judge_pass_check": judge_result.get("pass_check"),
                "judge_score": judge_result.get("score"),
                "judge_severity": judge_result.get("severity"),
                "blocking_issues": chapter_blocking_issues,
                "quality_suggestions": judge_result.get("suggestions", []),
                "dictionary_conflicts": chapter_conflicts,
                "context_warnings": chapter_context_warnings,
                "summary_checkpoint_reused": summary_checkpoint_reused if summary_data else False,
                "summary": summary_data.get("summary"),
                "summary_key_events": summary_data.get("key_events") or summary_data.get("summary_key_events") or [],
                "summary_active_characters": summary_data.get("active_characters") or summary_data.get("summary_active_characters") or [],
                "summary_chapter_index": summary_data.get("chapter_index") or summary_data.get("summary_chapter_index"),
                "error": chapter_error,
                "error_category": chapter_error_category,
                "error_retryable": chapter_error_retryable,
                "error_status_code": chapter_error_status_code,
                "error_operation": chapter_error_operation,
                "error_model": chapter_error_model,
                "warnings": warnings,
            }
        )

    report = {
        "project_name": project_name,
        "completed_at": datetime.now(timezone.utc).isoformat(),
        "run": run_metadata or {},
        "total_chapters": len(chapter_plan),
        "processed_count": len(report_processed_titles),
        "processed_chapters": report_processed_titles,
        "failed_count": sum(
            1 for chapter in chapter_reports if chapter["status"] in {"failed", "qa_failed"}
        ),
        "warning_count": sum(len(chapter["warnings"]) for chapter in chapter_reports),
        "chapters": chapter_reports,
        "error": workflow_error,
        "error_category": final_state.get("error_category") or error_meta.get("error_category"),
        "error_retryable": final_state.get("error_retryable") if final_state.get("error_retryable") is not None else error_meta.get("error_retryable"),
        "error_status_code": final_state.get("error_status_code") or error_meta.get("error_status_code"),
        "error_operation": final_state.get("error_operation") or error_meta.get("error_operation"),
        "error_model": final_state.get("error_model") or error_meta.get("error_model"),
        "dictionary_conflicts": final_state.get("dictionary_conflicts") or [],
    }
    report_path = os.path.join(output_dir, "translation_report.json")
    temp_path = f"{report_path}.tmp"
    try:
        with open(temp_path, "w", encoding="utf-8") as report_file:
            json.dump(report, report_file, ensure_ascii=False, indent=2, default=str)
        os.replace(temp_path, report_path)
        return report_path
    except (OSError, TypeError, ValueError) as exc:
        logger.warning("Не удалось записать отчет перевода: %s", exc)
        try:
            os.remove(temp_path)
        except OSError:
            logger.debug("Не удалось удалить временный файл отчета.", exc_info=True)
        return None


# Backwards-compatible underscore aliases. These helpers historically lived in
# ``graph_runner`` with leading underscores and the test suite patches / imports
# them as ``Perevod.graph_runner._<name>``. Public names above are the canonical
# API; the private aliases preserve the existing import contract.
_failed_title_from_error = failed_title_from_error
_find_error_metadata_by_title = find_error_metadata_by_title
_first_unprocessed_title = _first_unprocessed_title
_chapter_stage_statuses = _chapter_stage_statuses
_chapter_warnings = _chapter_warnings
_parse_errors_by_title = parse_errors_by_title
_report_error_text = report_error_text
_remaining_quality_error = remaining_quality_error
_write_workflow_report = write_workflow_report
