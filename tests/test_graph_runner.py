# tests/test_graph_runner.py
import unittest
from unittest.mock import patch, MagicMock
import os
import shutil
import json
import tempfile

# Устанавливаем переменную окружения для тестов
os.environ["GOOGLE_API_KEY"] = "AIza-real-looking-key"

from src.Perevod.graph_runner import (
    run_translation_workflow,
    _can_reuse_existing_translation,
    _checkpoint_run_to_report_chapter,
    _remaining_quality_error,
    _write_workflow_report,
)
from Perevod.config import PROJECT_ROOT
from Perevod.utils.api_errors import ApiErrorInfo, GeminiAPIError


class TestGraphRunner(unittest.TestCase):
    def setUp(self):
        self.test_temp_dir = tempfile.mkdtemp(
            prefix="_test_graph_runner_",
            dir=PROJECT_ROOT,
        )

    def tearDown(self):
        shutil.rmtree(self.test_temp_dir, ignore_errors=True)

    @patch("src.Perevod.graph_runner._is_process_running", return_value=False)
    def test_acquire_workflow_lock_removes_stale_lock(self, mock_process_running):
        from src.Perevod.graph_runner import _acquire_workflow_lock

        output_dir = os.path.join(self.test_temp_dir, "stale_lock")
        os.makedirs(output_dir, exist_ok=True)
        lock_path = os.path.join(output_dir, ".translation.lock")
        with open(lock_path, "w", encoding="utf-8") as lock_file:
            lock_file.write("pid=999999\n")

        acquired_lock = _acquire_workflow_lock(output_dir)

        self.assertEqual(acquired_lock, lock_path)
        mock_process_running.assert_called_once_with(999999)
        with open(lock_path, encoding="utf-8") as lock_file:
            self.assertIn(f"pid={os.getpid()}", lock_file.read())

    def test_can_reuse_existing_translation_rejects_failed_output_stage(self):
        self.assertFalse(
            _can_reuse_existing_translation(
                {
                    "status": "failed",
                    "stages": {
                        "translation": "done",
                        "output": "failed",
                        "judge": "not_started",
                    },
                    "error": "Failed stage: output",
                }
            )
        )

    def test_remaining_quality_error_uses_checkpoint_judge_blockers(self):
        error = _remaining_quality_error(
            {
                "blocking_issues": [],
                "processed_chapters": [],
                "judge_results": [],
                "chapter_runs": {
                    "chapter1": {
                        "judge_result": {
                            "blocking_issues": ["Missing canonical term"]
                        }
                    }
                },
            }
        )

        self.assertIn("QA failed after refinement limit", error)
        self.assertIn("1 blocking issue", error)

    def test_remaining_quality_error_treats_failed_judge_without_blockers_as_failure(self):
        error = _remaining_quality_error(
            {
                "blocking_issues": [],
                "processed_chapters": [],
                "judge_results": [
                    {
                        "title": "chapter1",
                        "pass_check": False,
                        "blocking_issues": [],
                    }
                ],
            }
        )

        self.assertIn("QA failed after refinement limit", error)
        self.assertIn("1 blocking issue", error)

    def test_remaining_quality_error_ignores_stale_global_blockers_after_passing_judge(self):
        error = _remaining_quality_error(
            {
                "blocking_issues": ["Old issue before refine"],
                "processed_chapters": [{"title": "chapter1", "blocking_issues": []}],
                "judge_results": [
                    {
                        "title": "chapter1",
                        "pass_check": True,
                        "blocking_issues": [],
                    }
                ],
            }
        )

        self.assertIsNone(error)

    def test_remaining_quality_error_keeps_global_blockers_for_unjudged_chapters(self):
        error = _remaining_quality_error(
            {
                "blocking_issues": ["chapter2 still has an omission"],
                "processed_chapters": [{"title": "chapter1", "blocking_issues": []}],
                "judge_results": [
                    {
                        "title": "chapter1",
                        "pass_check": True,
                        "blocking_issues": [],
                    }
                ],
                "chapters_to_process": [
                    {"title": "chapter1"},
                    {"title": "chapter2"},
                ],
            }
        )

        self.assertIn("QA failed after refinement limit", error)
        self.assertIn("1 blocking issue", error)

    def test_write_workflow_report_includes_structured_gateway_error_metadata(self):
        output_dir = os.path.join(self.test_temp_dir, "gateway_error_report")
        os.makedirs(output_dir, exist_ok=True)
        original_error = RuntimeError("401 UNAUTHENTICATED api_key=AIza-secret-key")
        gateway_error = GeminiAPIError(
            model_name="gemini-3-flash-preview",
            operation="generateContent",
            info=ApiErrorInfo(
                category="auth",
                retryable=False,
                status_code=401,
                message="401 UNAUTHENTICATED api_key=[REDACTED]",
            ),
            original_error=original_error,
        )

        report_path = _write_workflow_report(
            "gateway_error_project",
            output_dir,
            [
                {
                    "title": "chapter1",
                    "status": "pending",
                    "input_path": "chapter1.txt",
                    "output_path": "chapter1.ru.txt",
                }
            ],
            {"error": gateway_error, "processed_chapters": []},
        )

        with open(report_path, encoding="utf-8") as report_file:
            report = json.load(report_file)

        self.assertEqual(report["error_category"], "auth")
        self.assertFalse(report["error_retryable"])
        self.assertEqual(report["error_status_code"], 401)
        self.assertEqual(report["error_operation"], "generateContent")
        self.assertEqual(report["error_model"], "gemini-3-flash-preview")
        self.assertNotIn("AIza-secret-key", report["error"])
        chapter = report["chapters"][0]
        self.assertEqual(chapter["status"], "failed")
        self.assertEqual(chapter["error_category"], "auth")
        self.assertFalse(chapter["error_retryable"])
        self.assertEqual(chapter["error_status_code"], 401)

    def test_write_workflow_report_preserves_stage_error_metadata(self):
        output_dir = os.path.join(self.test_temp_dir, "stage_error_metadata_report")
        os.makedirs(output_dir, exist_ok=True)

        report_path = _write_workflow_report(
            "stage_error_project",
            output_dir,
            [
                {
                    "title": "chapter1",
                    "status": "pending",
                    "input_path": "chapter1.txt",
                    "output_path": "chapter1.ru.txt",
                }
            ],
            {
                "processed_chapters": [{"title": "chapter1"}],
                "summary_errors": [
                    {
                        "title": "chapter1",
                        "error": "Gemini API error",
                        "error_category": "quota",
                        "error_retryable": False,
                        "error_status_code": 429,
                        "error_operation": "generateContent",
                        "error_model": "gemini-3-flash-preview",
                    }
                ],
                "error": "Summary failed for chapter 'chapter1': Gemini API error",
            },
        )

        with open(report_path, encoding="utf-8") as report_file:
            report = json.load(report_file)

        chapter = report["chapters"][0]
        self.assertEqual(chapter["status"], "failed")
        self.assertEqual(chapter["stages"]["summary"], "failed")
        self.assertEqual(chapter["error_category"], "quota")
        self.assertFalse(chapter["error_retryable"])
        self.assertEqual(chapter["error_status_code"], 429)
        self.assertEqual(chapter["error_operation"], "generateContent")
        self.assertEqual(chapter["error_model"], "gemini-3-flash-preview")

    def test_write_workflow_report_surfaces_context_warnings_without_failure(self):
        output_dir = os.path.join(self.test_temp_dir, "context_warning_report")
        os.makedirs(output_dir, exist_ok=True)

        report_path = _write_workflow_report(
            "context_warning_project",
            output_dir,
            [
                {
                    "title": "chapter1",
                    "status": "pending",
                    "input_path": "chapter1.txt",
                    "output_path": "chapter1.ru.txt",
                }
            ],
            {
                "processed_chapters": [{"title": "chapter1"}],
                "chapter_contexts": {"chapter1": "Lexical fallback context"},
                "judge_results": [{"title": "chapter1", "pass_check": True}],
                "context_warnings": [
                    {
                        "title": "chapter1",
                        "scope": "semantic_lore",
                        "error": "embedding quota exhausted",
                        "error_category": "quota",
                        "error_retryable": False,
                        "error_status_code": 429,
                        "error_operation": "embedContent",
                        "error_model": "gemini-embedding-2",
                    }
                ],
                "error": None,
            },
        )

        with open(report_path, encoding="utf-8") as report_file:
            report = json.load(report_file)

        chapter = report["chapters"][0]
        self.assertEqual(chapter["status"], "translated")
        self.assertEqual(chapter["stages"]["context"], "done")
        self.assertIsNone(chapter["error"])
        self.assertIn("embedding quota exhausted", chapter["warnings"][0])
        self.assertEqual(chapter["context_warnings"][0]["error_category"], "quota")
        self.assertEqual(report["failed_count"], 0)
        self.assertEqual(report["warning_count"], 1)

    def test_write_workflow_report_tolerates_legacy_context_error_strings(self):
        output_dir = os.path.join(self.test_temp_dir, "legacy_context_error_report")
        os.makedirs(output_dir, exist_ok=True)

        report_path = _write_workflow_report(
            "legacy_context_error_project",
            output_dir,
            [
                {
                    "title": "chapter1",
                    "status": "pending",
                    "input_path": "chapter1.txt",
                    "output_path": "chapter1.ru.txt",
                }
            ],
            {
                "processed_chapters": [{"title": "chapter1"}],
                "chapter_contexts": {"chapter1": "Context built before failure"},
                "judge_results": [{"title": "chapter1", "pass_check": True}],
                "context_errors": ["legacy memory db locked"],
                "error": "Context retrieval failed: legacy memory db locked",
            },
        )

        self.assertIsNotNone(report_path)
        with open(report_path, encoding="utf-8") as report_file:
            report = json.load(report_file)

        chapter = report["chapters"][0]
        self.assertEqual(chapter["status"], "failed")
        self.assertEqual(chapter["stages"]["context"], "failed")
        self.assertIn("legacy memory db locked", chapter["error"])

    def test_write_workflow_report_tolerates_legacy_analysis_error_strings(self):
        output_dir = os.path.join(self.test_temp_dir, "legacy_analysis_error_report")
        os.makedirs(output_dir, exist_ok=True)

        report_path = _write_workflow_report(
            "legacy_analysis_error_project",
            output_dir,
            [
                {
                    "title": "chapter1",
                    "status": "pending",
                    "input_path": "chapter1.txt",
                    "output_path": "chapter1.ru.txt",
                }
            ],
            {
                "processed_chapters": [{"title": "chapter1"}],
                "analysis_errors": ["legacy analysis invalid json"],
                "error": "Analysis failed for chapter 'chapter1': legacy analysis invalid json",
            },
        )

        self.assertIsNotNone(report_path)
        with open(report_path, encoding="utf-8") as report_file:
            report = json.load(report_file)

        chapter = report["chapters"][0]
        self.assertEqual(chapter["status"], "failed")
        self.assertEqual(chapter["stages"]["analysis"], "failed")
        self.assertIn("legacy analysis invalid json", chapter["error"])

    def test_write_workflow_report_binds_legacy_analysis_error_string_to_chapter(self):
        output_dir = os.path.join(self.test_temp_dir, "legacy_analysis_error_title_report")
        os.makedirs(output_dir, exist_ok=True)

        report_path = _write_workflow_report(
            "legacy_analysis_error_title_project",
            output_dir,
            [
                {
                    "title": "chapter1",
                    "status": "pending",
                    "input_path": "chapter1.txt",
                    "output_path": "chapter1.ru.txt",
                }
            ],
            {
                "processed_chapters": [{"title": "chapter1"}],
                "analysis_errors": [
                    "Analysis failed for chapter 'chapter1': legacy analysis invalid json"
                ],
                "error": None,
            },
        )

        self.assertIsNotNone(report_path)
        with open(report_path, encoding="utf-8") as report_file:
            report = json.load(report_file)

        chapter = report["chapters"][0]
        self.assertEqual(chapter["status"], "failed")
        self.assertEqual(chapter["stages"]["analysis"], "failed")
        self.assertIn("legacy analysis invalid json", chapter["error"])

    def test_write_workflow_report_tolerates_legacy_summary_error_strings(self):
        output_dir = os.path.join(self.test_temp_dir, "legacy_summary_error_report")
        os.makedirs(output_dir, exist_ok=True)

        report_path = _write_workflow_report(
            "legacy_summary_error_project",
            output_dir,
            [
                {
                    "title": "chapter1",
                    "status": "pending",
                    "input_path": "chapter1.txt",
                    "output_path": "chapter1.ru.txt",
                }
            ],
            {
                "processed_chapters": [{"title": "chapter1"}],
                "judge_results": [{"title": "chapter1", "pass_check": True}],
                "summary_errors": ["legacy summary invalid json"],
                "error": "Summary failed for chapter 'chapter1': legacy summary invalid json",
            },
        )

        self.assertIsNotNone(report_path)
        with open(report_path, encoding="utf-8") as report_file:
            report = json.load(report_file)

        chapter = report["chapters"][0]
        self.assertEqual(chapter["status"], "failed")
        self.assertEqual(chapter["stages"]["summary"], "failed")
        self.assertIn("legacy summary invalid json", chapter["error"])

    def test_write_workflow_report_preserves_top_level_error_metadata(self):
        output_dir = os.path.join(self.test_temp_dir, "top_level_error_metadata_report")
        os.makedirs(output_dir, exist_ok=True)

        report_path = _write_workflow_report(
            "top_level_error_project",
            output_dir,
            [
                {
                    "title": "chapter1",
                    "status": "pending",
                    "input_path": "chapter1.txt",
                    "output_path": "chapter1.ru.txt",
                }
            ],
            {
                "processed_chapters": [{"title": "chapter1"}],
                "error": "Ошибка Судьи для главы 'chapter1': quota exhausted",
                "error_category": "quota",
                "error_retryable": False,
                "error_status_code": 429,
                "error_operation": "generateContent",
                "error_model": "gemini-3-flash-preview",
            },
        )

        with open(report_path, encoding="utf-8") as report_file:
            report = json.load(report_file)

        self.assertEqual(report["error_category"], "quota")
        chapter = report["chapters"][0]
        self.assertEqual(chapter["stages"]["judge"], "failed")
        self.assertEqual(chapter["error_category"], "quota")
        self.assertFalse(chapter["error_retryable"])
        self.assertEqual(chapter["error_status_code"], 429)

    def test_checkpoint_run_to_report_chapter_preserves_glossary_and_output_stages(self):
        chapter = _checkpoint_run_to_report_chapter(
            {
                "title": "chapter1",
                "status": "failed",
                "stages": {
                    "glossary_updated": "done",
                    "translation_done": "done",
                    "output_written": "failed",
                },
                "error": "Failed stage: output",
            }
        )

        self.assertEqual(chapter["stages"]["glossary"], "done")
        self.assertEqual(chapter["stages"]["translation"], "done")
        self.assertEqual(chapter["stages"]["output"], "failed")

    def test_checkpoint_run_to_report_chapter_marks_failed_judge_without_blockers(self):
        chapter = _checkpoint_run_to_report_chapter(
            {
                "title": "chapter1",
                "status": "judge_done",
                "stages": {
                    "translation_done": "done",
                    "output_written": "done",
                    "judge_done": "done",
                },
                "judge_result": {
                    "pass_check": False,
                    "blocking_issues": [],
                    "score": 5,
                },
                "error": None,
            }
        )

        self.assertEqual(chapter["status"], "qa_failed")
        self.assertEqual(
            chapter["blocking_issues"],
            ["Judge failed without blocking issue details"],
        )
        self.assertEqual(chapter["stages"]["refine"], "needed")
        self.assertIsNone(chapter["error"])

    @patch("src.Perevod.graph_runner._is_process_running", return_value=True)
    def test_acquire_workflow_lock_keeps_active_lock(self, mock_process_running):
        from src.Perevod.graph_runner import _acquire_workflow_lock

        output_dir = os.path.join(self.test_temp_dir, "active_lock")
        os.makedirs(output_dir, exist_ok=True)
        lock_path = os.path.join(output_dir, ".translation.lock")
        with open(lock_path, "w", encoding="utf-8") as lock_file:
            lock_file.write("pid=123\n")

        with self.assertRaises(RuntimeError):
            _acquire_workflow_lock(output_dir)

        mock_process_running.assert_called_once_with(123)

    def test_acquire_workflow_lock_recovers_old_malformed_lock(self):
        from src.Perevod.graph_runner import _acquire_workflow_lock

        output_dir = os.path.join(self.test_temp_dir, "malformed_lock")
        os.makedirs(output_dir, exist_ok=True)
        lock_path = os.path.join(output_dir, ".translation.lock")
        with open(lock_path, "w", encoding="utf-8") as lock_file:
            lock_file.write("started_at=unknown\n")
        os.utime(lock_path, (0, 0))

        acquired_lock = _acquire_workflow_lock(output_dir)

        self.assertEqual(acquired_lock, lock_path)
        with open(lock_path, encoding="utf-8") as lock_file:
            self.assertIn(f"pid={os.getpid()}", lock_file.read())

    def test_acquire_workflow_lock_keeps_recent_malformed_lock(self):
        from src.Perevod.graph_runner import _acquire_workflow_lock

        output_dir = os.path.join(self.test_temp_dir, "recent_malformed_lock")
        os.makedirs(output_dir, exist_ok=True)
        lock_path = os.path.join(output_dir, ".translation.lock")
        with open(lock_path, "w", encoding="utf-8") as lock_file:
            lock_file.write("started_at=unknown\n")

        with self.assertRaises(RuntimeError):
            _acquire_workflow_lock(output_dir)

        self.assertTrue(os.path.exists(lock_path))

    def test_workflow_rejects_same_input_and_output_dir(self):
        chapter_dir = os.path.join(self.test_temp_dir, "same_dir")
        os.makedirs(chapter_dir, exist_ok=True)
        with open(os.path.join(chapter_dir, "chapter1.txt"), "w", encoding="utf-8") as f:
            f.write("chapter source")

        with self.assertRaisesRegex(ValueError, "input_dir and output_dir"):
            run_translation_workflow(
                "test_project_same_io",
                {
                    "input_dir": chapter_dir,
                    "output_dir": chapter_dir,
                    "GOOGLE_API_KEY": "AIza-real-looking-key",
                },
            )

    def test_workflow_rejects_output_inside_input_dir(self):
        input_dir = os.path.join(self.test_temp_dir, "input")
        output_dir = os.path.join(input_dir, "translated")
        os.makedirs(input_dir, exist_ok=True)
        with open(os.path.join(input_dir, "chapter1.txt"), "w", encoding="utf-8") as f:
            f.write("chapter source")

        with self.assertRaisesRegex(ValueError, "inside input_dir"):
            run_translation_workflow(
                "test_project_nested_output",
                {
                    "input_dir": input_dir,
                    "output_dir": output_dir,
                    "GOOGLE_API_KEY": "AIza-real-looking-key",
                },
            )

        self.assertFalse(os.path.exists(output_dir))

    def test_workflow_rejects_input_inside_output_dir(self):
        output_dir = os.path.join(self.test_temp_dir, "output")
        input_dir = os.path.join(output_dir, "input")
        os.makedirs(input_dir, exist_ok=True)
        with open(os.path.join(input_dir, "chapter1.txt"), "w", encoding="utf-8") as f:
            f.write("chapter source")

        with self.assertRaisesRegex(ValueError, "inside output_dir"):
            run_translation_workflow(
                "test_project_nested_input",
                {
                    "input_dir": input_dir,
                    "output_dir": output_dir,
                    "GOOGLE_API_KEY": "AIza-real-looking-key",
                },
            )

    def test_workflow_rejects_symlink_output_dir(self):
        real_output = os.path.join(self.test_temp_dir, "real_output")
        link_output = os.path.join(self.test_temp_dir, "link_output")
        input_dir = os.path.join(self.test_temp_dir, "input")
        os.makedirs(real_output, exist_ok=True)
        os.makedirs(input_dir, exist_ok=True)
        with open(os.path.join(input_dir, "chapter1.txt"), "w", encoding="utf-8") as f:
            f.write("chapter source")
        try:
            os.symlink(real_output, link_output, target_is_directory=True)
        except (OSError, NotImplementedError) as exc:
            self.skipTest(f"Symlinks are unavailable in this environment: {exc}")

        with self.assertRaisesRegex(ValueError, "symlink"):
            run_translation_workflow(
                "test_project_symlink_output",
                {
                    "input_dir": input_dir,
                    "output_dir": link_output,
                    "GOOGLE_API_KEY": "AIza-real-looking-key",
                },
            )

    def test_write_workflow_report_serializes_non_string_error_safely(self):
        output_dir = os.path.join(self.test_temp_dir, "report_output")
        os.makedirs(output_dir, exist_ok=True)

        report_path = _write_workflow_report(
            "Book",
            output_dir,
            [
                {
                    "title": "chapter1",
                    "input_path": "input/chapter1.txt",
                    "output_path": "output/chapter1.txt",
                }
            ],
            {"error": RuntimeError("translation failed"), "processed_chapters": []},
            {"input_dir": "input", "output_dir": "output"},
        )

        self.assertEqual(
            report_path,
            os.path.join(output_dir, "translation_report.json"),
        )
        self.assertFalse(os.path.exists(f"{report_path}.tmp"))
        with open(report_path, encoding="utf-8") as report_file:
            report = json.load(report_file)
        self.assertIn("translation failed", report["error"])

    def test_write_workflow_report_includes_context_analysis_and_refine_stages(self):
        output_dir = os.path.join(self.test_temp_dir, "stage_report_output")
        os.makedirs(output_dir, exist_ok=True)

        report_path = _write_workflow_report(
            "Book",
            output_dir,
            [
                {
                    "title": "chapter1",
                    "input_path": "input/chapter1.txt",
                    "output_path": "output/chapter1.txt",
                },
                {
                    "title": "chapter2",
                    "input_path": "input/chapter2.txt",
                    "output_path": "output/chapter2.txt",
                },
            ],
            {
                "processed_chapters": [
                    {
                        "title": "chapter1",
                        "refined": True,
                        "output_backup_path": "output/chapter1.txt.bak",
                    },
                    {"title": "chapter2"},
                ],
                "chapter_contexts": {
                    "chapter1": "chapter1 context",
                    "chapter2": "chapter2 context",
                },
                "analysis_results": [
                    {"source_chapter": "chapter1", "english_term": "Lotus"},
                    {"source_chapter": "chapter2", "english_term": "Sword"},
                ],
                "judge_results": [
                    {"title": "chapter1", "pass_check": True},
                    {"title": "chapter2", "pass_check": True},
                ],
                "chapter_summaries": [
                    {"title": "chapter1"},
                    {"title": "chapter2"},
                ],
                "error": None,
            },
            {"input_dir": "input", "output_dir": "output"},
        )

        with open(report_path, encoding="utf-8") as report_file:
            report = json.load(report_file)

        stages = {chapter["title"]: chapter["stages"] for chapter in report["chapters"]}
        self.assertEqual(stages["chapter1"]["context"], "done")
        self.assertEqual(stages["chapter1"]["analysis"], "done")
        self.assertEqual(stages["chapter1"]["refine"], "done")
        self.assertEqual(stages["chapter2"]["context"], "done")
        self.assertEqual(stages["chapter2"]["analysis"], "done")
        self.assertEqual(stages["chapter2"]["refine"], "not_needed")
        self.assertEqual(
            report["chapters"][0]["output_backup_path"],
            "output/chapter1.txt.bak",
        )

    def test_write_workflow_report_uses_checkpoint_stage_evidence(self):
        output_dir = os.path.join(self.test_temp_dir, "checkpoint_stage_report_output")
        os.makedirs(output_dir, exist_ok=True)

        report_path = _write_workflow_report(
            "Book",
            output_dir,
            [
                {
                    "title": "chapter1",
                    "input_path": "input/chapter1.txt",
                    "output_path": "output/chapter1.txt",
                }
            ],
            {
                "processed_chapters": [{"title": "chapter1"}],
                "chapter_runs": {
                    "chapter1": {
                        "stages": {
                            "context_retrieved": "done",
                            "analysis_done": "done",
                            "glossary_updated": "done",
                            "translation_done": "done",
                            "output_written": "done",
                            "judge_done": "done",
                            "refine_done": "done",
                            "summary_done": "done",
                            "memory_updated": "done",
                        }
                    }
                },
                "error": None,
            },
            {"input_dir": "input", "output_dir": "output"},
        )

        with open(report_path, encoding="utf-8") as report_file:
            report = json.load(report_file)

        self.assertEqual(
            report["chapters"][0]["stages"],
            {
                "context": "done",
                "analysis": "done",
                "glossary": "done",
                "translation": "done",
                "output": "done",
                "judge": "done",
                "refine": "done",
                "summary": "done",
                "memory": "done",
            },
        )

    def test_write_workflow_report_marks_refine_failure_without_failing_judge(self):
        output_dir = os.path.join(self.test_temp_dir, "refine_failure_report_output")
        os.makedirs(output_dir, exist_ok=True)

        report_path = _write_workflow_report(
            "Book",
            output_dir,
            [
                {
                    "title": "chapter1",
                    "input_path": "input/chapter1.txt",
                    "output_path": "output/chapter1.txt",
                }
            ],
            {
                "processed_chapters": [
                    {
                        "title": "chapter1",
                        "blocking_issues": [{"category": "accuracy"}],
                    }
                ],
                "judge_results": [{"title": "chapter1", "pass_check": False}],
                "error": "Ошибка Редактора для главы 'chapter1': invalid edit json",
            },
            {"input_dir": "input", "output_dir": "output"},
        )

        with open(report_path, encoding="utf-8") as report_file:
            report = json.load(report_file)

        stages = report["chapters"][0]["stages"]
        self.assertEqual(stages["judge"], "done")
        self.assertEqual(stages["refine"], "failed")

    def test_release_workflow_lock_does_not_remove_other_process_lock(self):
        from src.Perevod.graph_runner import _release_workflow_lock

        output_dir = os.path.join(self.test_temp_dir, "foreign_lock")
        os.makedirs(output_dir, exist_ok=True)
        lock_path = os.path.join(output_dir, ".translation.lock")
        with open(lock_path, "w", encoding="utf-8") as lock_file:
            lock_file.write("pid=123456\n")

        _release_workflow_lock(lock_path)

        self.assertTrue(os.path.exists(lock_path))

    def test_retry_failed_requires_existing_report_and_releases_lock(self):
        project_name = "test_retry_missing_report"
        input_dir = os.path.join(self.test_temp_dir, project_name, "input")
        output_dir = os.path.join(self.test_temp_dir, project_name, "output")
        os.makedirs(input_dir, exist_ok=True)
        os.makedirs(output_dir, exist_ok=True)
        with open(os.path.join(input_dir, "chapter1.txt"), "w", encoding="utf-8") as f:
            f.write("chapter1 source")

        with self.assertRaises(FileNotFoundError):
            run_translation_workflow(
                project_name,
                {
                    "input_dir": input_dir,
                    "output_dir": output_dir,
                    "GOOGLE_API_KEY": "AIza-real-looking-key",
                    "retry_failed": True,
                },
            )

        self.assertFalse(os.path.exists(os.path.join(output_dir, ".translation.lock")))

    @patch("src.Perevod.graph_runner.DatabaseManager")
    @patch("src.Perevod.graph_runner.KnowledgeBaseManager")
    @patch("src.Perevod.graph_runner.LLMProvider")
    @patch("src.Perevod.graph_runner.build_graph")
    def test_retry_failed_uses_sqlite_checkpoint_when_report_is_missing(
        self,
        mock_build_graph,
        mock_llm_provider,
        mock_kb_manager,
        mock_db_manager,
    ):
        captured_state = {}

        def invoke(state):
            captured_state["chapters_to_process"] = state["chapters_to_process"]
            return {
                "processed_chapters": state["chapters_to_process"],
                "judge_results": [{"title": "chapter1", "pass_check": True}],
                "chapter_summaries": [{"title": "chapter1"}],
                "summary_errors": [],
                "error": None,
            }

        mock_app = MagicMock()
        mock_app.invoke.side_effect = invoke
        mock_build_graph.return_value = mock_app

        db_manager = mock_db_manager.return_value
        db_manager.get_chapter_runs.return_value = {
            "chapter1": {
                "title": "chapter1",
                "input_path": "input/chapter1.txt",
                "output_path": "output/chapter1.txt",
                "status": "failed",
                "stages": {
                    "discovered": "done",
                    "translation_done": "done",
                    "output_written": "done",
                    "judge_done": "failed",
                },
                "error": "Ошибка Судьи для главы 'chapter1': invalid judge response",
            }
        }

        mock_kb = MagicMock()
        mock_kb.collection.count.return_value = 0
        mock_kb_manager.return_value = mock_kb

        project_name = "test_project_retry_sqlite_checkpoint"
        input_dir = os.path.join(self.test_temp_dir, project_name, "input")
        output_dir = os.path.join(self.test_temp_dir, project_name, "output")
        os.makedirs(input_dir, exist_ok=True)
        os.makedirs(output_dir, exist_ok=True)
        with open(os.path.join(input_dir, "chapter1.txt"), "w", encoding="utf-8") as f:
            f.write("chapter1 source")
        with open(os.path.join(output_dir, "chapter1.txt"), "w", encoding="utf-8") as f:
            f.write("usable translated text")

        run_translation_workflow(
            project_name,
            {
                "input_dir": input_dir,
                "output_dir": output_dir,
                "GOOGLE_API_KEY": "AIza-real-looking-key",
                "retry_failed": True,
            },
        )

        self.assertEqual(
            [chapter["title"] for chapter in captured_state["chapters_to_process"]],
            ["chapter1"],
        )
        self.assertTrue(
            captured_state["chapters_to_process"][0]["reuse_existing_translation"]
        )
        self.assertIn(
            "invalid judge response",
            captured_state["chapters_to_process"][0]["previous_retry_error"],
        )

    @patch("src.Perevod.graph_runner.DatabaseManager")
    @patch("src.Perevod.graph_runner.KnowledgeBaseManager")
    @patch("src.Perevod.graph_runner.LLMProvider")
    @patch("src.Perevod.graph_runner.build_graph")
    def test_retry_failed_sqlite_checkpoint_does_not_reuse_failed_output(
        self,
        mock_build_graph,
        mock_llm_provider,
        mock_kb_manager,
        mock_db_manager,
    ):
        captured_state = {}

        def invoke(state):
            captured_state["chapters_to_process"] = state["chapters_to_process"]
            return {
                "processed_chapters": state["chapters_to_process"],
                "error": None,
            }

        mock_app = MagicMock()
        mock_app.invoke.side_effect = invoke
        mock_build_graph.return_value = mock_app

        db_manager = mock_db_manager.return_value
        db_manager.get_chapter_runs.return_value = {
            "chapter1": {
                "title": "chapter1",
                "input_path": "input/chapter1.txt",
                "output_path": "output/chapter1.txt",
                "status": "failed",
                "stages": {
                    "discovered": "done",
                    "translation_done": "done",
                    "output_written": "failed",
                    "judge_done": "not_started",
                },
                "error": "Failed stage: output",
            }
        }

        mock_kb = MagicMock()
        mock_kb.collection.count.return_value = 0
        mock_kb_manager.return_value = mock_kb

        project_name = "test_project_retry_sqlite_failed_output"
        input_dir = os.path.join(self.test_temp_dir, project_name, "input")
        output_dir = os.path.join(self.test_temp_dir, project_name, "output")
        os.makedirs(input_dir, exist_ok=True)
        os.makedirs(output_dir, exist_ok=True)
        with open(os.path.join(input_dir, "chapter1.txt"), "w", encoding="utf-8") as f:
            f.write("chapter1 source")
        with open(os.path.join(output_dir, "chapter1.txt"), "w", encoding="utf-8") as f:
            f.write("stale or partial translation")

        run_translation_workflow(
            project_name,
            {
                "input_dir": input_dir,
                "output_dir": output_dir,
                "GOOGLE_API_KEY": "AIza-real-looking-key",
                "retry_failed": True,
            },
        )

        self.assertEqual(
            [chapter["title"] for chapter in captured_state["chapters_to_process"]],
            ["chapter1"],
        )
        self.assertNotIn(
            "reuse_existing_translation",
            captured_state["chapters_to_process"][0],
        )

    @patch("src.Perevod.graph_runner.DatabaseManager")
    @patch("src.Perevod.graph_runner.KnowledgeBaseManager")
    @patch("src.Perevod.graph_runner.LLMProvider")
    @patch("src.Perevod.graph_runner.build_graph")
    def test_retry_incomplete_uses_sqlite_checkpoint_summary_failure(
        self,
        mock_build_graph,
        mock_llm_provider,
        mock_kb_manager,
        mock_db_manager,
    ):
        captured_state = {}

        def invoke(state):
            captured_state["chapters_to_process"] = state["chapters_to_process"]
            return {
                "processed_chapters": state["chapters_to_process"],
                "judge_results": [{"title": "chapter1", "pass_check": True}],
                "chapter_summaries": [{"title": "chapter1"}],
                "summary_errors": [],
                "error": None,
            }

        mock_app = MagicMock()
        mock_app.invoke.side_effect = invoke
        mock_build_graph.return_value = mock_app

        db_manager = mock_db_manager.return_value
        db_manager.get_chapter_runs.return_value = {
            "chapter1": {
                "title": "chapter1",
                "input_path": "input/chapter1.txt",
                "output_path": "output/chapter1.txt",
                "status": "summary_done",
                "stages": {
                    "discovered": "done",
                    "context_retrieved": "done",
                    "analysis_done": "done",
                    "glossary_updated": "done",
                    "translation_done": "done",
                    "output_written": "done",
                    "judge_done": "done",
                    "summary_done": "failed",
                },
                "judge_result": {
                    "pass_check": False,
                    "blocking_issues": ["Missing canonical term"],
                    "severity": "high",
                    "score": 4,
                },
                "error": "invalid summary json",
            }
        }

        mock_kb = MagicMock()
        mock_kb.collection.count.return_value = 0
        mock_kb_manager.return_value = mock_kb

        project_name = "test_project_retry_incomplete_sqlite_checkpoint"
        input_dir = os.path.join(self.test_temp_dir, project_name, "input")
        output_dir = os.path.join(self.test_temp_dir, project_name, "output")
        os.makedirs(input_dir, exist_ok=True)
        os.makedirs(output_dir, exist_ok=True)
        with open(os.path.join(input_dir, "chapter1.txt"), "w", encoding="utf-8") as f:
            f.write("chapter1 source")
        with open(os.path.join(output_dir, "chapter1.txt"), "w", encoding="utf-8") as f:
            f.write("usable translated text")

        run_translation_workflow(
            project_name,
            {
                "input_dir": input_dir,
                "output_dir": output_dir,
                "GOOGLE_API_KEY": "AIza-real-looking-key",
                "retry_incomplete": True,
            },
        )

        self.assertEqual(
            [chapter["title"] for chapter in captured_state["chapters_to_process"]],
            ["chapter1"],
        )
        self.assertTrue(
            captured_state["chapters_to_process"][0]["reuse_existing_translation"]
        )
        self.assertEqual(
            captured_state["chapters_to_process"][0]["blocking_issues"],
            ["Missing canonical term"],
        )
        self.assertEqual(
            captured_state["chapters_to_process"][0]["previous_retry_error"],
            "invalid summary json",
        )
        self.assertNotIn("force_rejudge", captured_state["chapters_to_process"][0])

    @patch("src.Perevod.graph_runner.DatabaseManager")
    @patch("src.Perevod.graph_runner.KnowledgeBaseManager")
    @patch("src.Perevod.graph_runner.LLMProvider")
    @patch("src.Perevod.graph_runner.build_graph")
    def test_retry_failed_uses_sqlite_checkpoint_judge_blockers(
        self,
        mock_build_graph,
        mock_llm_provider,
        mock_kb_manager,
        mock_db_manager,
    ):
        captured_state = {}

        def invoke(state):
            captured_state["chapters_to_process"] = state["chapters_to_process"]
            return {
                "processed_chapters": state["chapters_to_process"],
                "judge_results": [
                    {
                        "title": "chapter1",
                        "pass_check": False,
                        "blocking_issues": ["Missing canonical term"],
                    }
                ],
                "blocking_issues": ["Missing canonical term"],
                "error": None,
            }

        mock_app = MagicMock()
        mock_app.invoke.side_effect = invoke
        mock_build_graph.return_value = mock_app

        db_manager = mock_db_manager.return_value
        db_manager.get_chapter_runs.return_value = {
            "chapter1": {
                "title": "chapter1",
                "input_path": "input/chapter1.txt",
                "output_path": "output/chapter1.txt",
                "status": "judge_done",
                "stages": {
                    "discovered": "done",
                    "translation_done": "done",
                    "output_written": "done",
                    "judge_done": "done",
                },
                "judge_result": {
                    "pass_check": False,
                    "blocking_issues": ["Missing canonical term"],
                    "severity": "high",
                    "score": 4,
                },
                "error": None,
            }
        }

        mock_kb = MagicMock()
        mock_kb.collection.count.return_value = 0
        mock_kb_manager.return_value = mock_kb

        project_name = "test_project_retry_sqlite_judge_blockers"
        input_dir = os.path.join(self.test_temp_dir, project_name, "input")
        output_dir = os.path.join(self.test_temp_dir, project_name, "output")
        os.makedirs(input_dir, exist_ok=True)
        os.makedirs(output_dir, exist_ok=True)
        with open(os.path.join(input_dir, "chapter1.txt"), "w", encoding="utf-8") as f:
            f.write("chapter1 source")
        with open(os.path.join(output_dir, "chapter1.txt"), "w", encoding="utf-8") as f:
            f.write("usable translated text")

        with self.assertRaisesRegex(RuntimeError, "QA failed"):
            run_translation_workflow(
                project_name,
                {
                    "input_dir": input_dir,
                    "output_dir": output_dir,
                    "GOOGLE_API_KEY": "AIza-real-looking-key",
                    "retry_failed": True,
                },
            )

        self.assertEqual(
            [chapter["title"] for chapter in captured_state["chapters_to_process"]],
            ["chapter1"],
        )
        self.assertTrue(
            captured_state["chapters_to_process"][0]["reuse_existing_translation"]
        )
        self.assertEqual(
            captured_state["chapters_to_process"][0]["blocking_issues"],
            ["Missing canonical term"],
        )
        self.assertNotIn("force_rejudge", captured_state["chapters_to_process"][0])

    @patch("src.Perevod.graph_runner.DatabaseManager")
    @patch("src.Perevod.graph_runner.KnowledgeBaseManager")
    @patch("src.Perevod.graph_runner.LLMProvider")
    @patch("src.Perevod.graph_runner.build_graph")
    def test_retry_failed_rejudges_after_refine_checkpoint_with_stale_blockers(
        self,
        mock_build_graph,
        mock_llm_provider,
        mock_kb_manager,
        mock_db_manager,
    ):
        captured_state = {}

        def invoke(state):
            captured_state["chapters_to_process"] = state["chapters_to_process"]
            return {
                "processed_chapters": [
                    {
                        **state["chapters_to_process"][0],
                        "blocking_issues": [],
                    }
                ],
                "judge_results": [{"title": "chapter1", "pass_check": True}],
                "blocking_issues": [],
                "chapter_summaries": [{"title": "chapter1", "summary": "done"}],
                "summary_errors": [],
                "error": None,
            }

        mock_app = MagicMock()
        mock_app.invoke.side_effect = invoke
        mock_build_graph.return_value = mock_app

        db_manager = mock_db_manager.return_value
        db_manager.get_chapter_runs.return_value = {
            "chapter1": {
                "title": "chapter1",
                "input_path": "input/chapter1.txt",
                "output_path": "output/chapter1.txt",
                "status": "refine_done",
                "stages": {
                    "discovered": "done",
                    "translation_done": "done",
                    "output_written": "done",
                    "judge_done": "done",
                    "refine_done": "done",
                },
                "judge_result": {
                    "pass_check": False,
                    "blocking_issues": ["Missing canonical term"],
                    "severity": "high",
                    "score": 4,
                },
                "refine_result": {
                    "refined": True,
                    "refinement_count": 1,
                    "issues_fixed": ["Missing canonical term"],
                },
                "error": None,
            }
        }

        mock_kb = MagicMock()
        mock_kb.collection.count.return_value = 0
        mock_kb_manager.return_value = mock_kb

        project_name = "test_project_retry_sqlite_refine_then_rejudge"
        input_dir = os.path.join(self.test_temp_dir, project_name, "input")
        output_dir = os.path.join(self.test_temp_dir, project_name, "output")
        os.makedirs(input_dir, exist_ok=True)
        os.makedirs(output_dir, exist_ok=True)
        with open(os.path.join(input_dir, "chapter1.txt"), "w", encoding="utf-8") as f:
            f.write("chapter1 source")
        with open(os.path.join(output_dir, "chapter1.txt"), "w", encoding="utf-8") as f:
            f.write("refined translated text")

        run_translation_workflow(
            project_name,
            {
                "input_dir": input_dir,
                "output_dir": output_dir,
                "GOOGLE_API_KEY": "AIza-real-looking-key",
                "retry_failed": True,
            },
        )

        self.assertEqual(
            [chapter["title"] for chapter in captured_state["chapters_to_process"]],
            ["chapter1"],
        )
        self.assertTrue(
            captured_state["chapters_to_process"][0]["reuse_existing_translation"]
        )
        self.assertTrue(captured_state["chapters_to_process"][0]["force_rejudge"])

    @patch("src.Perevod.graph_runner.DatabaseManager")
    @patch("src.Perevod.graph_runner.KnowledgeBaseManager")
    @patch("src.Perevod.graph_runner.LLMProvider")
    @patch("src.Perevod.graph_runner.build_graph")
    def test_retry_incomplete_sqlite_ignores_skipped_chapter_runs(
        self,
        mock_build_graph,
        mock_llm_provider,
        mock_kb_manager,
        mock_db_manager,
    ):
        captured_state = {}

        def invoke(state):
            captured_state["chapters_to_process"] = state["chapters_to_process"]
            return {
                "processed_chapters": state["chapters_to_process"],
                "judge_results": [{"title": "chapter2", "pass_check": True}],
                "chapter_summaries": [{"title": "chapter2"}],
                "summary_errors": [],
                "error": None,
            }

        mock_app = MagicMock()
        mock_app.invoke.side_effect = invoke
        mock_build_graph.return_value = mock_app

        db_manager = mock_db_manager.return_value
        db_manager.get_chapter_runs.return_value = {
            "chapter1": {
                "title": "chapter1",
                "input_path": "input/chapter1.txt",
                "output_path": "output/chapter1.txt",
                "status": "skipped_not_failed",
                "stages": {
                    "context_retrieved": "skipped",
                    "analysis_done": "skipped",
                    "glossary_updated": "skipped",
                    "translation_done": "skipped",
                    "output_written": "skipped",
                    "judge_done": "skipped",
                    "summary_done": "skipped",
                    "memory_updated": "skipped",
                },
            },
            "chapter2": {
                "title": "chapter2",
                "input_path": "input/chapter2.txt",
                "output_path": "output/chapter2.txt",
                "status": "translated",
                "stages": {
                    "context_retrieved": "done",
                    "analysis_done": "done",
                    "glossary_updated": "done",
                    "translation_done": "done",
                    "output_written": "done",
                    "judge_done": "done",
                    "summary_done": "not_started",
                    "memory_updated": "not_started",
                },
            },
        }

        mock_kb = MagicMock()
        mock_kb.collection.count.return_value = 0
        mock_kb_manager.return_value = mock_kb

        project_name = "test_project_retry_incomplete_sqlite_skip_status"
        input_dir = os.path.join(self.test_temp_dir, project_name, "input")
        output_dir = os.path.join(self.test_temp_dir, project_name, "output")
        os.makedirs(input_dir, exist_ok=True)
        os.makedirs(output_dir, exist_ok=True)
        for chapter_name in ("chapter1", "chapter2"):
            with open(os.path.join(input_dir, f"{chapter_name}.txt"), "w", encoding="utf-8") as f:
                f.write(f"{chapter_name} source")
            with open(os.path.join(output_dir, f"{chapter_name}.txt"), "w", encoding="utf-8") as f:
                f.write(f"{chapter_name} translated")

        run_translation_workflow(
            project_name,
            {
                "input_dir": input_dir,
                "output_dir": output_dir,
                "GOOGLE_API_KEY": "AIza-real-looking-key",
                "retry_incomplete": True,
            },
        )

        self.assertEqual(
            [chapter["title"] for chapter in captured_state["chapters_to_process"]],
            ["chapter2"],
        )
        self.assertTrue(
            captured_state["chapters_to_process"][0]["reuse_existing_translation"]
        )
        self.assertNotIn(
            "backup_existing_output",
            captured_state["chapters_to_process"][0],
        )

    @patch("src.Perevod.graph_runner.DatabaseManager")
    @patch("src.Perevod.graph_runner.KnowledgeBaseManager")
    @patch("src.Perevod.graph_runner.LLMProvider")
    @patch("src.Perevod.graph_runner.build_graph")
    def test_retry_incomplete_sqlite_ignores_missing_new_stage_keys_in_hybrid_runs(
        self,
        mock_build_graph,
        mock_llm_provider,
        mock_kb_manager,
        mock_db_manager,
    ):
        captured_state = {}

        def invoke(state):
            captured_state["chapters_to_process"] = state["chapters_to_process"]
            return {
                "processed_chapters": state["chapters_to_process"],
                "judge_results": [{"title": "chapter2", "pass_check": True}],
                "chapter_summaries": [{"title": "chapter2"}],
                "summary_errors": [],
                "error": None,
            }

        mock_app = MagicMock()
        mock_app.invoke.side_effect = invoke
        mock_build_graph.return_value = mock_app

        db_manager = mock_db_manager.return_value
        db_manager.get_chapter_runs.return_value = {
            "chapter1": {
                "title": "chapter1",
                "input_path": "input/chapter1.txt",
                "output_path": "output/chapter1.txt",
                "status": "translated",
                "stages": {
                    "translation_done": "done",
                    "judge_done": "done",
                    "summary_done": "done",
                },
            },
            "chapter2": {
                "title": "chapter2",
                "input_path": "input/chapter2.txt",
                "output_path": "output/chapter2.txt",
                "status": "translated",
                "stages": {
                    "translation_done": "done",
                    "judge_done": "done",
                    "summary_done": "not_started",
                },
            },
        }

        mock_kb = MagicMock()
        mock_kb.collection.count.return_value = 0
        mock_kb_manager.return_value = mock_kb

        project_name = "test_project_retry_incomplete_sqlite_hybrid_schema"
        input_dir = os.path.join(self.test_temp_dir, project_name, "input")
        output_dir = os.path.join(self.test_temp_dir, project_name, "output")
        os.makedirs(input_dir, exist_ok=True)
        os.makedirs(output_dir, exist_ok=True)
        for chapter_name in ("chapter1", "chapter2"):
            with open(os.path.join(input_dir, f"{chapter_name}.txt"), "w", encoding="utf-8") as f:
                f.write(f"{chapter_name} source")
            with open(os.path.join(output_dir, f"{chapter_name}.txt"), "w", encoding="utf-8") as f:
                f.write(f"{chapter_name} translated")

        run_translation_workflow(
            project_name,
            {
                "input_dir": input_dir,
                "output_dir": output_dir,
                "GOOGLE_API_KEY": "AIza-real-looking-key",
                "retry_incomplete": True,
            },
        )

        self.assertEqual(
            [chapter["title"] for chapter in captured_state["chapters_to_process"]],
            ["chapter2"],
        )
        self.assertTrue(
            captured_state["chapters_to_process"][0]["reuse_existing_translation"]
        )

    def test_retry_failed_rejects_invalid_report_and_releases_lock(self):
        project_name = "test_retry_invalid_report"
        input_dir = os.path.join(self.test_temp_dir, project_name, "input")
        output_dir = os.path.join(self.test_temp_dir, project_name, "output")
        os.makedirs(input_dir, exist_ok=True)
        os.makedirs(output_dir, exist_ok=True)
        with open(os.path.join(input_dir, "chapter1.txt"), "w", encoding="utf-8") as f:
            f.write("chapter1 source")
        with open(
            os.path.join(output_dir, "translation_report.json"),
            "w",
            encoding="utf-8",
        ) as report_file:
            report_file.write("{invalid")

        with self.assertRaises(ValueError):
            run_translation_workflow(
                project_name,
                {
                    "input_dir": input_dir,
                    "output_dir": output_dir,
                    "GOOGLE_API_KEY": "AIza-real-looking-key",
                    "retry_failed": True,
                },
            )

        self.assertFalse(os.path.exists(os.path.join(output_dir, ".translation.lock")))

    def test_retry_failed_rejects_non_object_report_and_releases_lock(self):
        project_name = "test_retry_non_object_report"
        input_dir = os.path.join(self.test_temp_dir, project_name, "input")
        output_dir = os.path.join(self.test_temp_dir, project_name, "output")
        os.makedirs(input_dir, exist_ok=True)
        os.makedirs(output_dir, exist_ok=True)
        with open(os.path.join(input_dir, "chapter1.txt"), "w", encoding="utf-8") as f:
            f.write("chapter1 source")
        with open(
            os.path.join(output_dir, "translation_report.json"),
            "w",
            encoding="utf-8",
        ) as report_file:
            json.dump([], report_file)

        with self.assertRaisesRegex(ValueError, "report schema is invalid"):
            run_translation_workflow(
                project_name,
                {
                    "input_dir": input_dir,
                    "output_dir": output_dir,
                    "GOOGLE_API_KEY": "AIza-real-looking-key",
                    "retry_failed": True,
                },
            )

        self.assertFalse(os.path.exists(os.path.join(output_dir, ".translation.lock")))

    def test_retry_failed_rejects_non_object_report_chapters_and_releases_lock(self):
        project_name = "test_retry_bad_chapter_report"
        input_dir = os.path.join(self.test_temp_dir, project_name, "input")
        output_dir = os.path.join(self.test_temp_dir, project_name, "output")
        os.makedirs(input_dir, exist_ok=True)
        os.makedirs(output_dir, exist_ok=True)
        with open(os.path.join(input_dir, "chapter1.txt"), "w", encoding="utf-8") as f:
            f.write("chapter1 source")
        with open(
            os.path.join(output_dir, "translation_report.json"),
            "w",
            encoding="utf-8",
        ) as report_file:
            json.dump({"chapters": ["chapter1"]}, report_file)

        with self.assertRaisesRegex(ValueError, "report chapter schema is invalid"):
            run_translation_workflow(
                project_name,
                {
                    "input_dir": input_dir,
                    "output_dir": output_dir,
                    "GOOGLE_API_KEY": "AIza-real-looking-key",
                    "retry_failed": True,
                },
            )

        self.assertFalse(os.path.exists(os.path.join(output_dir, ".translation.lock")))

    def test_retry_failed_rejects_report_chapters_missing_from_input_dir(self):
        project_name = "test_retry_missing_input_file"
        input_dir = os.path.join(self.test_temp_dir, project_name, "input")
        output_dir = os.path.join(self.test_temp_dir, project_name, "output")
        os.makedirs(input_dir, exist_ok=True)
        os.makedirs(output_dir, exist_ok=True)
        with open(
            os.path.join(output_dir, "translation_report.json"),
            "w",
            encoding="utf-8",
        ) as report_file:
            json.dump(
                {
                    "chapters": [
                        {
                            "title": "chapter1",
                            "status": "failed",
                            "error": "Ошибка перевода главы 'chapter1': quota exhausted",
                        }
                    ]
                },
                report_file,
            )

        with self.assertRaisesRegex(FileNotFoundError, "chapter1"):
            run_translation_workflow(
                project_name,
                {
                    "input_dir": input_dir,
                    "output_dir": output_dir,
                    "GOOGLE_API_KEY": "AIza-real-looking-key",
                    "retry_failed": True,
                },
            )

        self.assertFalse(os.path.exists(os.path.join(output_dir, ".translation.lock")))

    @patch("src.Perevod.graph_runner.DatabaseManager")
    @patch("src.Perevod.graph_runner.KnowledgeBaseManager")
    @patch("src.Perevod.graph_runner.LLMProvider")
    def test_full_graph_execution(
        self, mock_llm_provider, mock_kb_manager, mock_db_manager
    ):
        """
        Тестирует полный прогон графа с моками зависимостей для упрощенной архитектуры.
        """
        # 1. Настройка моков
        mock_db_manager.return_value.get_from_cache.return_value = None
        mock_kb = MagicMock()
        mock_kb.collection.count.return_value = 0
        mock_kb.collection.get.return_value = {"documents": [], "metadatas": []}
        mock_kb_manager.return_value = mock_kb

        # Мокируем LLMProvider и его метод get_model
        mock_llm_instance = MagicMock()

        class MockResponse:
            def __init__(self, text_content):
                self.text = text_content

        # [ИСПРАВЛЕНИЕ] Задаем последовательность ответов для графа:
        # 1. Ответ для analysis_node (находит 1 термин)
        # 2. Ответ для translation_node (переводит главу)
        # 3. Ответ для judge_node (подтверждает качество)
        # 4. Ответ для summarization_node (сохраняет память главы)
        mock_llm_instance.generate_content.side_effect = [
            MockResponse('{"found_terms": [{"english_term": "test", "russian_translation": "тест", "category": "concept", "description": "a test"}]}'),
            MockResponse("Переведенный текст с термином тест."),
            MockResponse('{"pass_check": true, "severity": "low", "blocking_issues": [], "suggestions": [], "score": 9}'),
            MockResponse('{"title": "chapter1", "summary": "Test chapter translated.", "key_events": ["test"], "active_characters": []}'),
        ]

        mock_llm_provider.return_value.get_model.return_value = mock_llm_instance

        # 2. Подготовка тестовых данных и настроек
        project_name = "test_project_runner"
        input_dir = os.path.join(self.test_temp_dir, project_name, "input")
        output_dir = os.path.join(self.test_temp_dir, project_name, "output")
        os.makedirs(input_dir, exist_ok=True)
        os.makedirs(output_dir, exist_ok=True)

        with open(os.path.join(input_dir, "chapter1.txt"), "w", encoding="utf-8") as f:
            f.write("This is a test chapter.")

        project_settings = {
            "input_dir": input_dir,
            "output_dir": output_dir,
            "GOOGLE_API_KEY": "AIza-real-looking-key",
        }

        # 3. Запуск графа
        final_state = run_translation_workflow(project_name, project_settings)

        # 4. Проверка результатов
        self.assertIsNotNone(final_state)
        self.assertIsNone(
            final_state.get("error"),
            f"Граф завершился с ошибкой: {final_state.get('error')}",
        )
        self.assertIn("processed_chapters", final_state)
        self.assertEqual(len(final_state["processed_chapters"]), 1)

        output_file_path = os.path.join(output_dir, "chapter1.txt")
        self.assertTrue(os.path.exists(output_file_path))

        report_path = os.path.join(output_dir, "translation_report.json")
        self.assertTrue(os.path.exists(report_path))
        with open(report_path, encoding="utf-8") as report_file:
            report = json.load(report_file)
        self.assertEqual(report["project_name"], project_name)
        self.assertEqual(report["run"]["input_dir"], input_dir)
        self.assertEqual(report["run"]["output_dir"], output_dir)
        self.assertFalse(report["run"]["retry_failed"])
        self.assertFalse(report["run"]["retry_incomplete"])
        self.assertEqual(
            report["run"]["model_configs"]["translation"],
            "gemma-4-31b-it",
        )
        self.assertEqual(report["run"]["embedding_model"], "gemini-embedding-2")
        self.assertEqual(report["total_chapters"], 1)
        self.assertEqual(report["processed_count"], 1)
        self.assertEqual(report["failed_count"], 0)
        self.assertEqual(report["warning_count"], 0)
        self.assertEqual(report["chapters"][0]["title"], "chapter1")
        self.assertEqual(report["chapters"][0]["status"], "translated")
        self.assertEqual(report["chapters"][0]["translation_source"], "api")
        self.assertEqual(report["chapters"][0]["translation_mode"], "whole_chapter")
        self.assertEqual(report["chapters"][0]["translation_chunk_count"], 1)
        self.assertTrue(report["chapters"][0]["judge_pass_check"])
        self.assertEqual(report["chapters"][0]["judge_score"], 9)
        self.assertEqual(report["chapters"][0]["judge_severity"], "low")
        self.assertEqual(report["chapters"][0]["blocking_issues"], [])
        self.assertEqual(report["chapters"][0]["quality_suggestions"], [])
        self.assertEqual(
            report["chapters"][0]["stages"],
            {
                "context": "done",
                "analysis": "done",
                "glossary": "done",
                "translation": "done",
                "output": "done",
                "judge": "done",
                "refine": "not_needed",
                "summary": "done",
                "memory": "done",
            },
        )
        self.assertEqual(final_state["report_path"], report_path)

    @patch("src.Perevod.graph_runner.DatabaseManager")
    @patch("src.Perevod.graph_runner.KnowledgeBaseManager")
    @patch("src.Perevod.graph_runner.LLMProvider")
    @patch("src.Perevod.graph_runner.build_graph")
    def test_failed_graph_execution_writes_report(
        self,
        mock_build_graph,
        mock_llm_provider,
        mock_kb_manager,
        mock_db_manager,
    ):
        mock_app = MagicMock()
        mock_app.invoke.return_value = {
            "processed_chapters": [{"title": "chapter1"}],
            "error": "Ошибка перевода главы 'chapter2': translation failed",
        }
        mock_build_graph.return_value = mock_app

        mock_kb = MagicMock()
        mock_kb.collection.count.return_value = 0
        mock_kb_manager.return_value = mock_kb

        project_name = "test_project_failed_report"
        input_dir = os.path.join(self.test_temp_dir, project_name, "input")
        output_dir = os.path.join(self.test_temp_dir, project_name, "output")
        os.makedirs(input_dir, exist_ok=True)
        os.makedirs(output_dir, exist_ok=True)

        with open(os.path.join(input_dir, "chapter1.txt"), "w", encoding="utf-8") as f:
            f.write("This is a test chapter.")
        with open(os.path.join(input_dir, "chapter2.txt"), "w", encoding="utf-8") as f:
            f.write("This is another test chapter.")

        project_settings = {
            "input_dir": input_dir,
            "output_dir": output_dir,
            "GOOGLE_API_KEY": "AIza-real-looking-key",
        }

        with self.assertRaises(RuntimeError):
            run_translation_workflow(project_name, project_settings)

        report_path = os.path.join(output_dir, "translation_report.json")
        self.assertTrue(os.path.exists(report_path))
        with open(report_path, encoding="utf-8") as report_file:
            report = json.load(report_file)
        self.assertEqual(report["project_name"], project_name)
        self.assertEqual(report["total_chapters"], 2)
        self.assertEqual(report["processed_count"], 1)
        self.assertEqual(report["failed_count"], 1)
        statuses = {chapter["title"]: chapter["status"] for chapter in report["chapters"]}
        stages = {chapter["title"]: chapter["stages"] for chapter in report["chapters"]}
        expected_stage_keys = {
            "context",
            "analysis",
            "glossary",
            "translation",
            "output",
            "judge",
            "refine",
            "summary",
            "memory",
        }
        self.assertEqual(statuses["chapter1"], "translated")
        self.assertEqual(statuses["chapter2"], "failed")
        self.assertEqual(set(stages["chapter1"]), expected_stage_keys)
        self.assertEqual(set(stages["chapter2"]), expected_stage_keys)
        self.assertEqual(stages["chapter1"]["translation"], "done")
        self.assertEqual(stages["chapter1"]["judge"], "not_started")
        self.assertEqual(stages["chapter2"]["translation"], "failed")
        self.assertIn("translation failed", report["error"])

    @patch("src.Perevod.graph_runner.DatabaseManager")
    @patch("src.Perevod.graph_runner.KnowledgeBaseManager")
    @patch("src.Perevod.graph_runner.LLMProvider")
    @patch("src.Perevod.graph_runner.build_graph")
    def test_workflow_records_discovered_chapter_runs(
        self,
        mock_build_graph,
        mock_llm_provider,
        mock_kb_manager,
        mock_db_manager,
    ):
        captured_state = {}

        def invoke(state):
            captured_state["state"] = state
            return {
                "processed_chapters": state["chapters_to_process"],
                "error": None,
            }

        mock_app = MagicMock()
        mock_app.invoke.side_effect = invoke
        mock_build_graph.return_value = mock_app

        mock_kb = MagicMock()
        mock_kb.collection.count.return_value = 0
        mock_kb_manager.return_value = mock_kb

        project_name = "test_project_checkpoint_discovery"
        input_dir = os.path.join(self.test_temp_dir, project_name, "input")
        output_dir = os.path.join(self.test_temp_dir, project_name, "output")
        os.makedirs(input_dir, exist_ok=True)
        os.makedirs(output_dir, exist_ok=True)

        with open(os.path.join(input_dir, "chapter1.txt"), "w", encoding="utf-8") as f:
            f.write("chapter1 source")
        with open(os.path.join(input_dir, "chapter2.txt"), "w", encoding="utf-8") as f:
            f.write("chapter2 source")

        run_translation_workflow(
            project_name,
            {
                "input_dir": input_dir,
                "output_dir": output_dir,
                "GOOGLE_API_KEY": "AIza-real-looking-key",
            },
        )

        db_manager = captured_state["state"]["app_context"]["db_manager"]
        self.assertEqual(db_manager.upsert_chapter_run.call_count, 2)
        db_manager.upsert_chapter_run.assert_any_call(
            "chapter1",
            input_path=os.path.join(input_dir, "chapter1.txt"),
            output_path=os.path.join(output_dir, "chapter1.txt"),
            status="discovered",
        )
        db_manager.upsert_chapter_run.assert_any_call(
            "chapter2",
            input_path=os.path.join(input_dir, "chapter2.txt"),
            output_path=os.path.join(output_dir, "chapter2.txt"),
            status="discovered",
        )

    @patch("src.Perevod.graph_runner.DatabaseManager")
    @patch("src.Perevod.graph_runner.KnowledgeBaseManager")
    @patch("src.Perevod.graph_runner.LLMProvider")
    @patch("src.Perevod.graph_runner.build_graph")
    def test_workflow_report_refreshes_checkpoint_stages_after_graph(
        self,
        mock_build_graph,
        mock_llm_provider,
        mock_kb_manager,
        mock_db_manager,
    ):
        def invoke(state):
            return {
                "processed_chapters": [
                    {
                        "title": "chapter1",
                        "output_path": os.path.join(output_dir, "chapter1.txt"),
                    }
                ],
                "judge_results": [{"title": "chapter1", "pass_check": True}],
                "chapter_summaries": [{"title": "chapter1"}],
                "error": None,
            }

        mock_app = MagicMock()
        mock_app.invoke.side_effect = invoke
        mock_build_graph.return_value = mock_app

        db_manager = mock_db_manager.return_value
        db_manager.get_chapter_runs.side_effect = [
            {
                "chapter1": {
                    "title": "chapter1",
                    "stages": {"discovered": "done"},
                }
            },
            {
                "chapter1": {
                    "title": "chapter1",
                    "stages": {
                        "context_retrieved": "done",
                        "analysis_done": "done",
                        "glossary_updated": "done",
                        "translation_done": "done",
                        "output_written": "done",
                        "judge_done": "done",
                        "summary_done": "done",
                        "memory_updated": "done",
                    },
                }
            },
        ]

        mock_kb = MagicMock()
        mock_kb.collection.count.return_value = 0
        mock_kb_manager.return_value = mock_kb

        project_name = "test_project_checkpoint_report_refresh"
        input_dir = os.path.join(self.test_temp_dir, project_name, "input")
        output_dir = os.path.join(self.test_temp_dir, project_name, "output")
        os.makedirs(input_dir, exist_ok=True)
        os.makedirs(output_dir, exist_ok=True)

        with open(os.path.join(input_dir, "chapter1.txt"), "w", encoding="utf-8") as f:
            f.write("chapter1 source")

        run_translation_workflow(
            project_name,
            {
                "input_dir": input_dir,
                "output_dir": output_dir,
                "GOOGLE_API_KEY": "AIza-real-looking-key",
            },
        )

        with open(
            os.path.join(output_dir, "translation_report.json"),
            encoding="utf-8",
        ) as report_file:
            report = json.load(report_file)

        self.assertEqual(
            report["chapters"][0]["stages"],
            {
                "context": "done",
                "analysis": "done",
                "glossary": "done",
                "translation": "done",
                "output": "done",
                "judge": "done",
                "refine": "not_needed",
                "summary": "done",
                "memory": "done",
            },
        )

    @patch("src.Perevod.graph_runner.DatabaseManager")
    @patch("src.Perevod.graph_runner.KnowledgeBaseManager")
    @patch("src.Perevod.graph_runner.LLMProvider")
    @patch("src.Perevod.graph_runner.build_graph")
    def test_retry_failed_processes_only_failed_report_chapters(
        self,
        mock_build_graph,
        mock_llm_provider,
        mock_kb_manager,
        mock_db_manager,
    ):
        captured_state = {}

        def invoke(state):
            captured_state["chapters_to_process"] = state["chapters_to_process"]
            return {
                "processed_chapters": state["chapters_to_process"],
                "error": None,
            }

        mock_app = MagicMock()
        mock_app.invoke.side_effect = invoke
        mock_build_graph.return_value = mock_app

        mock_kb = MagicMock()
        mock_kb.collection.count.return_value = 0
        mock_kb_manager.return_value = mock_kb

        project_name = "test_project_retry_failed"
        input_dir = os.path.join(self.test_temp_dir, project_name, "input")
        output_dir = os.path.join(self.test_temp_dir, project_name, "output")
        os.makedirs(input_dir, exist_ok=True)
        os.makedirs(output_dir, exist_ok=True)

        for chapter_name in ("chapter1", "chapter2", "chapter3"):
            with open(
                os.path.join(input_dir, f"{chapter_name}.txt"),
                "w",
                encoding="utf-8",
            ) as f:
                f.write(f"{chapter_name} source")
            with open(
                os.path.join(output_dir, f"{chapter_name}.txt"),
                "w",
                encoding="utf-8",
            ) as f:
                f.write(f"{chapter_name} translated")

        with open(
            os.path.join(output_dir, "translation_report.json"),
            "w",
            encoding="utf-8",
        ) as report_file:
            json.dump(
                {
                    "chapters": [
                        {"title": "chapter1", "status": "translated"},
                        {
                            "title": "chapter2",
                            "status": "failed",
                            "error": "Ошибка Судьи для главы 'chapter2': invalid response",
                            "blocking_issues": ["Missing canonical term"],
                        },
                        {"title": "chapter3", "status": "skipped_existing"},
                    ]
                },
                report_file,
            )

        project_settings = {
            "input_dir": input_dir,
            "output_dir": output_dir,
            "GOOGLE_API_KEY": "AIza-real-looking-key",
            "retry_failed": True,
        }

        run_translation_workflow(project_name, project_settings)

        self.assertEqual(
            [chapter["title"] for chapter in captured_state["chapters_to_process"]],
            ["chapter2"],
        )
        self.assertTrue(
            captured_state["chapters_to_process"][0]["reuse_existing_translation"]
        )

        with open(
            os.path.join(output_dir, "translation_report.json"),
            encoding="utf-8",
        ) as report_file:
            report = json.load(report_file)
        statuses = {chapter["title"]: chapter["status"] for chapter in report["chapters"]}
        reused = {
            chapter["title"]: chapter["reused_existing_translation"]
            for chapter in report["chapters"]
        }
        self.assertEqual(statuses["chapter1"], "skipped_not_failed")
        self.assertEqual(statuses["chapter2"], "qa_failed")
        self.assertEqual(statuses["chapter3"], "skipped_not_failed")
        self.assertEqual(report["chapters"][0]["stages"]["translation"], "skipped")
        self.assertFalse(reused["chapter1"])
        self.assertTrue(reused["chapter2"])
        self.assertFalse(reused["chapter3"])
        self.assertEqual(
            report["chapters"][1]["blocking_issues"],
            ["Missing canonical term"],
        )

    @patch("src.Perevod.graph_runner.DatabaseManager")
    @patch("src.Perevod.graph_runner.KnowledgeBaseManager")
    @patch("src.Perevod.graph_runner.LLMProvider")
    @patch("src.Perevod.graph_runner.build_graph")
    def test_retry_failed_processes_report_chapters_with_blocking_issues(
        self,
        mock_build_graph,
        mock_llm_provider,
        mock_kb_manager,
        mock_db_manager,
    ):
        captured_state = {}

        def invoke(state):
            captured_state["chapters_to_process"] = state["chapters_to_process"]
            return {
                "processed_chapters": [
                    {
                        **state["chapters_to_process"][0],
                        "blocking_issues": ["Missing canonical term"],
                    }
                ],
                "judge_results": [
                    {
                        "title": "chapter1",
                        "pass_check": False,
                        "blocking_issues": ["Missing canonical term"],
                    }
                ],
                "blocking_issues": ["Missing canonical term"],
                "error": None,
            }

        mock_app = MagicMock()
        mock_app.invoke.side_effect = invoke
        mock_build_graph.return_value = mock_app

        mock_kb = MagicMock()
        mock_kb.collection.count.return_value = 0
        mock_kb_manager.return_value = mock_kb

        project_name = "test_project_retry_report_blocking_issues"
        input_dir = os.path.join(self.test_temp_dir, project_name, "input")
        output_dir = os.path.join(self.test_temp_dir, project_name, "output")
        os.makedirs(input_dir, exist_ok=True)
        os.makedirs(output_dir, exist_ok=True)

        with open(os.path.join(input_dir, "chapter1.txt"), "w", encoding="utf-8") as f:
            f.write("chapter1 source")
        with open(os.path.join(output_dir, "chapter1.txt"), "w", encoding="utf-8") as f:
            f.write("chapter1 translated")
        with open(
            os.path.join(output_dir, "translation_report.json"),
            "w",
            encoding="utf-8",
        ) as report_file:
            json.dump(
                {
                    "chapters": [
                        {
                            "title": "chapter1",
                            "status": "translated",
                            "stages": {"translation": "done", "judge": "done"},
                            "blocking_issues": ["Missing canonical term"],
                        }
                    ]
                },
                report_file,
            )

        with self.assertRaisesRegex(RuntimeError, "QA failed"):
            run_translation_workflow(
                project_name,
                {
                    "input_dir": input_dir,
                    "output_dir": output_dir,
                    "GOOGLE_API_KEY": "AIza-real-looking-key",
                    "retry_failed": True,
                },
            )

        self.assertEqual(
            [chapter["title"] for chapter in captured_state["chapters_to_process"]],
            ["chapter1"],
        )
        self.assertTrue(
            captured_state["chapters_to_process"][0]["reuse_existing_translation"]
        )
        self.assertEqual(
            captured_state["chapters_to_process"][0]["blocking_issues"],
            ["Missing canonical term"],
        )

    @patch("src.Perevod.graph_runner.DatabaseManager")
    @patch("src.Perevod.graph_runner.KnowledgeBaseManager")
    @patch("src.Perevod.graph_runner.LLMProvider")
    @patch("src.Perevod.graph_runner.build_graph")
    def test_retry_failed_processes_report_chapters_with_failed_judge_without_blockers(
        self,
        mock_build_graph,
        mock_llm_provider,
        mock_kb_manager,
        mock_db_manager,
    ):
        captured_state = {}

        def invoke(state):
            captured_state["chapters_to_process"] = state["chapters_to_process"]
            return {
                "processed_chapters": state["chapters_to_process"],
                "judge_results": [
                    {"title": "chapter1", "pass_check": True, "blocking_issues": []}
                ],
                "blocking_issues": [],
                "error": None,
            }

        mock_app = MagicMock()
        mock_app.invoke.side_effect = invoke
        mock_build_graph.return_value = mock_app

        mock_kb = MagicMock()
        mock_kb.collection.count.return_value = 0
        mock_kb_manager.return_value = mock_kb

        project_name = "test_project_retry_report_failed_judge_no_blockers"
        input_dir = os.path.join(self.test_temp_dir, project_name, "input")
        output_dir = os.path.join(self.test_temp_dir, project_name, "output")
        os.makedirs(input_dir, exist_ok=True)
        os.makedirs(output_dir, exist_ok=True)

        with open(os.path.join(input_dir, "chapter1.txt"), "w", encoding="utf-8") as f:
            f.write("chapter1 source")
        with open(os.path.join(output_dir, "chapter1.txt"), "w", encoding="utf-8") as f:
            f.write("chapter1 translated")
        with open(
            os.path.join(output_dir, "translation_report.json"),
            "w",
            encoding="utf-8",
        ) as report_file:
            json.dump(
                {
                    "chapters": [
                        {
                            "title": "chapter1",
                            "status": "translated",
                            "stages": {"translation": "done", "judge": "done"},
                            "judge_pass_check": False,
                            "blocking_issues": [],
                        }
                    ]
                },
                report_file,
            )

        run_translation_workflow(
            project_name,
            {
                "input_dir": input_dir,
                "output_dir": output_dir,
                "GOOGLE_API_KEY": "AIza-real-looking-key",
                "retry_failed": True,
            },
        )

        self.assertEqual(
            [chapter["title"] for chapter in captured_state["chapters_to_process"]],
            ["chapter1"],
        )
        self.assertTrue(
            captured_state["chapters_to_process"][0]["reuse_existing_translation"]
        )

    @patch("src.Perevod.graph_runner.DatabaseManager")
    @patch("src.Perevod.graph_runner.KnowledgeBaseManager")
    @patch("src.Perevod.graph_runner.LLMProvider")
    @patch("src.Perevod.graph_runner.build_graph")
    def test_retry_failed_does_not_reuse_output_after_translation_error(
        self,
        mock_build_graph,
        mock_llm_provider,
        mock_kb_manager,
        mock_db_manager,
    ):
        captured_state = {}

        def invoke(state):
            captured_state["chapters_to_process"] = state["chapters_to_process"]
            return {
                "processed_chapters": state["chapters_to_process"],
                "error": None,
            }

        mock_app = MagicMock()
        mock_app.invoke.side_effect = invoke
        mock_build_graph.return_value = mock_app

        mock_kb = MagicMock()
        mock_kb.collection.count.return_value = 0
        mock_kb_manager.return_value = mock_kb

        project_name = "test_project_retry_failed_translation_error"
        input_dir = os.path.join(self.test_temp_dir, project_name, "input")
        output_dir = os.path.join(self.test_temp_dir, project_name, "output")
        os.makedirs(input_dir, exist_ok=True)
        os.makedirs(output_dir, exist_ok=True)

        with open(os.path.join(input_dir, "chapter1.txt"), "w", encoding="utf-8") as f:
            f.write("chapter1 source")
        with open(os.path.join(output_dir, "chapter1.txt"), "w", encoding="utf-8") as f:
            f.write("possibly partial translation")

        with open(
            os.path.join(output_dir, "translation_report.json"),
            "w",
            encoding="utf-8",
        ) as report_file:
            json.dump(
                {
                    "chapters": [
                        {
                            "title": "chapter1",
                            "status": "failed",
                            "error": "Ошибка перевода главы 'chapter1': quota exhausted",
                        }
                    ]
                },
                report_file,
            )

        project_settings = {
            "input_dir": input_dir,
            "output_dir": output_dir,
            "GOOGLE_API_KEY": "AIza-real-looking-key",
            "retry_failed": True,
        }

        run_translation_workflow(project_name, project_settings)

        self.assertNotIn(
            "reuse_existing_translation",
            captured_state["chapters_to_process"][0],
        )

    @patch("src.Perevod.graph_runner.DatabaseManager")
    @patch("src.Perevod.graph_runner.KnowledgeBaseManager")
    @patch("src.Perevod.graph_runner.LLMProvider")
    @patch("src.Perevod.graph_runner.build_graph")
    def test_retry_failed_does_not_reuse_output_when_report_marks_translation_failed(
        self,
        mock_build_graph,
        mock_llm_provider,
        mock_kb_manager,
        mock_db_manager,
    ):
        captured_state = {}

        def invoke(state):
            captured_state["chapters_to_process"] = state["chapters_to_process"]
            return {
                "processed_chapters": state["chapters_to_process"],
                "error": None,
            }

        mock_app = MagicMock()
        mock_app.invoke.side_effect = invoke
        mock_build_graph.return_value = mock_app

        mock_kb = MagicMock()
        mock_kb.collection.count.return_value = 0
        mock_kb_manager.return_value = mock_kb

        project_name = "test_project_retry_failed_translation_stage"
        input_dir = os.path.join(self.test_temp_dir, project_name, "input")
        output_dir = os.path.join(self.test_temp_dir, project_name, "output")
        os.makedirs(input_dir, exist_ok=True)
        os.makedirs(output_dir, exist_ok=True)

        with open(os.path.join(input_dir, "chapter1.txt"), "w", encoding="utf-8") as f:
            f.write("chapter1 source")
        with open(os.path.join(output_dir, "chapter1.txt"), "w", encoding="utf-8") as f:
            f.write("stale or partial translation")

        with open(
            os.path.join(output_dir, "translation_report.json"),
            "w",
            encoding="utf-8",
        ) as report_file:
            json.dump(
                {
                    "chapters": [
                        {
                            "title": "chapter1",
                            "status": "failed",
                            "stages": {
                                "translation": "failed",
                                "judge": "not_started",
                                "summary": "not_started",
                            },
                            "error": "Gemini API returned an empty response",
                        }
                    ]
                },
                report_file,
            )

        project_settings = {
            "input_dir": input_dir,
            "output_dir": output_dir,
            "GOOGLE_API_KEY": "AIza-real-looking-key",
            "retry_failed": True,
        }

        run_translation_workflow(project_name, project_settings)

        self.assertNotIn(
            "reuse_existing_translation",
            captured_state["chapters_to_process"][0],
        )

    @patch("src.Perevod.graph_runner.DatabaseManager")
    @patch("src.Perevod.graph_runner.KnowledgeBaseManager")
    @patch("src.Perevod.graph_runner.LLMProvider")
    @patch("src.Perevod.graph_runner.build_graph")
    def test_retry_failed_does_not_reuse_output_when_report_marks_output_failed(
        self,
        mock_build_graph,
        mock_llm_provider,
        mock_kb_manager,
        mock_db_manager,
    ):
        captured_state = {}

        def invoke(state):
            captured_state["chapters_to_process"] = state["chapters_to_process"]
            return {
                "processed_chapters": state["chapters_to_process"],
                "error": None,
            }

        mock_app = MagicMock()
        mock_app.invoke.side_effect = invoke
        mock_build_graph.return_value = mock_app

        mock_kb = MagicMock()
        mock_kb.collection.count.return_value = 0
        mock_kb_manager.return_value = mock_kb

        project_name = "test_project_retry_failed_output_stage"
        input_dir = os.path.join(self.test_temp_dir, project_name, "input")
        output_dir = os.path.join(self.test_temp_dir, project_name, "output")
        os.makedirs(input_dir, exist_ok=True)
        os.makedirs(output_dir, exist_ok=True)

        with open(os.path.join(input_dir, "chapter1.txt"), "w", encoding="utf-8") as f:
            f.write("chapter1 source")
        with open(os.path.join(output_dir, "chapter1.txt"), "w", encoding="utf-8") as f:
            f.write("stale or partial translation")

        with open(
            os.path.join(output_dir, "translation_report.json"),
            "w",
            encoding="utf-8",
        ) as report_file:
            json.dump(
                {
                    "chapters": [
                        {
                            "title": "chapter1",
                            "status": "failed",
                            "stages": {
                                "translation": "done",
                                "output": "failed",
                                "judge": "not_started",
                                "summary": "not_started",
                            },
                            "error": "Failed stage: output",
                        }
                    ]
                },
                report_file,
            )

        project_settings = {
            "input_dir": input_dir,
            "output_dir": output_dir,
            "GOOGLE_API_KEY": "AIza-real-looking-key",
            "retry_failed": True,
        }

        run_translation_workflow(project_name, project_settings)

        self.assertNotIn(
            "reuse_existing_translation",
            captured_state["chapters_to_process"][0],
        )

    @patch("src.Perevod.graph_runner.DatabaseManager")
    @patch("src.Perevod.graph_runner.KnowledgeBaseManager")
    @patch("src.Perevod.graph_runner.LLMProvider")
    @patch("src.Perevod.graph_runner.build_graph")
    def test_retry_failed_reuses_output_after_editor_error(
        self,
        mock_build_graph,
        mock_llm_provider,
        mock_kb_manager,
        mock_db_manager,
    ):
        captured_state = {}

        def invoke(state):
            captured_state["chapters_to_process"] = state["chapters_to_process"]
            return {
                "processed_chapters": state["chapters_to_process"],
                "error": None,
            }

        mock_app = MagicMock()
        mock_app.invoke.side_effect = invoke
        mock_build_graph.return_value = mock_app

        mock_kb = MagicMock()
        mock_kb.collection.count.return_value = 0
        mock_kb_manager.return_value = mock_kb

        project_name = "test_project_retry_failed_editor_error"
        input_dir = os.path.join(self.test_temp_dir, project_name, "input")
        output_dir = os.path.join(self.test_temp_dir, project_name, "output")
        os.makedirs(input_dir, exist_ok=True)
        os.makedirs(output_dir, exist_ok=True)

        with open(os.path.join(input_dir, "chapter1.txt"), "w", encoding="utf-8") as f:
            f.write("chapter1 source")
        with open(os.path.join(output_dir, "chapter1.txt"), "w", encoding="utf-8") as f:
            f.write("usable translated text")

        with open(
            os.path.join(output_dir, "translation_report.json"),
            "w",
            encoding="utf-8",
        ) as report_file:
            json.dump(
                {
                    "chapters": [
                        {
                            "title": "chapter1",
                            "status": "failed",
                            "error": "Ошибка Редактора для главы 'chapter1': empty correction",
                        }
                    ]
                },
                report_file,
            )

        project_settings = {
            "input_dir": input_dir,
            "output_dir": output_dir,
            "GOOGLE_API_KEY": "AIza-real-looking-key",
            "retry_failed": True,
        }

        run_translation_workflow(project_name, project_settings)

        self.assertTrue(
            captured_state["chapters_to_process"][0]["reuse_existing_translation"]
        )

    @patch("src.Perevod.graph_runner.DatabaseManager")
    @patch("src.Perevod.graph_runner.KnowledgeBaseManager")
    @patch("src.Perevod.graph_runner.LLMProvider")
    @patch("src.Perevod.graph_runner.build_graph")
    def test_retry_failed_reuses_output_after_analysis_error(
        self,
        mock_build_graph,
        mock_llm_provider,
        mock_kb_manager,
        mock_db_manager,
    ):
        captured_state = {}

        def invoke(state):
            captured_state["chapters_to_process"] = state["chapters_to_process"]
            return {
                "processed_chapters": state["chapters_to_process"],
                "error": None,
            }

        mock_app = MagicMock()
        mock_app.invoke.side_effect = invoke
        mock_build_graph.return_value = mock_app

        mock_kb = MagicMock()
        mock_kb.collection.count.return_value = 0
        mock_kb_manager.return_value = mock_kb

        project_name = "test_project_retry_failed_analysis_error"
        input_dir = os.path.join(self.test_temp_dir, project_name, "input")
        output_dir = os.path.join(self.test_temp_dir, project_name, "output")
        os.makedirs(input_dir, exist_ok=True)
        os.makedirs(output_dir, exist_ok=True)

        with open(os.path.join(input_dir, "chapter1.txt"), "w", encoding="utf-8") as f:
            f.write("chapter1 source")
        with open(os.path.join(output_dir, "chapter1.txt"), "w", encoding="utf-8") as f:
            f.write("usable translated text")

        with open(
            os.path.join(output_dir, "translation_report.json"),
            "w",
            encoding="utf-8",
        ) as report_file:
            json.dump(
                {
                    "chapters": [
                        {
                            "title": "chapter1",
                            "status": "failed",
                            "error": "Analysis failed for chapter 'chapter1': quota exhausted",
                        }
                    ]
                },
                report_file,
            )

        project_settings = {
            "input_dir": input_dir,
            "output_dir": output_dir,
            "GOOGLE_API_KEY": "AIza-real-looking-key",
            "retry_failed": True,
        }

        run_translation_workflow(project_name, project_settings)

        self.assertTrue(
            captured_state["chapters_to_process"][0]["reuse_existing_translation"]
        )

    @patch("src.Perevod.graph_runner.DatabaseManager")
    @patch("src.Perevod.graph_runner.KnowledgeBaseManager")
    @patch("src.Perevod.graph_runner.LLMProvider")
    @patch("src.Perevod.graph_runner.build_graph")
    def test_retry_failed_reuses_output_after_global_context_error(
        self,
        mock_build_graph,
        mock_llm_provider,
        mock_kb_manager,
        mock_db_manager,
    ):
        captured_state = {}

        def invoke(state):
            captured_state["chapters_to_process"] = state["chapters_to_process"]
            return {
                "processed_chapters": state["chapters_to_process"],
                "error": None,
            }

        mock_app = MagicMock()
        mock_app.invoke.side_effect = invoke
        mock_build_graph.return_value = mock_app

        mock_kb = MagicMock()
        mock_kb.collection.count.return_value = 0
        mock_kb_manager.return_value = mock_kb

        project_name = "test_project_retry_failed_global_context_error"
        input_dir = os.path.join(self.test_temp_dir, project_name, "input")
        output_dir = os.path.join(self.test_temp_dir, project_name, "output")
        os.makedirs(input_dir, exist_ok=True)
        os.makedirs(output_dir, exist_ok=True)

        with open(os.path.join(input_dir, "chapter1.txt"), "w", encoding="utf-8") as f:
            f.write("chapter1 source")
        with open(os.path.join(output_dir, "chapter1.txt"), "w", encoding="utf-8") as f:
            f.write("usable translated text")

        with open(
            os.path.join(output_dir, "translation_report.json"),
            "w",
            encoding="utf-8",
        ) as report_file:
            json.dump(
                {
                    "chapters": [
                        {
                            "title": "chapter1",
                            "status": "failed",
                            "error": "Context retrieval failed: memory db locked",
                        }
                    ]
                },
                report_file,
            )

        project_settings = {
            "input_dir": input_dir,
            "output_dir": output_dir,
            "GOOGLE_API_KEY": "AIza-real-looking-key",
            "retry_failed": True,
        }

        run_translation_workflow(project_name, project_settings)

        self.assertTrue(
            captured_state["chapters_to_process"][0]["reuse_existing_translation"]
        )

    @patch("src.Perevod.graph_runner.DatabaseManager")
    @patch("src.Perevod.graph_runner.KnowledgeBaseManager")
    @patch("src.Perevod.graph_runner.LLMProvider")
    @patch("src.Perevod.graph_runner.build_graph")
    def test_retry_incomplete_processes_summary_warning_chapters(
        self,
        mock_build_graph,
        mock_llm_provider,
        mock_kb_manager,
        mock_db_manager,
    ):
        captured_state = {}

        def invoke(state):
            captured_state["chapters_to_process"] = state["chapters_to_process"]
            return {
                "processed_chapters": state["chapters_to_process"],
                "judge_results": [{"title": "chapter2", "pass_check": True}],
                "chapter_summaries": [{"title": "chapter2", "summary": "done"}],
                "summary_errors": [],
                "error": None,
            }

        mock_app = MagicMock()
        mock_app.invoke.side_effect = invoke
        mock_build_graph.return_value = mock_app

        mock_kb = MagicMock()
        mock_kb.collection.count.return_value = 0
        mock_kb_manager.return_value = mock_kb

        project_name = "test_project_retry_incomplete"
        input_dir = os.path.join(self.test_temp_dir, project_name, "input")
        output_dir = os.path.join(self.test_temp_dir, project_name, "output")
        os.makedirs(input_dir, exist_ok=True)
        os.makedirs(output_dir, exist_ok=True)

        for chapter_name in ("chapter1", "chapter2"):
            with open(
                os.path.join(input_dir, f"{chapter_name}.txt"),
                "w",
                encoding="utf-8",
            ) as f:
                f.write(f"{chapter_name} source")
            with open(
                os.path.join(output_dir, f"{chapter_name}.txt"),
                "w",
                encoding="utf-8",
            ) as f:
                f.write(f"{chapter_name} translated")

        with open(
            os.path.join(output_dir, "translation_report.json"),
            "w",
            encoding="utf-8",
        ) as report_file:
            json.dump(
                {
                    "chapters": [
                        {
                            "title": "chapter1",
                            "status": "translated",
                            "stages": {
                                "translation": "done",
                                "judge": "done",
                                "summary": "done",
                            },
                            "warnings": [],
                        },
                        {
                            "title": "chapter2",
                            "status": "translated",
                            "stages": {
                                "translation": "done",
                                "judge": "done",
                                "summary": "failed",
                            },
                            "warnings": ["Summary memory was not updated"],
                        },
                    ]
                },
                report_file,
            )

        project_settings = {
            "input_dir": input_dir,
            "output_dir": output_dir,
            "GOOGLE_API_KEY": "AIza-real-looking-key",
            "retry_incomplete": True,
        }

        run_translation_workflow(project_name, project_settings)

        self.assertEqual(
            [chapter["title"] for chapter in captured_state["chapters_to_process"]],
            ["chapter2"],
        )
        self.assertTrue(
            captured_state["chapters_to_process"][0]["reuse_existing_translation"]
        )

        with open(
            os.path.join(output_dir, "translation_report.json"),
            encoding="utf-8",
        ) as report_file:
            report = json.load(report_file)
        statuses = {chapter["title"]: chapter["status"] for chapter in report["chapters"]}
        self.assertEqual(statuses["chapter1"], "skipped_not_failed")
        self.assertEqual(statuses["chapter2"], "translated")
        self.assertEqual(report["warning_count"], 0)
        self.assertEqual(report["chapters"][1]["stages"]["summary"], "done")

    @patch("src.Perevod.graph_runner.DatabaseManager")
    @patch("src.Perevod.graph_runner.KnowledgeBaseManager")
    @patch("src.Perevod.graph_runner.LLMProvider")
    @patch("src.Perevod.graph_runner.build_graph")
    def test_retry_incomplete_processes_context_warnings_without_string_warnings(
        self,
        mock_build_graph,
        mock_llm_provider,
        mock_kb_manager,
        mock_db_manager,
    ):
        captured_state = {}

        def invoke(state):
            captured_state["chapters_to_process"] = state["chapters_to_process"]
            return {
                "processed_chapters": state["chapters_to_process"],
                "judge_results": [{"title": "chapter2", "pass_check": True}],
                "chapter_summaries": [{"title": "chapter2", "summary": "done"}],
                "summary_errors": [],
                "error": None,
            }

        mock_app = MagicMock()
        mock_app.invoke.side_effect = invoke
        mock_build_graph.return_value = mock_app

        mock_kb = MagicMock()
        mock_kb.collection.count.return_value = 0
        mock_kb_manager.return_value = mock_kb

        project_name = "test_project_retry_context_warning"
        input_dir = os.path.join(self.test_temp_dir, project_name, "input")
        output_dir = os.path.join(self.test_temp_dir, project_name, "output")
        os.makedirs(input_dir, exist_ok=True)
        os.makedirs(output_dir, exist_ok=True)

        for chapter_name in ("chapter1", "chapter2"):
            with open(
                os.path.join(input_dir, f"{chapter_name}.txt"),
                "w",
                encoding="utf-8",
            ) as f:
                f.write(f"{chapter_name} source")
            with open(
                os.path.join(output_dir, f"{chapter_name}.txt"),
                "w",
                encoding="utf-8",
            ) as f:
                f.write(f"{chapter_name} translated")

        with open(
            os.path.join(output_dir, "translation_report.json"),
            "w",
            encoding="utf-8",
        ) as report_file:
            json.dump(
                {
                    "chapters": [
                        {
                            "title": "chapter1",
                            "status": "translated",
                            "stages": {
                                "context": "done",
                                "translation": "done",
                                "judge": "done",
                                "summary": "done",
                            },
                            "warnings": [],
                        },
                        {
                            "title": "chapter2",
                            "status": "translated",
                            "stages": {
                                "context": "done",
                                "translation": "done",
                                "judge": "done",
                                "summary": "done",
                            },
                            "warnings": [],
                            "context_warnings": [
                                {
                                    "title": "chapter2",
                                    "scope": "semantic_lore",
                                    "error": "embedding quota exhausted",
                                }
                            ],
                        },
                    ]
                },
                report_file,
            )

        project_settings = {
            "input_dir": input_dir,
            "output_dir": output_dir,
            "GOOGLE_API_KEY": "AIza-real-looking-key",
            "retry_incomplete": True,
        }

        run_translation_workflow(project_name, project_settings)

        self.assertEqual(
            [chapter["title"] for chapter in captured_state["chapters_to_process"]],
            ["chapter2"],
        )
        self.assertTrue(
            captured_state["chapters_to_process"][0]["reuse_existing_translation"]
        )

    @patch("src.Perevod.graph_runner.DatabaseManager")
    @patch("src.Perevod.graph_runner.KnowledgeBaseManager")
    @patch("src.Perevod.graph_runner.LLMProvider")
    @patch("src.Perevod.graph_runner.build_graph")
    def test_retry_incomplete_processes_stage_not_started_without_warnings(
        self,
        mock_build_graph,
        mock_llm_provider,
        mock_kb_manager,
        mock_db_manager,
    ):
        captured_state = {}

        def invoke(state):
            captured_state["chapters_to_process"] = state["chapters_to_process"]
            return {
                "processed_chapters": state["chapters_to_process"],
                "judge_results": [{"title": "chapter2", "pass_check": True}],
                "chapter_summaries": [{"title": "chapter2", "summary": "done"}],
                "summary_errors": [],
                "error": None,
            }

        mock_app = MagicMock()
        mock_app.invoke.side_effect = invoke
        mock_build_graph.return_value = mock_app

        mock_kb = MagicMock()
        mock_kb.collection.count.return_value = 0
        mock_kb_manager.return_value = mock_kb

        project_name = "test_project_retry_incomplete_stage_not_started"
        input_dir = os.path.join(self.test_temp_dir, project_name, "input")
        output_dir = os.path.join(self.test_temp_dir, project_name, "output")
        os.makedirs(input_dir, exist_ok=True)
        os.makedirs(output_dir, exist_ok=True)

        for chapter_name in ("chapter1", "chapter2"):
            with open(
                os.path.join(input_dir, f"{chapter_name}.txt"),
                "w",
                encoding="utf-8",
            ) as f:
                f.write(f"{chapter_name} source")
            with open(
                os.path.join(output_dir, f"{chapter_name}.txt"),
                "w",
                encoding="utf-8",
            ) as f:
                f.write(f"{chapter_name} translated")

        with open(
            os.path.join(output_dir, "translation_report.json"),
            "w",
            encoding="utf-8",
        ) as report_file:
            json.dump(
                {
                    "chapters": [
                        {
                            "title": "chapter1",
                            "status": "translated",
                            "stages": {
                                "translation": "done",
                                "judge": "done",
                                "summary": "done",
                            },
                            "warnings": [],
                        },
                        {
                            "title": "chapter2",
                            "status": "translated",
                            "stages": {
                                "translation": "done",
                                "judge": "done",
                                "summary": "not_started",
                            },
                            "warnings": [],
                        },
                    ]
                },
                report_file,
            )

        project_settings = {
            "input_dir": input_dir,
            "output_dir": output_dir,
            "GOOGLE_API_KEY": "AIza-real-looking-key",
            "retry_incomplete": True,
        }

        run_translation_workflow(project_name, project_settings)

        self.assertEqual(
            [chapter["title"] for chapter in captured_state["chapters_to_process"]],
            ["chapter2"],
        )
        self.assertTrue(
            captured_state["chapters_to_process"][0]["reuse_existing_translation"]
        )

    @patch("src.Perevod.graph_runner.DatabaseManager")
    @patch("src.Perevod.graph_runner.KnowledgeBaseManager")
    @patch("src.Perevod.graph_runner.LLMProvider")
    @patch("src.Perevod.graph_runner.build_graph")
    def test_retry_incomplete_skips_chapters_marked_skipped_not_failed(
        self,
        mock_build_graph,
        mock_llm_provider,
        mock_kb_manager,
        mock_db_manager,
    ):
        captured_state = {}

        def invoke(state):
            captured_state["chapters_to_process"] = state["chapters_to_process"]
            return {
                "processed_chapters": state["chapters_to_process"],
                "judge_results": [{"title": "chapter2", "pass_check": True}],
                "chapter_summaries": [{"title": "chapter2", "summary": "done"}],
                "summary_errors": [],
                "error": None,
            }

        mock_app = MagicMock()
        mock_app.invoke.side_effect = invoke
        mock_build_graph.return_value = mock_app

        mock_kb = MagicMock()
        mock_kb.collection.count.return_value = 0
        mock_kb_manager.return_value = mock_kb

        project_name = "test_project_retry_incomplete_skip_status"
        input_dir = os.path.join(self.test_temp_dir, project_name, "input")
        output_dir = os.path.join(self.test_temp_dir, project_name, "output")
        os.makedirs(input_dir, exist_ok=True)
        os.makedirs(output_dir, exist_ok=True)

        for chapter_name in ("chapter1", "chapter2"):
            with open(
                os.path.join(input_dir, f"{chapter_name}.txt"),
                "w",
                encoding="utf-8",
            ) as f:
                f.write(f"{chapter_name} source")
            with open(
                os.path.join(output_dir, f"{chapter_name}.txt"),
                "w",
                encoding="utf-8",
            ) as f:
                f.write(f"{chapter_name} translated")

        with open(
            os.path.join(output_dir, "translation_report.json"),
            "w",
            encoding="utf-8",
        ) as report_file:
            json.dump(
                {
                    "chapters": [
                        {
                            "title": "chapter1",
                            "status": "skipped_not_failed",
                            "stages": {
                                "context": "skipped",
                                "analysis": "skipped",
                                "glossary": "skipped",
                                "translation": "skipped",
                                "output": "skipped",
                                "judge": "skipped",
                                "refine": "skipped",
                                "summary": "skipped",
                                "memory": "skipped",
                            },
                            "warnings": [],
                        },
                        {
                            "title": "chapter2",
                            "status": "translated",
                            "stages": {
                                "context": "done",
                                "analysis": "done",
                                "glossary": "done",
                                "translation": "done",
                                "output": "done",
                                "judge": "done",
                                "refine": "not_needed",
                                "summary": "not_started",
                                "memory": "not_started",
                            },
                            "warnings": [],
                        },
                    ]
                },
                report_file,
            )

        project_settings = {
            "input_dir": input_dir,
            "output_dir": output_dir,
            "GOOGLE_API_KEY": "AIza-real-looking-key",
            "retry_incomplete": True,
        }

        run_translation_workflow(project_name, project_settings)

        self.assertEqual(
            [chapter["title"] for chapter in captured_state["chapters_to_process"]],
            ["chapter2"],
        )

    @patch("src.Perevod.graph_runner.DatabaseManager")
    @patch("src.Perevod.graph_runner.KnowledgeBaseManager")
    @patch("src.Perevod.graph_runner.LLMProvider")
    @patch("src.Perevod.graph_runner.build_graph")
    def test_retry_incomplete_ignores_missing_new_stage_keys_in_hybrid_report(
        self,
        mock_build_graph,
        mock_llm_provider,
        mock_kb_manager,
        mock_db_manager,
    ):
        captured_state = {}

        def invoke(state):
            captured_state["chapters_to_process"] = state["chapters_to_process"]
            return {
                "processed_chapters": state["chapters_to_process"],
                "judge_results": [{"title": "chapter2", "pass_check": True}],
                "chapter_summaries": [{"title": "chapter2", "summary": "done"}],
                "summary_errors": [],
                "error": None,
            }

        mock_app = MagicMock()
        mock_app.invoke.side_effect = invoke
        mock_build_graph.return_value = mock_app

        mock_kb = MagicMock()
        mock_kb.collection.count.return_value = 0
        mock_kb_manager.return_value = mock_kb

        project_name = "test_project_retry_incomplete_hybrid_schema"
        input_dir = os.path.join(self.test_temp_dir, project_name, "input")
        output_dir = os.path.join(self.test_temp_dir, project_name, "output")
        os.makedirs(input_dir, exist_ok=True)
        os.makedirs(output_dir, exist_ok=True)

        for chapter_name in ("chapter1", "chapter2"):
            with open(
                os.path.join(input_dir, f"{chapter_name}.txt"),
                "w",
                encoding="utf-8",
            ) as f:
                f.write(f"{chapter_name} source")
            with open(
                os.path.join(output_dir, f"{chapter_name}.txt"),
                "w",
                encoding="utf-8",
            ) as f:
                f.write(f"{chapter_name} translated")

        with open(
            os.path.join(output_dir, "translation_report.json"),
            "w",
            encoding="utf-8",
        ) as report_file:
            json.dump(
                {
                    "chapters": [
                        {
                            "title": "chapter1",
                            "status": "translated",
                            "stages": {
                                "context": "done",
                                "analysis": "done",
                                "translation": "done",
                                "judge": "done",
                                "refine": "not_needed",
                                "summary": "done",
                            },
                            "warnings": [],
                        },
                        {
                            "title": "chapter2",
                            "status": "translated",
                            "stages": {
                                "context": "done",
                                "analysis": "done",
                                "translation": "done",
                                "judge": "done",
                                "refine": "not_needed",
                                "summary": "not_started",
                            },
                            "warnings": [],
                        },
                    ]
                },
                report_file,
            )

        project_settings = {
            "input_dir": input_dir,
            "output_dir": output_dir,
            "GOOGLE_API_KEY": "AIza-real-looking-key",
            "retry_incomplete": True,
        }

        run_translation_workflow(project_name, project_settings)

        self.assertEqual(
            [chapter["title"] for chapter in captured_state["chapters_to_process"]],
            ["chapter2"],
        )

    @patch("src.Perevod.graph_runner.DatabaseManager")
    @patch("src.Perevod.graph_runner.KnowledgeBaseManager")
    @patch("src.Perevod.graph_runner.LLMProvider")
    @patch("src.Perevod.graph_runner.build_graph")
    def test_report_marks_processed_chapter_failed_when_qa_fails(
        self,
        mock_build_graph,
        mock_llm_provider,
        mock_kb_manager,
        mock_db_manager,
    ):
        mock_app = MagicMock()
        mock_app.invoke.return_value = {
            "processed_chapters": [{"title": "chapter1"}],
            "error": "Ошибка Судьи для главы 'chapter1': invalid judge response",
        }
        mock_build_graph.return_value = mock_app

        mock_kb = MagicMock()
        mock_kb.collection.count.return_value = 0
        mock_kb_manager.return_value = mock_kb

        project_name = "test_project_qa_failed_report"
        input_dir = os.path.join(self.test_temp_dir, project_name, "input")
        output_dir = os.path.join(self.test_temp_dir, project_name, "output")
        os.makedirs(input_dir, exist_ok=True)
        os.makedirs(output_dir, exist_ok=True)

        with open(os.path.join(input_dir, "chapter1.txt"), "w", encoding="utf-8") as f:
            f.write("This is a test chapter.")

        project_settings = {
            "input_dir": input_dir,
            "output_dir": output_dir,
            "GOOGLE_API_KEY": "AIza-real-looking-key",
        }

        with self.assertRaises(RuntimeError):
            run_translation_workflow(project_name, project_settings)

        with open(
            os.path.join(output_dir, "translation_report.json"),
            encoding="utf-8",
        ) as report_file:
            report = json.load(report_file)

        self.assertEqual(report["chapters"][0]["status"], "failed")
        self.assertEqual(report["chapters"][0]["stages"]["translation"], "done")
        self.assertEqual(report["chapters"][0]["stages"]["judge"], "failed")
        self.assertIn("invalid judge response", report["chapters"][0]["error"])

    @patch("src.Perevod.graph_runner.DatabaseManager")
    @patch("src.Perevod.graph_runner.KnowledgeBaseManager")
    @patch("src.Perevod.graph_runner.LLMProvider")
    @patch("src.Perevod.graph_runner.build_graph")
    def test_report_marks_editor_error_as_downstream_qa_failure(
        self,
        mock_build_graph,
        mock_llm_provider,
        mock_kb_manager,
        mock_db_manager,
    ):
        mock_app = MagicMock()
        mock_app.invoke.return_value = {
            "processed_chapters": [{"title": "chapter1"}],
            "judge_results": [
                {
                    "title": "chapter1",
                    "pass_check": False,
                    "blocking_issues": ["Omission"],
                }
            ],
            "error": "Ошибка Редактора для главы 'chapter1': редактор вернул пустую правку",
        }
        mock_build_graph.return_value = mock_app

        mock_kb = MagicMock()
        mock_kb.collection.count.return_value = 0
        mock_kb_manager.return_value = mock_kb

        project_name = "test_project_editor_failed_report"
        input_dir = os.path.join(self.test_temp_dir, project_name, "input")
        output_dir = os.path.join(self.test_temp_dir, project_name, "output")
        os.makedirs(input_dir, exist_ok=True)
        os.makedirs(output_dir, exist_ok=True)

        with open(os.path.join(input_dir, "chapter1.txt"), "w", encoding="utf-8") as f:
            f.write("This is a test chapter.")

        project_settings = {
            "input_dir": input_dir,
            "output_dir": output_dir,
            "GOOGLE_API_KEY": "AIza-real-looking-key",
        }

        with self.assertRaises(RuntimeError):
            run_translation_workflow(project_name, project_settings)

        with open(
            os.path.join(output_dir, "translation_report.json"),
            encoding="utf-8",
        ) as report_file:
            report = json.load(report_file)

        chapter = report["chapters"][0]
        self.assertEqual(chapter["status"], "failed")
        self.assertEqual(chapter["stages"]["translation"], "done")
        self.assertEqual(chapter["stages"]["judge"], "done")
        self.assertEqual(chapter["stages"]["refine"], "failed")
        self.assertEqual(chapter["stages"]["summary"], "not_started")
        self.assertIn("редактор вернул пустую правку", chapter["error"])

    @patch("src.Perevod.graph_runner.DatabaseManager")
    @patch("src.Perevod.graph_runner.KnowledgeBaseManager")
    @patch("src.Perevod.graph_runner.LLMProvider")
    @patch("src.Perevod.graph_runner.build_graph")
    def test_remaining_qa_blockers_fail_workflow_after_report(
        self,
        mock_build_graph,
        mock_llm_provider,
        mock_kb_manager,
        mock_db_manager,
    ):
        mock_app = MagicMock()
        mock_app.invoke.return_value = {
            "processed_chapters": [
                {"title": "chapter1", "blocking_issues": ["Missing canonical term"]}
            ],
            "judge_results": [
                {
                    "title": "chapter1",
                    "pass_check": False,
                    "blocking_issues": ["Missing canonical term"],
                }
            ],
            "blocking_issues": ["Missing canonical term"],
            "error": None,
        }
        mock_build_graph.return_value = mock_app

        mock_kb = MagicMock()
        mock_kb.collection.count.return_value = 0
        mock_kb_manager.return_value = mock_kb

        project_name = "test_project_remaining_qa_blockers"
        input_dir = os.path.join(self.test_temp_dir, project_name, "input")
        output_dir = os.path.join(self.test_temp_dir, project_name, "output")
        os.makedirs(input_dir, exist_ok=True)
        os.makedirs(output_dir, exist_ok=True)

        with open(os.path.join(input_dir, "chapter1.txt"), "w", encoding="utf-8") as f:
            f.write("This is a test chapter.")

        project_settings = {
            "input_dir": input_dir,
            "output_dir": output_dir,
            "GOOGLE_API_KEY": "AIza-real-looking-key",
        }

        with self.assertRaisesRegex(RuntimeError, "QA failed"):
            run_translation_workflow(project_name, project_settings)

        with open(
            os.path.join(output_dir, "translation_report.json"),
            encoding="utf-8",
        ) as report_file:
            report = json.load(report_file)

        self.assertEqual(report["failed_count"], 1)
        self.assertEqual(report["chapters"][0]["status"], "qa_failed")
        self.assertIn("QA failed", report["chapters"][0]["error"])
        self.assertIn("Missing canonical term", report["chapters"][0]["error"])
        self.assertIn("QA failed after refinement limit", report["error"])

    @patch("src.Perevod.graph_runner.DatabaseManager")
    @patch("src.Perevod.graph_runner.KnowledgeBaseManager")
    @patch("src.Perevod.graph_runner.LLMProvider")
    @patch("src.Perevod.graph_runner.build_graph")
    def test_per_chapter_qa_blockers_fail_workflow_without_global_blockers(
        self,
        mock_build_graph,
        mock_llm_provider,
        mock_kb_manager,
        mock_db_manager,
    ):
        mock_app = MagicMock()
        mock_app.invoke.return_value = {
            "processed_chapters": [
                {"title": "chapter1", "blocking_issues": ["Missing canonical term"]}
            ],
            "judge_results": [
                {
                    "title": "chapter1",
                    "pass_check": False,
                    "blocking_issues": ["Missing canonical term"],
                }
            ],
            "blocking_issues": [],
            "error": None,
        }
        mock_build_graph.return_value = mock_app

        mock_kb = MagicMock()
        mock_kb.collection.count.return_value = 0
        mock_kb_manager.return_value = mock_kb

        project_name = "test_project_per_chapter_qa_blockers"
        input_dir = os.path.join(self.test_temp_dir, project_name, "input")
        output_dir = os.path.join(self.test_temp_dir, project_name, "output")
        os.makedirs(input_dir, exist_ok=True)
        os.makedirs(output_dir, exist_ok=True)

        with open(os.path.join(input_dir, "chapter1.txt"), "w", encoding="utf-8") as f:
            f.write("This is a test chapter.")

        project_settings = {
            "input_dir": input_dir,
            "output_dir": output_dir,
            "GOOGLE_API_KEY": "AIza-real-looking-key",
        }

        with self.assertRaisesRegex(RuntimeError, "QA failed"):
            run_translation_workflow(project_name, project_settings)

        with open(
            os.path.join(output_dir, "translation_report.json"),
            encoding="utf-8",
        ) as report_file:
            report = json.load(report_file)

        self.assertEqual(report["failed_count"], 1)
        self.assertEqual(report["chapters"][0]["status"], "qa_failed")
        self.assertIn("Missing canonical term", report["chapters"][0]["error"])

    @patch("src.Perevod.graph_runner.DatabaseManager")
    @patch("src.Perevod.graph_runner.KnowledgeBaseManager")
    @patch("src.Perevod.graph_runner.LLMProvider")
    @patch("src.Perevod.graph_runner.build_graph")
    def test_report_surfaces_summary_errors_as_failed_stage(
        self,
        mock_build_graph,
        mock_llm_provider,
        mock_kb_manager,
        mock_db_manager,
    ):
        mock_app = MagicMock()
        mock_app.invoke.return_value = {
            "processed_chapters": [{"title": "chapter1"}],
            "judge_results": [{"title": "chapter1", "pass_check": True}],
            "chapter_summaries": [],
            "summary_errors": [{"title": "chapter1", "error": "invalid summary json"}],
            "error": None,
        }
        mock_build_graph.return_value = mock_app

        mock_kb = MagicMock()
        mock_kb.collection.count.return_value = 0
        mock_kb_manager.return_value = mock_kb

        project_name = "test_project_summary_warning_report"
        input_dir = os.path.join(self.test_temp_dir, project_name, "input")
        output_dir = os.path.join(self.test_temp_dir, project_name, "output")
        os.makedirs(input_dir, exist_ok=True)
        os.makedirs(output_dir, exist_ok=True)

        with open(os.path.join(input_dir, "chapter1.txt"), "w", encoding="utf-8") as f:
            f.write("This is a test chapter.")

        project_settings = {
            "input_dir": input_dir,
            "output_dir": output_dir,
            "GOOGLE_API_KEY": "AIza-real-looking-key",
        }

        with self.assertRaisesRegex(RuntimeError, "Summary failed"):
            run_translation_workflow(project_name, project_settings)

        with open(
            os.path.join(output_dir, "translation_report.json"),
            encoding="utf-8",
        ) as report_file:
            report = json.load(report_file)

        self.assertEqual(report["failed_count"], 1)
        self.assertEqual(report["warning_count"], 1)
        self.assertEqual(report["chapters"][0]["status"], "failed")
        self.assertEqual(report["chapters"][0]["stages"]["summary"], "failed")
        self.assertIn("invalid summary json", report["chapters"][0]["error"])
        self.assertIn("invalid summary json", report["chapters"][0]["warnings"][0])

    @patch("src.Perevod.graph_runner.DatabaseManager")
    @patch("src.Perevod.graph_runner.KnowledgeBaseManager")
    @patch("src.Perevod.graph_runner.LLMProvider")
    @patch("src.Perevod.graph_runner.build_graph")
    def test_summary_errors_fail_workflow_after_report(
        self,
        mock_build_graph,
        mock_llm_provider,
        mock_kb_manager,
        mock_db_manager,
    ):
        mock_app = MagicMock()
        mock_app.invoke.return_value = {
            "processed_chapters": [{"title": "chapter1"}],
            "judge_results": [{"title": "chapter1", "pass_check": True}],
            "chapter_summaries": [],
            "summary_errors": [{"title": "chapter1", "error": "invalid summary json"}],
            "error": None,
        }
        mock_build_graph.return_value = mock_app

        mock_kb = MagicMock()
        mock_kb.collection.count.return_value = 0
        mock_kb_manager.return_value = mock_kb

        project_name = "test_project_summary_failed_report"
        input_dir = os.path.join(self.test_temp_dir, project_name, "input")
        output_dir = os.path.join(self.test_temp_dir, project_name, "output")
        os.makedirs(input_dir, exist_ok=True)
        os.makedirs(output_dir, exist_ok=True)

        with open(os.path.join(input_dir, "chapter1.txt"), "w", encoding="utf-8") as f:
            f.write("This is a test chapter.")

        with self.assertRaisesRegex(RuntimeError, "Summary failed"):
            run_translation_workflow(
                project_name,
                {
                    "input_dir": input_dir,
                    "output_dir": output_dir,
                    "GOOGLE_API_KEY": "AIza-real-looking-key",
                },
            )

        with open(
            os.path.join(output_dir, "translation_report.json"),
            encoding="utf-8",
        ) as report_file:
            report = json.load(report_file)

        self.assertEqual(report["failed_count"], 1)
        self.assertEqual(report["chapters"][0]["status"], "failed")
        self.assertEqual(report["chapters"][0]["stages"]["translation"], "done")
        self.assertEqual(report["chapters"][0]["stages"]["judge"], "done")
        self.assertEqual(report["chapters"][0]["stages"]["summary"], "failed")
        self.assertIn("invalid summary json", report["chapters"][0]["error"])

    def test_write_workflow_report_marks_all_summary_failed_chapters_as_failed(self):
        output_dir = os.path.join(self.test_temp_dir, "multi_summary_failure_report_output")
        os.makedirs(output_dir, exist_ok=True)

        report_path = _write_workflow_report(
            "Book",
            output_dir,
            [
                {
                    "title": "chapter1",
                    "input_path": "input/chapter1.txt",
                    "output_path": "output/chapter1.txt",
                },
                {
                    "title": "chapter2",
                    "input_path": "input/chapter2.txt",
                    "output_path": "output/chapter2.txt",
                },
            ],
            {
                "processed_chapters": [
                    {"title": "chapter1"},
                    {"title": "chapter2"},
                ],
                "judge_results": [
                    {"title": "chapter1", "pass_check": True},
                    {"title": "chapter2", "pass_check": True},
                ],
                "summary_errors": [
                    {"title": "chapter1", "error": "invalid summary json 1"},
                    {"title": "chapter2", "error": "invalid summary json 2"},
                ],
                "error": "Summary failed for chapter 'chapter1': invalid summary json 1",
            },
            {"input_dir": "input", "output_dir": "output"},
        )

        with open(report_path, encoding="utf-8") as report_file:
            report = json.load(report_file)

        statuses = {chapter["title"]: chapter["status"] for chapter in report["chapters"]}
        self.assertEqual(statuses["chapter1"], "failed")
        self.assertEqual(statuses["chapter2"], "failed")
        self.assertEqual(report["failed_count"], 2)

    def test_write_workflow_report_uses_checkpoint_error_for_failed_chapter(self):
        output_dir = os.path.join(self.test_temp_dir, "checkpoint_error_report_output")
        os.makedirs(output_dir, exist_ok=True)

        report_path = _write_workflow_report(
            "Book",
            output_dir,
            [
                {
                    "title": "chapter1",
                    "input_path": "input/chapter1.txt",
                    "output_path": "output/chapter1.txt",
                },
                {
                    "title": "chapter2",
                    "input_path": "input/chapter2.txt",
                    "output_path": "output/chapter2.txt",
                },
            ],
            {
                "processed_chapters": [
                    {"title": "chapter1"},
                    {"title": "chapter2"},
                ],
                "chapter_runs": {
                    "chapter1": {
                        "stages": {
                            "translation_done": "done",
                            "judge_done": "done",
                        },
                        "error": None,
                    },
                    "chapter2": {
                        "stages": {
                            "translation_done": "done",
                            "judge_done": "failed",
                        },
                        "error": "invalid judge json",
                    },
                },
                "error": "Summary failed for chapter 'chapter1': invalid summary json 1",
            },
            {"input_dir": "input", "output_dir": "output"},
        )

        with open(report_path, encoding="utf-8") as report_file:
            report = json.load(report_file)

        chapter_by_title = {chapter["title"]: chapter for chapter in report["chapters"]}
        self.assertEqual(chapter_by_title["chapter2"]["status"], "failed")
        self.assertEqual(chapter_by_title["chapter2"]["error"], "invalid judge json")

    def test_write_workflow_report_adds_fallback_error_for_failed_stage(self):
        output_dir = os.path.join(self.test_temp_dir, "failed_stage_fallback_error_output")
        os.makedirs(output_dir, exist_ok=True)

        report_path = _write_workflow_report(
            "Book",
            output_dir,
            [
                {
                    "title": "chapter1",
                    "input_path": "input/chapter1.txt",
                    "output_path": "output/chapter1.txt",
                }
            ],
            {
                "processed_chapters": [{"title": "chapter1"}],
                "chapter_runs": {
                    "chapter1": {
                        "stages": {"judge_done": "failed"},
                        "error": None,
                    }
                },
                "error": None,
            },
            {"input_dir": "input", "output_dir": "output"},
        )

        with open(report_path, encoding="utf-8") as report_file:
            report = json.load(report_file)

        chapter = report["chapters"][0]
        self.assertEqual(chapter["status"], "failed")
        self.assertEqual(chapter["stages"]["judge"], "failed")
        self.assertIsInstance(chapter["error"], str)
        self.assertIn("judge", chapter["error"])

    def test_write_workflow_report_marks_translated_from_checkpoint_without_processed_chapter(self):
        output_dir = os.path.join(self.test_temp_dir, "checkpoint_translated_status_output")
        os.makedirs(output_dir, exist_ok=True)

        report_path = _write_workflow_report(
            "Book",
            output_dir,
            [
                {
                    "title": "chapter1",
                    "input_path": "input/chapter1.txt",
                    "output_path": "output/chapter1.txt",
                    "status": "pending",
                }
            ],
            {
                "processed_chapters": [],
                "chapter_runs": {
                    "chapter1": {
                        "stages": {
                            "context_retrieved": "done",
                            "analysis_done": "done",
                            "glossary_updated": "done",
                            "translation_done": "done",
                            "output_written": "done",
                            "judge_done": "done",
                            "summary_done": "done",
                            "memory_updated": "done",
                        },
                        "error": None,
                    }
                },
                "error": None,
            },
            {"input_dir": "input", "output_dir": "output"},
        )

        with open(report_path, encoding="utf-8") as report_file:
            report = json.load(report_file)

        chapter = report["chapters"][0]
        self.assertEqual(chapter["stages"]["translation"], "done")
        self.assertEqual(chapter["status"], "translated")
        self.assertEqual(report["processed_count"], 1)
        self.assertEqual(report["processed_chapters"], ["chapter1"])

    def test_write_workflow_report_uses_checkpoint_judge_result_for_qa_failed_status(self):
        output_dir = os.path.join(self.test_temp_dir, "checkpoint_judge_result_report_output")
        os.makedirs(output_dir, exist_ok=True)

        report_path = _write_workflow_report(
            "Book",
            output_dir,
            [
                {
                    "title": "chapter1",
                    "input_path": "input/chapter1.txt",
                    "output_path": "output/chapter1.txt",
                    "status": "pending",
                }
            ],
            {
                "processed_chapters": [],
                "chapter_runs": {
                    "chapter1": {
                        "stages": {
                            "context_retrieved": "done",
                            "analysis_done": "done",
                            "glossary_updated": "done",
                            "translation_done": "done",
                            "output_written": "done",
                            "judge_done": "done",
                        },
                        "judge_result": {
                            "pass_check": False,
                            "severity": "high",
                            "score": 4,
                            "blocking_issues": ["Missing canonical term"],
                            "suggestions": ["Fix glossary usage"],
                        },
                        "error": None,
                    }
                },
                "error": None,
            },
            {"input_dir": "input", "output_dir": "output"},
        )

        with open(report_path, encoding="utf-8") as report_file:
            report = json.load(report_file)

        chapter = report["chapters"][0]
        self.assertEqual(chapter["stages"]["translation"], "done")
        self.assertEqual(chapter["status"], "qa_failed")
        self.assertEqual(chapter["blocking_issues"], ["Missing canonical term"])
        self.assertIn("QA failed", chapter["error"])
        self.assertIn("Missing canonical term", chapter["error"])
        self.assertFalse(chapter["judge_pass_check"])
        self.assertEqual(chapter["judge_severity"], "high")
        self.assertEqual(chapter["judge_score"], 4)
        self.assertEqual(chapter["quality_suggestions"], ["Fix glossary usage"])

    def test_write_workflow_report_current_passing_judge_clears_stale_blockers(self):
        output_dir = os.path.join(self.test_temp_dir, "passing_judge_clears_stale_blockers")
        os.makedirs(output_dir, exist_ok=True)

        report_path = _write_workflow_report(
            "Book",
            output_dir,
            [
                {
                    "title": "chapter1",
                    "input_path": "input/chapter1.txt",
                    "output_path": "output/chapter1.txt",
                }
            ],
            {
                "processed_chapters": [
                    {"title": "chapter1", "blocking_issues": ["Old issue before refine"]}
                ],
                "judge_results": [
                    {
                        "title": "chapter1",
                        "pass_check": True,
                        "blocking_issues": [],
                        "score": 9,
                    }
                ],
                "blocking_issues": [],
                "error": None,
            },
            {"input_dir": "input", "output_dir": "output"},
        )

        with open(report_path, encoding="utf-8") as report_file:
            report = json.load(report_file)

        chapter = report["chapters"][0]
        self.assertEqual(chapter["status"], "translated")
        self.assertEqual(chapter["stages"]["refine"], "not_needed")
        self.assertEqual(chapter["blocking_issues"], [])
        self.assertIsNone(chapter["error"])
        self.assertEqual(report["failed_count"], 0)

    def test_write_workflow_report_marks_failed_judge_without_blockers_as_qa_failed(self):
        output_dir = os.path.join(self.test_temp_dir, "failed_judge_without_blockers")
        os.makedirs(output_dir, exist_ok=True)

        report_path = _write_workflow_report(
            "Book",
            output_dir,
            [
                {
                    "title": "chapter1",
                    "input_path": "input/chapter1.txt",
                    "output_path": "output/chapter1.txt",
                }
            ],
            {
                "processed_chapters": [{"title": "chapter1"}],
                "judge_results": [
                    {
                        "title": "chapter1",
                        "pass_check": False,
                        "blocking_issues": [],
                        "score": 5,
                    }
                ],
                "blocking_issues": [],
                "error": None,
            },
            {"input_dir": "input", "output_dir": "output"},
        )

        with open(report_path, encoding="utf-8") as report_file:
            report = json.load(report_file)

        chapter = report["chapters"][0]
        self.assertEqual(chapter["status"], "qa_failed")
        self.assertEqual(chapter["stages"]["refine"], "needed")
        self.assertIn("Judge failed without blocking issue details", chapter["error"])
        self.assertEqual(report["failed_count"], 1)

    def test_write_workflow_report_uses_checkpoint_refine_result(self):
        output_dir = os.path.join(self.test_temp_dir, "checkpoint_refine_result_report_output")
        os.makedirs(output_dir, exist_ok=True)

        report_path = _write_workflow_report(
            "Book",
            output_dir,
            [
                {
                    "title": "chapter1",
                    "input_path": "input/chapter1.txt",
                    "output_path": "output/chapter1.txt",
                    "status": "pending",
                }
            ],
            {
                "processed_chapters": [],
                "chapter_runs": {
                    "chapter1": {
                        "stages": {
                            "translation_done": "done",
                            "output_written": "done",
                            "judge_done": "done",
                            "refine_done": "done",
                        },
                        "refine_result": {
                            "refined": True,
                            "refinement_count": 1,
                            "issues_fixed": ["Missing canonical term"],
                        },
                        "error": None,
                    }
                },
                "error": None,
            },
            {"input_dir": "input", "output_dir": "output"},
        )

        with open(report_path, encoding="utf-8") as report_file:
            report = json.load(report_file)

        chapter = report["chapters"][0]
        self.assertEqual(chapter["stages"]["refine"], "done")
        self.assertTrue(chapter["refined"])
        self.assertTrue(chapter["refine_checkpoint_reused"])
        self.assertEqual(chapter["refinement_count"], 1)
        self.assertEqual(chapter["refine_issues_fixed"], ["Missing canonical term"])

    def test_write_workflow_report_uses_checkpoint_summary_result(self):
        output_dir = os.path.join(self.test_temp_dir, "checkpoint_summary_result_report_output")
        os.makedirs(output_dir, exist_ok=True)

        report_path = _write_workflow_report(
            "Book",
            output_dir,
            [
                {
                    "title": "chapter1",
                    "input_path": "input/chapter1.txt",
                    "output_path": "output/chapter1.txt",
                    "status": "pending",
                }
            ],
            {
                "processed_chapters": [],
                "chapter_runs": {
                    "chapter1": {
                        "stages": {
                            "translation_done": "done",
                            "output_written": "done",
                            "judge_done": "done",
                            "summary_done": "done",
                            "memory_updated": "done",
                        },
                        "summary_result": {
                            "title": "chapter1",
                            "summary": "Checkpoint summary.",
                            "key_events": ["Event"],
                            "active_characters": ["Hero"],
                            "chapter_index": 1,
                        },
                        "error": None,
                    }
                },
                "error": None,
            },
            {"input_dir": "input", "output_dir": "output"},
        )

        with open(report_path, encoding="utf-8") as report_file:
            report = json.load(report_file)

        chapter = report["chapters"][0]
        self.assertEqual(chapter["stages"]["summary"], "done")
        self.assertEqual(chapter["stages"]["memory"], "done")
        self.assertTrue(chapter["summary_checkpoint_reused"])
        self.assertEqual(chapter["summary"], "Checkpoint summary.")
        self.assertEqual(chapter["summary_key_events"], ["Event"])
        self.assertEqual(chapter["summary_active_characters"], ["Hero"])
        self.assertEqual(chapter["summary_chapter_index"], 1)

    def test_write_workflow_report_marks_global_context_failure_for_all_processed(self):
        output_dir = os.path.join(self.test_temp_dir, "global_context_failure_report_output")
        os.makedirs(output_dir, exist_ok=True)

        report_path = _write_workflow_report(
            "Book",
            output_dir,
            [
                {
                    "title": "chapter1",
                    "input_path": "input/chapter1.txt",
                    "output_path": "output/chapter1.txt",
                    "status": "pending",
                },
                {
                    "title": "chapter2",
                    "input_path": "input/chapter2.txt",
                    "output_path": "output/chapter2.txt",
                    "status": "pending",
                },
                {
                    "title": "chapter3",
                    "input_path": "input/chapter3.txt",
                    "output_path": "output/chapter3.txt",
                    "status": "skipped_not_failed",
                },
            ],
            {
                "processed_chapters": [
                    {"title": "chapter1"},
                    {"title": "chapter2"},
                ],
                "context_errors": [{"title": "*", "error": "memory db locked"}],
                "judge_results": [
                    {"title": "chapter1", "pass_check": True},
                    {"title": "chapter2", "pass_check": True},
                ],
                "chapter_summaries": [
                    {"title": "chapter1"},
                    {"title": "chapter2"},
                ],
                "error": "Context retrieval failed: memory db locked",
            },
            {"input_dir": "input", "output_dir": "output"},
        )

        with open(report_path, encoding="utf-8") as report_file:
            report = json.load(report_file)

        chapter_by_title = {chapter["title"]: chapter for chapter in report["chapters"]}
        self.assertEqual(chapter_by_title["chapter1"]["status"], "failed")
        self.assertEqual(chapter_by_title["chapter1"]["stages"]["context"], "failed")
        self.assertEqual(chapter_by_title["chapter2"]["status"], "failed")
        self.assertEqual(chapter_by_title["chapter2"]["stages"]["context"], "failed")
        self.assertEqual(chapter_by_title["chapter3"]["status"], "skipped_not_failed")
        self.assertEqual(chapter_by_title["chapter3"]["stages"]["context"], "skipped")

    def test_write_workflow_report_does_not_treat_retrieval_memory_as_summary_done(self):
        output_dir = os.path.join(self.test_temp_dir, "memory_entry_not_summary_output")
        os.makedirs(output_dir, exist_ok=True)

        report_path = _write_workflow_report(
            "Book",
            output_dir,
            [
                {
                    "title": "chapter2",
                    "input_path": "input/chapter2.txt",
                    "output_path": "output/chapter2.txt",
                    "status": "pending",
                }
            ],
            {
                "processed_chapters": [{"title": "chapter2"}],
                "judge_results": [{"title": "chapter2", "pass_check": True}],
                "chapter_summaries": [
                    {
                        "title": "chapter1",
                        "content": "Old memory summary from retrieval",
                        "chapter_index": 1,
                    }
                ],
                "error": None,
            },
            {"input_dir": "input", "output_dir": "output"},
        )

        with open(report_path, encoding="utf-8") as report_file:
            report = json.load(report_file)

        chapter = report["chapters"][0]
        self.assertEqual(chapter["stages"]["translation"], "done")
        self.assertEqual(chapter["stages"]["summary"], "not_started")
        self.assertEqual(chapter["status"], "translated")
        self.assertEqual(report["failed_count"], 0)

    def test_write_workflow_report_runtime_context_failure_overrides_checkpoint_done(self):
        output_dir = os.path.join(self.test_temp_dir, "context_runtime_overrides_checkpoint_output")
        os.makedirs(output_dir, exist_ok=True)

        report_path = _write_workflow_report(
            "Book",
            output_dir,
            [
                {
                    "title": "chapter1",
                    "input_path": "input/chapter1.txt",
                    "output_path": "output/chapter1.txt",
                }
            ],
            {
                "processed_chapters": [{"title": "chapter1"}],
                "chapter_runs": {
                    "chapter1": {
                        "stages": {"context_retrieved": "done"},
                        "error": None,
                    }
                },
                "context_errors": [{"title": "chapter1", "error": "read failed"}],
                "error": "Context retrieval failed for chapter 'chapter1': read failed",
            },
            {"input_dir": "input", "output_dir": "output"},
        )

        with open(report_path, encoding="utf-8") as report_file:
            report = json.load(report_file)

        chapter = report["chapters"][0]
        self.assertEqual(chapter["status"], "failed")
        self.assertEqual(chapter["stages"]["context"], "failed")

    def test_write_workflow_report_runtime_summary_failure_overrides_checkpoint_done(self):
        output_dir = os.path.join(self.test_temp_dir, "summary_runtime_overrides_checkpoint_output")
        os.makedirs(output_dir, exist_ok=True)

        report_path = _write_workflow_report(
            "Book",
            output_dir,
            [
                {
                    "title": "chapter1",
                    "input_path": "input/chapter1.txt",
                    "output_path": "output/chapter1.txt",
                }
            ],
            {
                "processed_chapters": [{"title": "chapter1"}],
                "chapter_runs": {
                    "chapter1": {
                        "stages": {"summary_done": "done"},
                        "error": None,
                    }
                },
                "summary_errors": [{"title": "chapter1", "error": "invalid summary json"}],
                "error": "Summary failed for chapter 'chapter1': invalid summary json",
            },
            {"input_dir": "input", "output_dir": "output"},
        )

        with open(report_path, encoding="utf-8") as report_file:
            report = json.load(report_file)

        chapter = report["chapters"][0]
        self.assertEqual(chapter["status"], "failed")
        self.assertEqual(chapter["stages"]["summary"], "failed")

    def test_write_workflow_report_infers_analysis_stage_failure_from_workflow_error(self):
        output_dir = os.path.join(self.test_temp_dir, "analysis_error_fallback_report_output")
        os.makedirs(output_dir, exist_ok=True)

        report_path = _write_workflow_report(
            "Book",
            output_dir,
            [
                {
                    "title": "chapter1",
                    "input_path": "input/chapter1.txt",
                    "output_path": "output/chapter1.txt",
                }
            ],
            {
                "processed_chapters": [{"title": "chapter1"}],
                "analysis_errors": [],
                "error": "Analysis failed for chapter 'chapter1': malformed response",
            },
            {"input_dir": "input", "output_dir": "output"},
        )

        with open(report_path, encoding="utf-8") as report_file:
            report = json.load(report_file)

        chapter = report["chapters"][0]
        self.assertEqual(chapter["status"], "failed")
        self.assertEqual(chapter["stages"]["analysis"], "failed")

    def test_write_workflow_report_surfaces_dictionary_conflicts(self):
        output_dir = os.path.join(self.test_temp_dir, "dictionary_conflict_report_output")
        os.makedirs(output_dir, exist_ok=True)

        conflict = {
            "status": "conflict",
            "english_term": "Spirit Lotus",
            "existing_russian_term": "Духовный лотос",
            "candidate_russian_term": "Духовный лотос / Небесный лотос",
            "source_chapter": "chapter1",
            "reason": "QA synonym update",
        }

        report_path = _write_workflow_report(
            "Book",
            output_dir,
            [
                {
                    "title": "chapter1",
                    "input_path": "input/chapter1.txt",
                    "output_path": "output/chapter1.txt",
                }
            ],
            {
                "processed_chapters": [
                    {"title": "chapter1", "dictionary_conflicts": [conflict]}
                ],
                "judge_results": [
                    {
                        "title": "chapter1",
                        "pass_check": True,
                        "dictionary_conflicts": [conflict],
                    }
                ],
                "dictionary_conflicts": [conflict],
                "error": None,
            },
            {"input_dir": "input", "output_dir": "output"},
        )

        with open(report_path, encoding="utf-8") as report_file:
            report = json.load(report_file)

        chapter = report["chapters"][0]
        self.assertEqual(chapter["dictionary_conflicts"], [conflict])
        self.assertIn("Dictionary conflict", chapter["warnings"][0])
        self.assertEqual(report["dictionary_conflicts"], [conflict])

    @patch("src.Perevod.graph_runner.DatabaseManager")
    @patch("src.Perevod.graph_runner.KnowledgeBaseManager")
    @patch("src.Perevod.graph_runner.LLMProvider")
    @patch("src.Perevod.graph_runner.build_graph")
    def test_report_marks_analysis_errors_as_failed(
        self,
        mock_build_graph,
        mock_llm_provider,
        mock_kb_manager,
        mock_db_manager,
    ):
        mock_app = MagicMock()
        mock_app.invoke.return_value = {
            "processed_chapters": [{"title": "chapter1"}],
            "analysis_errors": [{"title": "chapter1", "error": "quota exhausted"}],
            "judge_results": [{"title": "chapter1", "pass_check": True}],
            "chapter_summaries": [{"title": "chapter1"}],
            "summary_errors": [],
            "error": None,
        }
        mock_build_graph.return_value = mock_app

        mock_kb = MagicMock()
        mock_kb.collection.count.return_value = 0
        mock_kb_manager.return_value = mock_kb

        project_name = "test_project_analysis_warning_report"
        input_dir = os.path.join(self.test_temp_dir, project_name, "input")
        output_dir = os.path.join(self.test_temp_dir, project_name, "output")
        os.makedirs(input_dir, exist_ok=True)
        os.makedirs(output_dir, exist_ok=True)

        with open(os.path.join(input_dir, "chapter1.txt"), "w", encoding="utf-8") as f:
            f.write("This is a test chapter.")

        project_settings = {
            "input_dir": input_dir,
            "output_dir": output_dir,
            "GOOGLE_API_KEY": "AIza-real-looking-key",
        }

        with self.assertRaisesRegex(RuntimeError, "Analysis failed"):
            run_translation_workflow(project_name, project_settings)

        with open(
            os.path.join(output_dir, "translation_report.json"),
            encoding="utf-8",
        ) as report_file:
            report = json.load(report_file)

        self.assertEqual(report["warning_count"], 1)
        self.assertEqual(report["failed_count"], 1)
        self.assertEqual(report["chapters"][0]["status"], "failed")
        self.assertEqual(report["chapters"][0]["stages"]["analysis"], "failed")
        self.assertIn("quota exhausted", report["chapters"][0]["error"])
        self.assertIn("quota exhausted", report["chapters"][0]["warnings"][0])

    @patch("src.Perevod.graph_runner.DatabaseManager")
    @patch("src.Perevod.graph_runner.KnowledgeBaseManager")
    @patch("src.Perevod.graph_runner.LLMProvider")
    @patch("src.Perevod.graph_runner.build_graph")
    def test_report_marks_global_context_errors_as_failed(
        self,
        mock_build_graph,
        mock_llm_provider,
        mock_kb_manager,
        mock_db_manager,
    ):
        mock_app = MagicMock()
        mock_app.invoke.return_value = {
            "processed_chapters": [{"title": "chapter1"}],
            "context_errors": [{"title": "*", "error": "memory db locked"}],
            "analysis_errors": [],
            "judge_results": [{"title": "chapter1", "pass_check": True}],
            "chapter_summaries": [{"title": "chapter1"}],
            "summary_errors": [],
            "error": None,
        }
        mock_build_graph.return_value = mock_app

        mock_kb = MagicMock()
        mock_kb.collection.count.return_value = 0
        mock_kb_manager.return_value = mock_kb

        project_name = "test_project_context_warning_report"
        input_dir = os.path.join(self.test_temp_dir, project_name, "input")
        output_dir = os.path.join(self.test_temp_dir, project_name, "output")
        os.makedirs(input_dir, exist_ok=True)
        os.makedirs(output_dir, exist_ok=True)

        with open(os.path.join(input_dir, "chapter1.txt"), "w", encoding="utf-8") as f:
            f.write("This is a test chapter.")

        project_settings = {
            "input_dir": input_dir,
            "output_dir": output_dir,
            "GOOGLE_API_KEY": "AIza-real-looking-key",
        }

        with self.assertRaisesRegex(RuntimeError, "Context retrieval failed"):
            run_translation_workflow(project_name, project_settings)

        with open(
            os.path.join(output_dir, "translation_report.json"),
            encoding="utf-8",
        ) as report_file:
            report = json.load(report_file)

        self.assertEqual(report["warning_count"], 1)
        self.assertEqual(report["failed_count"], 1)
        self.assertEqual(report["chapters"][0]["status"], "failed")
        self.assertEqual(report["chapters"][0]["stages"]["context"], "failed")
        self.assertIn("memory db locked", report["chapters"][0]["error"])
        self.assertIn("memory db locked", report["chapters"][0]["warnings"][0])

    def test_judge_resume_does_not_bypass_qa_for_previously_failed_chapter(self):
        from Perevod.graph_runner import _should_retry_checkpoint_run
        
        # Сценарий 1: Чекпоинт имеет статус завершенного, но judge_result.pass_check = False
        run_failed = {
            "status": "qa_failed",
            "stages": {
                "translation_done": "done",
                "judge_done": "done",
            },
            "judge_result": {
                "pass_check": False,
                "blocking_issues": ["Style mismatch"],
            }
        }
        
        # Должен возвращать True, так как QA не пройден
        self.assertTrue(_should_retry_checkpoint_run(run_failed, include_incomplete=True))
        
        # Сценарий 2: Чекпоинт успешен (pass_check = True)
        run_passed = {
            "status": "completed",
            "stages": {
                "translation_done": "done",
                "judge_done": "done",
                "summary_done": "done",
                "memory_updated": "done",
            },
            "judge_result": {
                "pass_check": True,
            }
        }
        
        # Не должен возвращать True при include_incomplete=False
        self.assertFalse(_should_retry_checkpoint_run(run_passed, include_incomplete=False))
