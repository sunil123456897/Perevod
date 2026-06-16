# src/Perevod/graph_runner.py
"""Orchestrates the LangGraph translation workflow.

This module is intentionally a thin orchestration layer. The heavy,
unit-testable pieces of behaviour live in dedicated modules:

* :mod:`Perevod.workflow_lock`   - cross-process lock for output dirs.
* :mod:`Perevod.retry_planner`   - retry / rejudge / reuse planning.
* :mod:`Perevod.report_builder`  - ``translation_report.json`` generation.

The names are re-exported here (``_acquire_workflow_lock`` etc.) so that the
existing test suite, which patches ``Perevod.graph_runner.<name>``, keeps
working while the implementation has moved.
"""
import logging
import os
import re

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

# Re-export the extracted helpers under their historical names so the test
# suite's ``@patch("Perevod.graph_runner.<name>")`` decorators keep resolving.
from Perevod.workflow_lock import (  # noqa: F401
    _acquire_workflow_lock as _acquire_workflow_lock_impl,
    _can_remove_existing_lock,
    _create_lock_file,
    _is_process_running,
    _read_lock_pid,
    _release_workflow_lock,
    acquire_workflow_lock,
    release_workflow_lock,
)
from Perevod.retry_planner import (  # noqa: F401
    _can_reuse_existing_translation,
    _checkpoint_run_to_report_chapter,
    _chapter_number,
    _load_retry_chapters,
    _parse_chapter_filter,
    _should_retry_checkpoint_run,
    can_reuse_existing_translation,
    checkpoint_run_to_report_chapter,
    chapter_number,
    load_retry_chapters,
    parse_chapter_filter,
    should_retry_checkpoint_run,
)
from Perevod.report_builder import (  # noqa: F401
    _chapter_stage_statuses,
    _chapter_warnings,
    _failed_title_from_error,
    _find_error_metadata_by_title,
    _first_unprocessed_title,
    _parse_errors_by_title,
    _remaining_quality_error,
    _report_error_text,
    _write_workflow_report,
    failed_title_from_error,
    remaining_quality_error,
    write_workflow_report,
)

logger = logging.getLogger("NovelTranslator.GraphRunner")


def _acquire_workflow_lock(output_dir: str) -> str:
    """Bind :func:`acquire_workflow_lock` to this module's ``_is_process_running``.

    The test suite patches ``Perevod.graph_runner._is_process_running``. After
    the lock logic moved to :mod:`Perevod.workflow_lock`, a bare re-export would
    bypass that patch. This thin wrapper injects the local liveness callable so
    the historical patch path keeps controlling stale-lock detection.
    """
    return _acquire_workflow_lock_impl(output_dir, is_running=_is_process_running)


# Определяем имена узлов
ANALYSIS = "analysis"
CURATION = "autonomous_curation"
TRANSLATION = "translation"
JUDGE = "judge"
REFINE = "refine"
CONTEXT_RETRIEVAL = "context_retrieval"
SUMMARIZATION = "summarization"


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
    # Cache existing output filenames once, so resume can detect chapters that
    # were already translated and later renamed to "Глава NNN. ..." regardless of
    # the original English filename. Without this, a renamed-but-translated
    # chapter would be re-translated (producing duplicates).
    output_files = {
        f for f in os.listdir(output_dir) if f.lower().endswith((".txt", ".md"))
    }

    def _find_existing_translation(stem: str, exact_path: str) -> str | None:
        """Return the existing output filename for ``stem`` if translated.

        Checks the exact expected path first (via ``os.path.exists`` so existing
        tests that mock ``os.path.exists`` still work), then falls back to a
        numbered ``Глава NNN`` prefix match against the cached output file list
        so renamed files are recognised as done.
        """
        exact = f"{stem}.txt"
        if os.path.exists(exact_path):
            return exact
        # Extract the leading chapter number (e.g. "Chapter 604 ..." -> "604").
        m = re.match(r"[^\d]*?(\d+)", stem)
        if not m:
            return None
        num = m.group(1)
        for fname in output_files:
            # Skip the exact name — already checked above.
            if fname == exact:
                continue
            # Match "Глава 604.txt", "Глава 604. ....txt", and also bare
            # number-prefixed variants, while avoiding 6040/6041 false hits.
            if re.match(rf"^[^\d]*?{num}(?:\D|$)", fname):
                return fname
        return None

    for f in all_files:
        title = os.path.splitext(f)[0]
        output_path = os.path.join(output_dir, f"{os.path.splitext(f)[0]}.txt")
        chapter_data = {
            "title": title,
            "input_path": os.path.join(input_dir, f),
            "output_path": output_path,
        }
        planned_chapter = {**chapter_data, "status": "pending"}
        existing_output_name = _find_existing_translation(title, output_path)
        output_exists = existing_output_name is not None
        if existing_output_name and existing_output_name != f"{title}.txt":
            # Point reuse/rejudge logic at the actually-existing (renamed) file.
            output_path = os.path.join(output_dir, existing_output_name)
            chapter_data["output_path"] = output_path
            planned_chapter["output_path"] = output_path
            logger.info(
                "Глава '%s': найден существующий перевод под именем '%s'.",
                title,
                existing_output_name,
            )
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
            # Distinguish a partial failure (some chapters failed but others
            # succeeded) from a total failure. With partial failures we keep
            # the successful translations and let the run finish so resume can
            # pick up the remaining chapters next time, instead of losing the
            # whole batch to one transient API error.
            processed = final_state.get("processed_chapters") or []
            failed = final_state.get("failed_chapters") or []
            if processed and failed:
                logger.warning(
                    "Перевод завершен с частичным сбоем: %d глав переведено, "
                    "%d глав не удалось перевести. Успешные результаты сохранены.",
                    len(processed),
                    len(failed),
                )
                # Keep error as informational but do not raise — write report.
            else:
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
