# src/Perevod/retry_planner.py
"""Decides which chapters deserve a retry and how to reuse prior work.

The translation workflow can be re-run in several modes:

* ``retry_failed`` — re-translate only chapters whose last run ended in
  ``failed`` or ``qa_failed``.
* ``retry_incomplete`` — like ``retry_failed`` but additionally re-runs
  chapters with degraded stages or non-fatal warnings.
* ``rejudge_existing`` — skip translation entirely and only re-run the Judge
  against already-written output files.

This module reads the previous ``translation_report.json`` (falling back to
SQLite checkpoints when no report exists) and produces a mapping of titles to
their prior chapter records, plus the per-chapter reuse/rejudge flags consumed
by the graph runner.
"""

import json
import logging
import os
import re

logger = logging.getLogger("NovelTranslator.GraphRunner")


def parse_errors_by_title(error_list: list) -> dict[str, str]:
    errors_by_title = {}
    for item in error_list or []:
        if isinstance(item, dict):
            title = item.get("title")
            error_val = item.get("error")
            if title and title != "*":
                errors_by_title[title] = report_error_text(error_val)
        elif isinstance(item, str):
            match = re.search(r"(?:chapter|глав[аы])\s+'([^']+)'", item, flags=re.IGNORECASE)
            if match:
                errors_by_title[match.group(1)] = item
    return errors_by_title


def report_error_text(error) -> str | None:
    if error is None or isinstance(error, str):
        return error
    return str(error)


def should_retry_report_chapter(chapter: dict, *, include_incomplete: bool) -> bool:
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


def should_retry_checkpoint_run(run: dict, *, include_incomplete: bool) -> bool:
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


def checkpoint_run_to_report_chapter(run: dict) -> dict:
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


def can_reuse_existing_translation(chapter: dict) -> bool:
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


def load_retry_chapters(
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
        if should_retry_report_chapter(
            chapter,
            include_incomplete=include_incomplete,
        )
        and chapter.get("title")
    }


def parse_chapter_filter(spec: str) -> set[int] | None:
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


def chapter_number(file_name: str) -> int | None:
    """Извлекает ведущий номер главы из имени файла ('Chapter 591 ...' -> 591)."""
    match = re.search(r"(\d+)", os.path.basename(file_name))
    return int(match.group(1)) if match else None


# Backwards-compatible underscore aliases. These helpers historically lived in
# ``graph_runner`` with leading underscores and the test suite patches / imports
# them as ``Perevod.graph_runner._<name>``. Public names above are the canonical
# API; the private aliases preserve the existing import contract.
_parse_errors_by_title = parse_errors_by_title
_report_error_text = report_error_text
_should_retry_report_chapter = should_retry_report_chapter
_should_retry_checkpoint_run = should_retry_checkpoint_run
_checkpoint_run_to_report_chapter = checkpoint_run_to_report_chapter
_can_reuse_existing_translation = can_reuse_existing_translation
_load_retry_chapters = load_retry_chapters
_parse_chapter_filter = parse_chapter_filter
_chapter_number = chapter_number
