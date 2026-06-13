# src/Perevod/graph_runner.py
import logging
import os
import json
import contextlib
import re
import ctypes
from datetime import datetime, timezone
from langgraph.graph import StateGraph, END

from Perevod.agents.state import AgentState, AppContext
from Perevod.agents.nodes import (
    analysis_node,
    autonomous_curation_node,
    translation_node,
    judge_node,
    refine_node,
    context_retrieval_node,
    summarization_node,
)
from Perevod.api_usage import is_placeholder_api_key
from Perevod.utils.api_errors import gemini_api_error_metadata
from Perevod.database.database_manager import DatabaseManager
from Perevod.knowledge_base.knowledge_base_manager import KnowledgeBaseManager
from Perevod.config import normalize_embedding_model, normalize_model_configs, settings
from Perevod.llm_provider import LLMProvider

logger = logging.getLogger("NovelTranslator.GraphRunner")

# Определяем имена узлов
ANALYSIS = "analysis"
CURATION = "autonomous_curation"
TRANSLATION = "translation"
JUDGE = "judge"
REFINE = "refine"
CONTEXT_RETRIEVAL = "context_retrieval"
SUMMARIZATION = "summarization"
MALFORMED_LOCK_STALE_AFTER_SECONDS = 15 * 60


def _acquire_workflow_lock(output_dir: str) -> str:
    lock_path = os.path.join(output_dir, ".translation.lock")
    try:
        fd = _create_lock_file(lock_path)
    except FileExistsError as exc:
        lock_pid = _read_lock_pid(lock_path)
        if _can_remove_existing_lock(lock_path, lock_pid):
            logger.warning(
                "Удаление устаревшего lock-файла '%s' от PID %s.",
                lock_path,
                lock_pid or "unknown",
            )
            os.remove(lock_path)
            fd = _create_lock_file(lock_path)
        else:
            raise RuntimeError(
                f"Translation is already running for output directory: {output_dir}"
            ) from exc
    with os.fdopen(fd, "w", encoding="utf-8") as lock_file:
        lock_file.write(f"pid={os.getpid()}\n")
        lock_file.write(f"started_at={datetime.now(timezone.utc).isoformat()}\n")
    return lock_path


def _create_lock_file(lock_path: str) -> int:
    return os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)


def _read_lock_pid(lock_path: str) -> int | None:
    try:
        with open(lock_path, encoding="utf-8") as lock_file:
            for line in lock_file:
                if line.startswith("pid="):
                    return int(line.split("=", 1)[1].strip())
    except (OSError, ValueError):
        return None
    return None


def _can_remove_existing_lock(lock_path: str, lock_pid: int | None) -> bool:
    if lock_pid:
        return not _is_process_running(lock_pid)
    try:
        lock_age_seconds = datetime.now(timezone.utc).timestamp() - os.path.getmtime(
            lock_path
        )
    except OSError:
        return False
    return lock_age_seconds >= MALFORMED_LOCK_STALE_AFTER_SECONDS


def _is_process_running(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name == "nt":
        process_query_limited_information = 0x1000
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.OpenProcess(
            process_query_limited_information,
            False,
            pid,
        )
        if handle:
            kernel32.CloseHandle(handle)
            return True
        return kernel32.GetLastError() == 5

    try:
        os.kill(pid, 0)
    except OSError:
        return False
    except ValueError:
        return False
    return True


def _release_workflow_lock(lock_path: str | None) -> None:
    if not lock_path:
        return
    lock_pid = _read_lock_pid(lock_path)
    if lock_pid != os.getpid():
        if os.path.exists(lock_path):
            logger.warning(
                "Lock-файл '%s' не удален: владелец PID %s, текущий PID %s.",
                lock_path,
                lock_pid or "unknown",
                os.getpid(),
            )
        return
    with contextlib.suppress(FileNotFoundError):
        os.remove(lock_path)


def _report_error_text(error) -> str | None:
    if error is None or isinstance(error, str):
        return error
    return str(error)


def _parse_errors_by_title(error_list: list) -> dict[str, str]:
    errors_by_title = {}
    for item in error_list or []:
        if isinstance(item, dict):
            title = item.get("title")
            error_val = item.get("error")
            if title and title != "*":
                errors_by_title[title] = _report_error_text(error_val)
        elif isinstance(item, str):
            match = re.search(r"(?:chapter|глав[аы])\s+'([^']+)'", item, flags=re.IGNORECASE)
            if match:
                errors_by_title[match.group(1)] = item
    return errors_by_title


def _find_error_metadata_by_title(title: str, final_state: dict) -> dict:
    for list_key in ["context_errors", "analysis_errors", "summary_errors"]:
        for item in final_state.get(list_key, []) or []:
            if isinstance(item, dict) and item.get("title") == title:
                return item
    return {}


def _write_workflow_report(
    project_name: str,
    output_dir: str,
    chapter_plan: list[dict],
    final_state: dict,
    run_metadata: dict | None = None,
) -> str | None:
    error_obj = final_state.get("error")
    workflow_error = _report_error_text(error_obj)
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
    analysis_errors_by_title = _parse_errors_by_title(final_state.get("analysis_errors", []))
    summary_errors_by_title = _parse_errors_by_title(final_state.get("summary_errors", []))
    
    context_errors = final_state.get("context_errors") or []
    context_errors_by_title = {}
    global_context_errors = []
    for item in context_errors:
        if isinstance(item, dict):
            title = item.get("title")
            error_val = item.get("error")
            if title == "*":
                global_context_errors.append(_report_error_text(error_val))
            elif title:
                context_errors_by_title[title] = _report_error_text(error_val)
        elif isinstance(item, str):
            match = re.search(r"(?:chapter|глав[аы])\s+'([^']+)'", item, flags=re.IGNORECASE)
            if match:
                context_errors_by_title[match.group(1)] = item
            else:
                global_context_errors.append(item)

    failed_title = final_state.get("failed_chapter") or _failed_title_from_error(
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
        err_meta = _find_error_metadata_by_title(title, final_state)
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


def _failed_title_from_error(error: str | None) -> str | None:
    if not error:
        return None
    match = re.search(r"(?:глав[аы]|chapter)\s+'([^']+)'", error, flags=re.IGNORECASE)
    return match.group(1) if match else None


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
                global_context_errors.append(_report_error_text(error_val))
            elif title_val:
                context_errors_by_title[title_val] = _report_error_text(error_val)
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
    analysis_errors_by_title = _parse_errors_by_title(analysis_errors)
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
        failed_title = final_state.get("failed_chapter") or _failed_title_from_error(error)
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


def _first_unprocessed_title(
    chapter_plan: list[dict], processed_by_title: dict[str, dict]
) -> str | None:
    for chapter in chapter_plan:
        if chapter.get("status") == "pending" and chapter.get("title") not in processed_by_title:
            return chapter.get("title")
    return None


def _build_model_run_config(project_settings: dict) -> tuple[dict[str, str], str, bool]:
    embedding_model = project_settings.get(
        "embedding_model_name", settings.embedding_model_name
    )
    free_tier_mode = project_settings.get(
        "gemini_free_tier_mode", settings.gemini_free_tier_mode
    )
    embedding_model = normalize_embedding_model(
        embedding_model, free_tier_mode=free_tier_mode
    )

    model_configs = {
        "analysis": project_settings.get(
            "analysis_model_name", settings.analysis_model_name
        ),
        "curation": project_settings.get(
            "curation_model_name", settings.curation_model_name
        ),
        "translation": project_settings.get(
            "translation_model_name", settings.translation_model_name
        ),
        "qa": project_settings.get("qa_model_name", settings.qa_model_name),
        "judge": project_settings.get("judge_model_name", settings.judge_model_name),
        "expert_judge": project_settings.get(
            "expert_judge_model_name", settings.expert_judge_model_name
        ),
        "editor": project_settings.get(
            "editor_model_name", settings.editor_model_name
        ),
        "summarization": project_settings.get(
            "summarization_model_name", settings.summarization_model_name
        ),
    }
    model_configs = normalize_model_configs(
        model_configs,
        free_tier_mode=free_tier_mode,
    )
    return model_configs, embedding_model, free_tier_mode


def _parse_chapter_filter(spec: str) -> set[int] | None:
    """Парсит фильтр глав: "591-603", "591,593,600", или None при ошибке.

    Возвращает множество номеров глав или None, если спека невалидна
    (в этом случае фильтр не применяется — предохранение от молчаливой
    фильтрации всех глав).
    """
    if not spec or not str(spec).strip():
        return None
    text = str(spec).strip()
    allowed: set[int] = set()
    try:
        for part in text.split(","):
            part = part.strip()
            if not part:
                continue
            if "-" in part:
                lo_s, hi_s = part.split("-", 1)
                lo, hi = int(lo_s.strip()), int(hi_s.strip())
                if lo > hi:
                    lo, hi = hi, lo
                allowed.update(range(lo, hi + 1))
            else:
                allowed.add(int(part))
    except ValueError:
        logger.warning("Невалидный фильтр глав '%s' — игнорируется.", text)
        return None
    return allowed or None


def _chapter_number(file_name: str) -> int | None:
    """Извлекает ведущий номер главы из имени файла ('Chapter 591 ...' -> 591)."""
    match = re.search(r"(\d+)", os.path.basename(file_name))
    return int(match.group(1)) if match else None


def _build_run_metadata(
    *,
    input_dir: str,
    output_dir: str,
    overwrite_existing: bool,
    retry_failed: bool,
    retry_incomplete: bool,
    rejudge_existing: bool,
    model_configs: dict[str, str],
    embedding_model: str,
    free_tier_mode: bool,
) -> dict:
    return {
        "input_dir": input_dir,
        "output_dir": output_dir,
        "overwrite_existing": overwrite_existing,
        "retry_failed": retry_failed,
        "retry_incomplete": retry_incomplete,
        "rejudge_existing": rejudge_existing,
        "gemini_free_tier_mode": free_tier_mode,
        "model_configs": model_configs,
        "embedding_model": embedding_model,
    }


def _remaining_quality_error(final_state: dict) -> str | None:
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


def _load_retry_failed_titles(output_dir: str) -> set[str]:
    return set(_load_retry_failed_chapters(output_dir))


def _load_retry_failed_chapters(output_dir: str) -> dict[str, dict]:
    return _load_retry_chapters(output_dir, include_incomplete=False)


def _load_retry_chapters(
    output_dir: str,
    *,
    include_incomplete: bool,
    require_report: bool = False,
) -> dict[str, dict]:
    report_path = os.path.join(output_dir, "translation_report.json")
    try:
        with open(report_path, encoding="utf-8") as report_file:
            report = json.load(report_file)
    except FileNotFoundError as exc:
        if require_report:
            raise FileNotFoundError(
                f"Retry requested, but report was not found: {report_path}"
            ) from exc
        return {}
    except json.JSONDecodeError as exc:
        if require_report:
            raise ValueError(
                f"Retry requested, but report is invalid JSON: {report_path}"
            ) from exc
        return {}
    except OSError as exc:
        if require_report:
            raise OSError(
                f"Retry requested, but report is unreadable: {report_path}"
            ) from exc
        return {}

    if not isinstance(report, dict) or not isinstance(report.get("chapters"), list):
        if require_report:
            raise ValueError(
                f"Retry requested, but report schema is invalid: {report_path}"
            )
        return {}

    if any(not isinstance(chapter, dict) for chapter in report["chapters"]):
        if require_report:
            raise ValueError(
                f"Retry requested, but report chapter schema is invalid: {report_path}"
            )
        return {}

    return {
        chapter.get("title"): chapter
        for chapter in report.get("chapters", [])
        if _should_retry_report_chapter(
            chapter,
            include_incomplete=include_incomplete,
        )
        and chapter.get("title")
    }


def _should_retry_report_chapter(chapter: dict, *, include_incomplete: bool) -> bool:
    status = chapter.get("status")
    if status in {"failed", "qa_failed"}:
        return True
    if chapter.get("judge_pass_check") is False or bool(chapter.get("blocking_issues")):
        return True
    if not include_incomplete:
        return False

    stages = chapter.get("stages") or {}
    if status in {"skipped_not_failed", "skipped_existing"}:
        return False

    if "failed" in stages.values():
        return True

    for stage, s_status in stages.items():
        if s_status == "not_started":
            if stage == "refine" and stages.get("judge") == "done" and chapter.get("judge_pass_check") is True:
                continue
            return True

    return bool(chapter.get("warnings")) or bool(chapter.get("context_warnings"))


def _should_retry_checkpoint_run(run: dict, *, include_incomplete: bool) -> bool:
    """Определяет, нужно ли запускать повторную обработку для чекпоинта из SQLite."""
    status = run.get("status") or ""
    if status.startswith("skipped"):
        return False

    judge_res = run.get("judge_result") or {}
    blocking_issues = run.get("blocking_issues") or judge_res.get("blocking_issues")

    if status in {"failed", "qa_failed"} or bool(blocking_issues):
        return True

    if not judge_res.get("pass_check", True):
        return True

    if not include_incomplete:
        return False

    stages = run.get("stages") or {}
    if "failed" in stages.values():
        return True

    for stage in ["glossary_updated", "translation_done", "output_written", "judge_done", "refine_done", "summary_done", "memory_updated"]:
        if stage in stages and stages[stage] not in {"done", "skipped"}:
            return True

    for legacy_stage in ["translation_done", "judge_done", "summary_done"]:
        if legacy_stage in stages and stages[legacy_stage] not in {"done", "skipped"}:
            return True

    if bool(run.get("warnings")):
        return True

    return False


def _checkpoint_run_to_report_chapter(run: dict) -> dict:
    """Конвертирует SQLite checkpoint (run) в формат главы для отчета."""
    stages = run.get("stages") or {}
    report_stages = {}

    stage_mapping = {
        "glossary_updated": "glossary",
        "translation_done": "translation",
        "output_written": "output",
        "judge_done": "judge",
        "refine_done": "refine",
        "summary_done": "summary",
    }

    for db_stage, report_stage in stage_mapping.items():
        if db_stage in stages:
            report_stages[report_stage] = stages[db_stage]

    for stage, val in stages.items():
        if stage not in stage_mapping:
            report_stages[stage] = val

    blocking_issues = list(run.get("blocking_issues") or [])
    judge_res = run.get("judge_result") or {}

    status = run.get("status")
    if (
        status == "judge_done"
        and not judge_res.get("pass_check", True)
        and not blocking_issues
        and not judge_res.get("blocking_issues")
    ):
        status = "qa_failed"
        blocking_issues = ["Judge failed without blocking issue details"]
        report_stages["refine"] = "needed"
    elif status == "judge_done" and not judge_res.get("pass_check", True):
        status = "qa_failed"
        blocking_issues = list(judge_res.get("blocking_issues") or [])
        report_stages["refine"] = "needed"

    report_chapter = {
        "title": run.get("title"),
        "status": status,
        "stages": report_stages,
        "input_path": run.get("input_path"),
        "output_path": run.get("output_path"),
        "reused_existing_translation": bool(run.get("reused_existing_translation")),
        "translation_source": run.get("translation_source"),
        "translation_mode": run.get("translation_mode"),
        "translation_chunk_count": run.get("translation_chunk_count"),
        "judge_pass_check": judge_res.get("pass_check"),
        "judge_score": judge_res.get("score"),
        "judge_severity": judge_res.get("severity"),
        "blocking_issues": blocking_issues or list(judge_res.get("blocking_issues") or []),
        "quality_suggestions": judge_res.get("suggestions") or [],
        "dictionary_conflicts": run.get("dictionary_conflicts") or [],
        "context_warnings": run.get("context_warnings") or [],
        "error": run.get("error"),
        "warnings": list(run.get("warnings") or []),
    }
    for cw in report_chapter["context_warnings"]:
        if isinstance(cw, dict) and cw.get("error"):
            cw_err = cw["error"]
            report_chapter["warnings"].append(f"Context retrieval was degraded: {cw_err}")
    report_chapter["warnings"] = list(dict.fromkeys(report_chapter["warnings"]))
    return report_chapter


def _can_reuse_existing_translation(chapter: dict) -> bool:
    stages = chapter.get("stages") or {}
    translation_stage = stages.get("translation")
    output_stage = stages.get("output")
    if translation_stage == "failed" or output_stage == "failed":
        return False
    if translation_stage == "done":
        return True
    if translation_stage in {"not_started", "skipped"}:
        return False

    status = chapter.get("status")
    error = chapter.get("error") or ""
    normalized_error = error.casefold()
    if status in {"qa_failed", "translated"}:
        return True
    if status == "failed":
        if "ошибка перевода" in normalized_error or "translation failed" in normalized_error:
            return False
        return True
    return False


def should_refine(state: AgentState) -> str:
    """Conditional edge to decide if we need another refinement pass."""
    if state.get("error"):
        return END

    blocking_issues = state.get("blocking_issues", [])
    refinement_count = state.get("refinement_count", 0)

    if blocking_issues and refinement_count < 2:
        logger.info(
            f"Найдено {len(blocking_issues)} блокирующих ошибок. "
            f"Переход к уточнению (итерация {refinement_count + 1}/2)."
        )
        return REFINE

    if blocking_issues:
        logger.warning(
            f"Достигнут лимит уточнений (2), но {len(blocking_issues)} ошибок остались."
        )

    return SUMMARIZATION


def build_graph():
    workflow = StateGraph(AgentState)

    # Определяем узлы
    workflow.add_node(ANALYSIS, analysis_node)
    workflow.add_node(CURATION, autonomous_curation_node)
    workflow.add_node(TRANSLATION, translation_node)
    workflow.add_node(JUDGE, judge_node)
    workflow.add_node(REFINE, refine_node)
    workflow.add_node(CONTEXT_RETRIEVAL, context_retrieval_node)
    workflow.add_node(SUMMARIZATION, summarization_node)

    # Строим граф
    workflow.set_entry_point(CONTEXT_RETRIEVAL)
    workflow.add_edge(CONTEXT_RETRIEVAL, ANALYSIS)
    workflow.add_edge(ANALYSIS, CURATION)
    workflow.add_edge(CURATION, TRANSLATION)
    workflow.add_edge(TRANSLATION, JUDGE)

    # Условный переход от Судьи
    workflow.add_conditional_edges(
        JUDGE,
        should_refine,
        {
            REFINE: REFINE,
            SUMMARIZATION: SUMMARIZATION,
            END: END,
        },
    )

    # После уточнения возвращаемся к Судье для повторной проверки
    workflow.add_edge(REFINE, JUDGE)

    # После саммари завершаем
    workflow.add_edge(SUMMARIZATION, END)

    return workflow.compile()


def run_translation_workflow(
    project_name: str, project_settings: dict, progress_callback=None
):
    """Основная функция для запуска полного цикла перевода."""
    logger.info(f"Запуск воркфлоу для проекта: {project_name}")

    input_dir = project_settings.get("input_dir")
    output_dir = project_settings.get("output_dir")

    if not input_dir or not output_dir:
        raise ValueError("Директории ввода/вывода не указаны в project_settings.")

    # Валидация символических ссылок и вложенности путей до создания директорий
    if os.path.islink(input_dir) or os.path.islink(output_dir):
        raise ValueError("input_dir or output_dir cannot be a symlink")

    abs_input = os.path.abspath(input_dir)
    abs_output = os.path.abspath(output_dir)

    if abs_input == abs_output:
        raise ValueError("input_dir and output_dir cannot be the same")

    if abs_output.startswith(abs_input + os.sep):
        raise ValueError("output_dir cannot be inside input_dir")

    if abs_input.startswith(abs_output + os.sep):
        raise ValueError("input_dir cannot be inside output_dir")

    lock_path = None
    try:
        os.makedirs(output_dir, exist_ok=True)
        lock_path = _acquire_workflow_lock(output_dir)
        all_files = sorted(
            [
                f
                for f in os.listdir(input_dir)
                if os.path.isfile(os.path.join(input_dir, f))
                and f.lower().endswith((".txt", ".md"))
            ]
        )
        chapter_filter = project_settings.get("chapter_filter")
        if chapter_filter:
            allowed = _parse_chapter_filter(chapter_filter)
            if allowed is not None:
                all_files = [
                    f for f in all_files if _chapter_number(f) in allowed
                ]
                logger.info(
                    "Фильтр глав %s: обработке подлежит %d глав.",
                    chapter_filter,
                    len(all_files),
                )
    except FileNotFoundError:
        raise FileNotFoundError(f"Директория ввода '{input_dir}' не найдена.")
    except Exception:
        _release_workflow_lock(lock_path)
        raise

    overwrite_existing = project_settings.get("overwrite_existing", False)

    retry_failed = project_settings.get("retry_failed", False)
    retry_incomplete = project_settings.get("retry_incomplete", False)
    rejudge_existing = project_settings.get("rejudge_existing", False)
    model_configs, embedding_model, free_tier_mode = _build_model_run_config(
        project_settings
    )
    run_metadata = _build_run_metadata(
        input_dir=input_dir,
        output_dir=output_dir,
        overwrite_existing=overwrite_existing,
        retry_failed=retry_failed,
        retry_incomplete=retry_incomplete,
        rejudge_existing=rejudge_existing,
        model_configs=model_configs,
        embedding_model=embedding_model,
        free_tier_mode=free_tier_mode,
    )
    retry_from_report = retry_failed or retry_incomplete
    retry_failed_chapters = {}
    if retry_from_report:
        try:
            retry_failed_chapters = _load_retry_chapters(
                output_dir,
                include_incomplete=retry_incomplete,
                require_report=True,
            )
        except FileNotFoundError as fnf_exc:
            logger.info("Отчет перевода не найден. Попытка загрузить чекпоинты из SQLite.")
            db_manager = DatabaseManager(project_name)
            try:
                chapter_runs = db_manager.get_chapter_runs()
                for title, run in chapter_runs.items():
                    if _should_retry_checkpoint_run(run, include_incomplete=retry_incomplete):
                        retry_failed_chapters[title] = _checkpoint_run_to_report_chapter(run)
                if not retry_failed_chapters:
                    raise FileNotFoundError(
                        "No translation report and no SQLite checkpoints found to retry."
                    ) from fnf_exc
            except FileNotFoundError:
                _release_workflow_lock(lock_path)
                raise
            except Exception as exc:
                logger.warning("Не удалось загрузить чекпоинты из SQLite: %s", exc)
                _release_workflow_lock(lock_path)
                raise
        except Exception:
            _release_workflow_lock(lock_path)
            raise
    retry_failed_titles = set(retry_failed_chapters)

    if retry_from_report:
        available_titles = {os.path.splitext(file_name)[0] for file_name in all_files}
        missing_retry_titles = sorted(retry_failed_titles - available_titles)
        if missing_retry_titles:
            _release_workflow_lock(lock_path)
            missing_list = ", ".join(missing_retry_titles)
            raise FileNotFoundError(
                "Retry requested, but source files are missing for "
                f"report chapter(s): {missing_list}"
            )

    chapter_plan = []
    chapters_to_process = []
    for f in all_files:
        title = os.path.splitext(f)[0]
        output_path = os.path.join(output_dir, f"{os.path.splitext(f)[0]}.txt")
        chapter_data = {
            "title": title,
            "input_path": os.path.join(input_dir, f),
            "output_path": output_path,
        }
        planned_chapter = {**chapter_data, "status": "pending"}
        output_exists = os.path.exists(output_path)
        should_process = overwrite_existing or not output_exists
        if retry_from_report:
            should_process = title in retry_failed_titles
            previous_report_chapter = retry_failed_chapters.get(title, {})
            if (
                should_process
                and os.path.exists(output_path)
                and _can_reuse_existing_translation(previous_report_chapter)
            ):
                chapter_data["reuse_existing_translation"] = True
                chapter_data["previous_retry_error"] = previous_report_chapter.get("error")
                prev_blocking = previous_report_chapter.get("blocking_issues") or []
                chapter_data["blocking_issues"] = prev_blocking
                planned_chapter["reuse_existing_translation"] = True
                planned_chapter["blocking_issues"] = prev_blocking

                # force_rejudge
                prev_stages = previous_report_chapter.get("stages") or {}
                prev_judge_pass = previous_report_chapter.get("judge_pass_check")
                has_refine = prev_stages.get("refine") in {"done", "skipped"} or prev_stages.get("refine_done") in {"done", "skipped"}
                if prev_judge_pass is None and "judge_result" in previous_report_chapter:
                    prev_judge_pass = (previous_report_chapter.get("judge_result") or {}).get("pass_check")
                if has_refine and prev_judge_pass is False:
                    chapter_data["force_rejudge"] = True
                    planned_chapter["force_rejudge"] = True
        elif rejudge_existing and output_exists:
            # Режим пере-судейства: используем готовый файл перевода (без повторного
            # обращения к API перевода) и форсируем запуск Judge по новым правилам.
            should_process = True
            chapter_data["reuse_existing_translation"] = True
            chapter_data["force_rejudge"] = True
            planned_chapter["reuse_existing_translation"] = True
            planned_chapter["force_rejudge"] = True
            logger.info(
                "Rejudge-режим: глава '%s' будет перепроверена судьёй по новым правилам "
                "(существующий перевод используется без повторного запроса к API).",
                title,
            )

        if should_process:
            chapters_to_process.append(chapter_data)
            chapter_plan.append(planned_chapter)
        else:
            chapter_plan.append(
                {
                    **planned_chapter,
                    "status": "skipped_not_failed"
                    if retry_from_report
                    else "skipped_existing",
                }
            )
            logger.info(f"Пропуск главы '{f}', так как перевод уже существует.")

    logger.info(f"Найдено глав для обработки: {len(chapters_to_process)}")

    if not chapters_to_process:
        logger.warning("Главы для обработки не найдены. Воркфлоу завершен досрочно.")
        final_state = {"processed_chapters": [], "error": None}
        final_state["report_path"] = _write_workflow_report(
            project_name, output_dir, chapter_plan, final_state, run_metadata
        )
        _release_workflow_lock(lock_path)
        return final_state

    api_key = project_settings.get("GOOGLE_API_KEY") or settings.GOOGLE_API_KEY
    if not api_key:
        _release_workflow_lock(lock_path)
        raise ValueError("GOOGLE_API_KEY is required to run translation workflow.")
    if is_placeholder_api_key(api_key):
        _release_workflow_lock(lock_path)
        raise ValueError(
            "A real GOOGLE_API_KEY is required to run translation workflow; "
            "fake/test placeholder keys are only valid for isolated unit tests."
        )

    try:
        db_mgr = DatabaseManager(project_name)
        app_context = AppContext(
            db_manager=db_mgr,
            kb_manager=KnowledgeBaseManager(
                project_name=project_name,
                api_key=api_key,
                embedding_model_name=embedding_model,
                enable_reranker=project_settings.get(
                    "enable_reranker", settings.enable_reranker
                ),
                db_manager=db_mgr,
            ),
            llm_provider=LLMProvider(model_configs=model_configs, api_key=api_key),
            settings=settings,
        )
    except Exception:
        _release_workflow_lock(lock_path)
        raise

    # Записываем обнаруженные главы в БД со статусом "discovered"
    for chapter in chapters_to_process:
        try:
            app_context["db_manager"].upsert_chapter_run(
                chapter["title"],
                input_path=chapter["input_path"],
                output_path=chapter["output_path"],
                status="discovered",
            )
        except Exception as exc:
            logger.warning(
                f"Не удалось записать статус 'discovered' для главы '{chapter['title']}': {exc}"
            )

    initial_state = AgentState(
        app_context=app_context,
        project_name=project_name,
        project_settings=project_settings,
        chapters_to_process=chapters_to_process,
        processed_chapters=[],
        analysis_results=[],
        analysis_errors=[],
        unification_verdicts=[],
        judge_results=[],
        blocking_issues=[],
        refinement_count=0,
        rag_context="",
        chapter_contexts={},
        context_errors=[],
        chapter_summaries=[],
        summary_errors=[],
        chapter_runs=app_context["db_manager"].get_chapter_runs(),
        error=None,
        progress_callback=progress_callback,
    )

    app = build_graph()
    final_state = None
    try:
        final_state = app.invoke(initial_state)

        # Проверяем наличие специфичных ошибок, которые должны уронить воркфлоу
        global_context_errs = [
            item.get("error") if isinstance(item, dict) else item
            for item in final_state.get("context_errors", []) or []
            if (isinstance(item, dict) and item.get("title") == "*") or (isinstance(item, str) and not re.search(r"(?:chapter|глав[аы])\s+'([^']+)'", item, flags=re.IGNORECASE))
        ]
        if global_context_errs:
            err_msg = f"Context retrieval failed: {global_context_errs[0]}"
            final_state["error"] = err_msg
            raise RuntimeError(err_msg)

        analysis_errs = [
            item.get("error") if isinstance(item, dict) else item
            for item in final_state.get("analysis_errors", []) or []
        ]
        if analysis_errs:
            err_msg = f"Analysis failed: {analysis_errs[0]}"
            final_state["error"] = err_msg
            raise RuntimeError(err_msg)

        summary_errs = [
            item.get("error") if isinstance(item, dict) else item
            for item in final_state.get("summary_errors", []) or []
        ]
        if summary_errs:
            err_msg = f"Summary failed: {summary_errs[0]}"
            final_state["error"] = err_msg
            raise RuntimeError(err_msg)

        if final_state.get("error"):
            raise RuntimeError(final_state["error"])
        quality_error = _remaining_quality_error(final_state)
        if quality_error:
            final_state["error"] = quality_error
            raise RuntimeError(quality_error)

        final_state["report_path"] = _write_workflow_report(
            project_name, output_dir, chapter_plan, final_state, run_metadata
        )
    except Exception as exc:
        if final_state is None:
            final_state = {
                "processed_chapters": [],
                "error": str(exc),
            }
        else:
            final_state["error"] = final_state.get("error") or str(exc)
        final_state["report_path"] = _write_workflow_report(
            project_name, output_dir, chapter_plan, final_state, run_metadata
        )
        logger.error("Ошибка в процессе выполнения графа: %s", final_state["error"])
        raise
    finally:
        _release_workflow_lock(lock_path)

    logger.info("Воркфлоу успешно завершен.")
    return final_state
