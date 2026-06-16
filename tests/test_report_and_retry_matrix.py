# tests/test_report_and_retry_matrix.py
"""End-to-end tests for ``translation_report.json`` generation and the
retry/rejudge decision matrix.

These tests exercise the now-extracted pure functions directly:

* :func:`Perevod.report_builder.write_workflow_report` builds a report from a
  realistic final graph state and verifies the full per-chapter shape
  (stages, judge metadata, dictionary conflicts, structured API diagnostics,
  warnings).
* :func:`Perevod.retry_planner.load_retry_chapters` /
  :func:`Perevod.retry_planner.can_reuse_existing_translation` /
  :func:`Perevod.retry_planner.should_retry_checkpoint_run` are driven through
  a matrix that pins down exactly which (status, stages, blocking_issues)
  combinations trigger a retry and which reuse prior work.

Together they cover the critical path that previously lived untested inside the
1527-line ``graph_runner`` god module.
"""

import json
import os

from Perevod.report_builder import write_workflow_report
from Perevod.retry_planner import (
    can_reuse_existing_translation,
    load_retry_chapters,
    should_retry_checkpoint_run,
)


def _write_report(output_dir, chapters):
    """Helper: write a fake prior report and return its path."""
    report_path = os.path.join(output_dir, "translation_report.json")
    with open(report_path, "w", encoding="utf-8") as report_file:
        json.dump(
            {"project_name": "P", "chapters": chapters, "total_chapters": len(chapters)},
            report_file,
        )
    return report_path


def test_write_workflow_report_full_shape(tmp_path):
    """Full E2E: a translated, a qa_failed, and a not_started chapter."""
    chapter_plan = [
        {"title": "ch1", "input_path": "in/ch1.txt", "output_path": "out/ch1.txt", "status": "pending"},
        {"title": "ch2", "input_path": "in/ch2.txt", "output_path": "out/ch2.txt", "status": "pending"},
        {"title": "ch3", "input_path": "in/ch3.txt", "output_path": "out/ch3.txt", "status": "pending"},
    ]
    final_state = {
        "processed_chapters": [
            {
                "title": "ch1",
                "translation_source": "api",
                "translation_mode": "whole_chapter",
                "translation_chunk_count": 1,
                "output_backup_path": "out/ch1.txt.bak",
                "refined": True,
                "refinement_count": 1,
                "refine_issues_fixed": ["fixed-x"],
                "reused_existing_translation": False,
                "dictionary_conflicts": [],
            }
        ],
        "judge_results": [
            {
                "title": "ch1",
                "pass_check": True,
                "score": 9.0,
                "severity": "low",
                "blocking_issues": [],
                "suggestions": ["polish dialogue"],
            },
            {
                "title": "ch2",
                "pass_check": False,
                "score": 4.0,
                "severity": "high",
                "blocking_issues": ["Missing canonical term: Sword"],
                "suggestions": [],
            },
        ],
        "chapter_summaries": [
            {"title": "ch1", "summary": "Hero arrived.", "key_events": ["arrival"], "active_characters": ["Hero"]},
        ],
        "chapter_runs": {},
        "error": None,
        "run": {},
    }
    report_path = write_workflow_report("ProjX", str(tmp_path), chapter_plan, final_state)

    assert report_path == os.path.join(str(tmp_path), "translation_report.json")
    with open(report_path, encoding="utf-8") as report_file:
        report = json.load(report_file)

    assert report["project_name"] == "ProjX"
    assert report["total_chapters"] == 3
    # ch1 translated and QA-passed
    ch1 = next(c for c in report["chapters"] if c["title"] == "ch1")
    assert ch1["status"] == "translated"
    assert ch1["stages"]["translation"] == "done"
    assert ch1["stages"]["judge"] == "done"
    assert ch1["stages"]["summary"] == "done"
    assert ch1["judge_pass_check"] is True
    assert ch1["judge_score"] == 9.0
    assert ch1["refined"] is True
    assert ch1["refinement_count"] == 1
    assert ch1["summary"] == "Hero arrived."
    assert ch1["summary_key_events"] == ["arrival"]
    # ch2 QA-failed with blocking issue surfaced into error text
    ch2 = next(c for c in report["chapters"] if c["title"] == "ch2")
    assert ch2["status"] == "qa_failed"
    assert ch2["blocking_issues"] == ["Missing canonical term: Sword"]
    assert "QA failed" in (ch2["error"] or "")
    # ch3 never started
    ch3 = next(c for c in report["chapters"] if c["title"] == "ch3")
    assert ch3["status"] == "pending"
    # Aggregate counters
    assert report["failed_count"] == 1
    assert report["processed_count"] == 1


def test_write_workflow_report_structured_api_diagnostics(tmp_path):
    """A failed chapter with an API error surfaces structured diagnostics."""
    from Perevod.utils.api_errors import GeminiAPIError, ApiErrorInfo

    api_error = GeminiAPIError(
        model_name="gemini-3-flash-preview",
        operation="generateContent",
        info=ApiErrorInfo(
            category="quota",
            retryable=False,
            status_code=429,
            message="quota exceeded",
        ),
        original_error=RuntimeError("upstream 429"),
    )
    chapter_plan = [{"title": "ch1", "status": "pending"}]
    final_state = {
        "processed_chapters": [],
        "judge_results": [],
        "chapter_summaries": [],
        "chapter_runs": {},
        "error": api_error,
    }
    report_path = write_workflow_report("Proj", str(tmp_path), chapter_plan, final_state)
    with open(report_path, encoding="utf-8") as report_file:
        report = json.load(report_file)

    # The workflow-level error carries the structured category/status_code.
    assert report["error_category"] == "quota"
    assert report["error_status_code"] == 429
    assert report["error_retryable"] is False
    assert report["error_operation"] == "generateContent"
    assert report["error_model"] == "gemini-3-flash-preview"


# ---------------------------------------------------------------------------
# Retry / reuse decision matrix.
# ---------------------------------------------------------------------------

def test_load_retry_chapters_picks_failed_and_qa_failed(tmp_path):
    chapters = [
        {"title": "ok", "status": "translated", "stages": {"translation": "done"}, "judge_pass_check": True},
        {"title": "boom", "status": "failed", "stages": {"translation": "failed"}, "error": "x"},
        {"title": "qa", "status": "qa_failed", "blocking_issues": ["b"], "judge_pass_check": False},
    ]
    _write_report(str(tmp_path), chapters)
    retry = load_retry_chapters(str(tmp_path), include_incomplete=False)
    assert set(retry) == {"boom", "qa"}


def test_load_retry_chapters_incomplete_mode_adds_warnings(tmp_path):
    chapters = [
        {"title": "ok", "status": "translated", "stages": {"translation": "done"}, "judge_pass_check": True},
        {
            "title": "degraded",
            "status": "translated",
            "stages": {"translation": "done", "judge": "done"},
            "judge_pass_check": True,
            "warnings": ["Context retrieval was degraded"],
        },
    ]
    _write_report(str(tmp_path), chapters)

    # Strict mode: degraded chapter is NOT retried (translation succeeded).
    strict = load_retry_chapters(str(tmp_path), include_incomplete=False)
    assert strict == {}

    # Incomplete mode: warnings flip it into the retry set.
    incomplete = load_retry_chapters(str(tmp_path), include_incomplete=True)
    assert set(incomplete) == {"degraded"}


def test_load_retry_chapters_skips_explicitly_skipped(tmp_path):
    chapters = [
        {"title": "skip1", "status": "skipped_not_failed", "stages": {}},
        {"title": "skip2", "status": "skipped_existing", "stages": {}},
    ]
    _write_report(str(tmp_path), chapters)
    assert load_retry_chapters(str(tmp_path), include_incomplete=True) == {}


def test_can_reuse_existing_translation_matrix():
    """Matrix: when an existing output file may be reused without re-calling
    the translation model."""
    # Translation stage done -> always reusable.
    assert can_reuse_existing_translation(
        {"status": "qa_failed", "stages": {"translation": "done"}}
    ) is True
    # Translation stage explicitly failed -> never reusable.
    assert can_reuse_existing_translation(
        {"status": "failed", "stages": {"translation": "failed"}, "error": "translation failed"}
    ) is False
    # Failed for a non-translation reason (e.g. judge) -> reusable.
    assert can_reuse_existing_translation(
        {"status": "failed", "stages": {"translation": "done"}, "error": "judge failed"}
    ) is True
    # Not started -> nothing to reuse.
    assert can_reuse_existing_translation(
        {"status": "pending", "stages": {"translation": "not_started"}}
    ) is False


def test_should_retry_checkpoint_run_matrix():
    """Matrix over SQLite checkpoint shapes."""
    # Skipped checkpoints are never retried.
    assert should_retry_checkpoint_run(
        {"status": "skipped_existing"}, include_incomplete=True
    ) is False
    # Failed status -> retry.
    assert should_retry_checkpoint_run(
        {"status": "failed"}, include_incomplete=False
    ) is True
    # QA-failed via judge pass_check=False -> retry.
    assert should_retry_checkpoint_run(
        {"status": "judge_done", "judge_result": {"pass_check": False, "blocking_issues": ["x"]}},
        include_incomplete=False,
    ) is True
    # Fully done -> not retried in strict mode...
    assert should_retry_checkpoint_run(
        {
            "status": "summary_done",
            "judge_result": {"pass_check": True},
            "stages": {"translation_done": "done", "judge_done": "done", "summary_done": "done"},
        },
        include_incomplete=False,
    ) is False
    # ...and not retried in incomplete mode either when all stages are done.
    assert should_retry_checkpoint_run(
        {
            "status": "summary_done",
            "judge_result": {"pass_check": True},
            "stages": {"translation_done": "done", "judge_done": "done", "summary_done": "done"},
        },
        include_incomplete=True,
    ) is False
    # Incomplete mode catches a missing stage that strict mode ignores.
    assert should_retry_checkpoint_run(
        {
            "status": "translation_done",
            "judge_result": {"pass_check": True},
            "stages": {"translation_done": "done", "judge_done": "done", "summary_done": "not_started"},
        },
        include_incomplete=False,
    ) is False
    assert should_retry_checkpoint_run(
        {
            "status": "translation_done",
            "judge_result": {"pass_check": True},
            "stages": {"translation_done": "done", "judge_done": "done", "summary_done": "not_started"},
        },
        include_incomplete=True,
    ) is True
