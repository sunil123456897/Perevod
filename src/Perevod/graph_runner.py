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


def _write_workflow_report(
    project_name: str,
    output_dir: str,
    chapter_plan: list[dict],
    final_state: dict,
    run_metadata: dict | None = None,
) -> str | None:
    workflow_error = _report_error_text(final_state.get("error"))
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
    summary_errors_by_title = {
        item.get("title"): item.get("error")
        for item in final_state.get("summary_errors", [])
        if item.get("title")
    }
    analysis_errors_by_title = {
        item.get("title"): item.get("error")
        for item in final_state.get("analysis_errors", [])
        if item.get("title")
    }
    context_errors_by_title = {
        item.get("title"): item.get("error")
        for item in final_state.get("context_errors", [])
        if item.get("title") and item.get("title") != "*"
    }
    global_context_errors = [
        item.get("error")
        for item in final_state.get("context_errors", [])
        if item.get("title") == "*"
    ]
    failed_title = final_state.get("failed_chapter") or _failed_title_from_error(
        workflow_error
    )
    if workflow_error and not failed_title:
        failed_title = _first_unprocessed_title(chapter_plan, processed_by_title)

    chapter_reports = []
    for chapter in chapter_plan:
        title = chapter.get("title")
        status = chapter.get("status", "pending")
        chapter_error = None
        processed_chapter = processed_by_title.get(title)
        judge_result = judge_by_title.get(title) or {}

        if title == failed_title:
            status = "failed"
            chapter_error = workflow_error
        elif processed_chapter:
            status = "qa_failed" if processed_chapter.get("blocking_issues") else "translated"
        elif status == "pending" and workflow_error:
            status = "not_started"

        chapter_reports.append(
            {
                "title": title,
                "status": status,
                "stages": _chapter_stage_statuses(
                    title=title,
                    base_status=chapter.get("status", "pending"),
                    processed_chapter=processed_chapter,
                    judge_titles=judge_titles,
                    summary_titles=summary_titles,
                    summary_errors_by_title=summary_errors_by_title,
                    error=workflow_error,
                ),
                "input_path": chapter.get("input_path"),
                "output_path": chapter.get("output_path"),
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
                "judge_pass_check": judge_result.get("pass_check"),
                "judge_score": judge_result.get("score"),
                "judge_severity": judge_result.get("severity"),
                "blocking_issues": (processed_chapter or {}).get("blocking_issues")
                or judge_result.get("blocking_issues", []),
                "quality_suggestions": judge_result.get("suggestions", []),
                "error": chapter_error,
                "warnings": _chapter_warnings(
                    title=title,
                    context_errors_by_title=context_errors_by_title,
                    global_context_errors=global_context_errors,
                    analysis_errors_by_title=analysis_errors_by_title,
                    summary_errors_by_title=summary_errors_by_title,
                ),
            }
        )

    report = {
        "project_name": project_name,
        "completed_at": datetime.now(timezone.utc).isoformat(),
        "run": run_metadata or {},
        "total_chapters": len(chapter_plan),
        "processed_count": len(processed_chapters),
        "processed_chapters": [
            chapter.get("title") for chapter in processed_chapters
        ],
        "failed_count": sum(
            1 for chapter in chapter_reports if chapter["status"] in {"failed", "qa_failed"}
        ),
        "warning_count": sum(len(chapter["warnings"]) for chapter in chapter_reports),
        "chapters": chapter_reports,
        "error": workflow_error,
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
    match = re.search(r"глав[аы]\s+'([^']+)'", error, flags=re.IGNORECASE)
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
) -> dict[str, str]:
    if base_status.startswith("skipped"):
        return {
            "translation": "skipped",
            "judge": "skipped",
            "summary": "skipped",
        }

    stages = {
        "translation": "done" if processed_chapter else "not_started",
        "judge": "done" if title in judge_titles else "not_started",
        "summary": "failed"
        if title in summary_errors_by_title
        else "done"
        if title in summary_titles
        else "not_started",
    }

    if title and error:
        if title == _failed_title_from_error(error):
            normalized_error = error.casefold()
            if normalized_error.startswith("ошибка перевода главы"):
                stages["translation"] = "failed"
            elif normalized_error.startswith(("ошибка судьи", "ошибка редактора")):
                stages["judge"] = "failed"

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
        "judge": project_settings.get("judge_model_name", settings.qa_model_name),
        "editor": project_settings.get(
            "editor_model_name", settings.translation_model_name
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


def _build_run_metadata(
    *,
    input_dir: str,
    output_dir: str,
    overwrite_existing: bool,
    retry_failed: bool,
    retry_incomplete: bool,
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
        "gemini_free_tier_mode": free_tier_mode,
        "model_configs": model_configs,
        "embedding_model": embedding_model,
    }


def _remaining_quality_error(final_state: dict) -> str | None:
    blocking_issues = final_state.get("blocking_issues") or []
    if not blocking_issues:
        return None
    return (
        "QA failed after refinement limit: "
        f"{len(blocking_issues)} blocking issue(s) remain."
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
    if chapter.get("status") in {"failed", "qa_failed"}:
        return True
    if not include_incomplete:
        return False
    stages = chapter.get("stages") or {}
    return "failed" in stages.values() or bool(chapter.get("warnings"))


def _can_reuse_existing_translation(chapter: dict) -> bool:
    stages = chapter.get("stages") or {}
    translation_stage = stages.get("translation")
    if translation_stage == "failed":
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
    if status == "failed" and "ошибка перевода главы" in normalized_error:
        return False
    if status == "failed":
        downstream_error_markers = (
            "ошибка судьи",
            "ошибка редактора",
            "ошибка editor",
            "judge",
            "editor",
            "qa failed",
            "summary",
            "summarization",
        )
        return any(marker in normalized_error for marker in downstream_error_markers)
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
    except FileNotFoundError:
        raise FileNotFoundError(f"Директория ввода '{input_dir}' не найдена.")
    except Exception:
        _release_workflow_lock(lock_path)
        raise

    overwrite_existing = project_settings.get("overwrite_existing", False)

    retry_failed = project_settings.get("retry_failed", False)
    retry_incomplete = project_settings.get("retry_incomplete", False)
    model_configs, embedding_model, free_tier_mode = _build_model_run_config(
        project_settings
    )
    run_metadata = _build_run_metadata(
        input_dir=input_dir,
        output_dir=output_dir,
        overwrite_existing=overwrite_existing,
        retry_failed=retry_failed,
        retry_incomplete=retry_incomplete,
        model_configs=model_configs,
        embedding_model=embedding_model,
        free_tier_mode=free_tier_mode,
    )
    retry_from_report = retry_failed or retry_incomplete
    try:
        retry_failed_chapters = (
            _load_retry_chapters(
                output_dir,
                include_incomplete=retry_incomplete,
                require_report=True,
            )
            if retry_from_report
            else {}
        )
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
        should_process = overwrite_existing or not os.path.exists(output_path)
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
                planned_chapter["reuse_existing_translation"] = True

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
        app_context = AppContext(
            db_manager=DatabaseManager(project_name),
            kb_manager=KnowledgeBaseManager(
                project_name=project_name,
                api_key=api_key,
                embedding_model_name=embedding_model,
                enable_reranker=project_settings.get(
                    "enable_reranker", settings.enable_reranker
                ),
            ),
            llm_provider=LLMProvider(model_configs=model_configs, api_key=api_key),
            settings=settings,
        )
    except Exception:
        _release_workflow_lock(lock_path)
        raise

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
        context_errors=[],
        chapter_summaries=[],
        summary_errors=[],
        error=None,
        progress_callback=progress_callback,
    )

    app = build_graph()
    final_state = None
    try:
        final_state = app.invoke(initial_state)

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
