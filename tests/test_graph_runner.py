# tests/test_graph_runner.py
import unittest
from unittest.mock import patch, MagicMock
import os
import shutil
import json
import tempfile

# Устанавливаем переменную окружения для тестов
os.environ["GOOGLE_API_KEY"] = "AIza-real-looking-key"

from src.Perevod.graph_runner import run_translation_workflow, _write_workflow_report
from Perevod.config import PROJECT_ROOT


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
            "gemini-3-flash-preview",
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
            {"translation": "done", "judge": "done", "summary": "done"},
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
        self.assertEqual(statuses["chapter1"], "translated")
        self.assertEqual(statuses["chapter2"], "failed")
        self.assertEqual(stages["chapter1"]["translation"], "done")
        self.assertEqual(stages["chapter1"]["judge"], "not_started")
        self.assertEqual(stages["chapter2"]["translation"], "failed")
        self.assertIn("translation failed", report["error"])

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
        self.assertEqual(statuses["chapter2"], "translated")
        self.assertEqual(statuses["chapter3"], "skipped_not_failed")
        self.assertEqual(report["chapters"][0]["stages"]["translation"], "skipped")
        self.assertFalse(reused["chapter1"])
        self.assertTrue(reused["chapter2"])
        self.assertFalse(reused["chapter3"])

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
        self.assertEqual(chapter["stages"]["judge"], "failed")
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
        self.assertIn("QA failed after refinement limit", report["error"])

    @patch("src.Perevod.graph_runner.DatabaseManager")
    @patch("src.Perevod.graph_runner.KnowledgeBaseManager")
    @patch("src.Perevod.graph_runner.LLMProvider")
    @patch("src.Perevod.graph_runner.build_graph")
    def test_report_surfaces_non_fatal_summary_errors(
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

        run_translation_workflow(project_name, project_settings)

        with open(
            os.path.join(output_dir, "translation_report.json"),
            encoding="utf-8",
        ) as report_file:
            report = json.load(report_file)

        self.assertEqual(report["warning_count"], 1)
        self.assertEqual(report["chapters"][0]["status"], "translated")
        self.assertEqual(report["chapters"][0]["stages"]["summary"], "failed")
        self.assertIn("invalid summary json", report["chapters"][0]["warnings"][0])

    @patch("src.Perevod.graph_runner.DatabaseManager")
    @patch("src.Perevod.graph_runner.KnowledgeBaseManager")
    @patch("src.Perevod.graph_runner.LLMProvider")
    @patch("src.Perevod.graph_runner.build_graph")
    def test_report_surfaces_non_fatal_analysis_errors(
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

        run_translation_workflow(project_name, project_settings)

        with open(
            os.path.join(output_dir, "translation_report.json"),
            encoding="utf-8",
        ) as report_file:
            report = json.load(report_file)

        self.assertEqual(report["warning_count"], 1)
        self.assertEqual(report["chapters"][0]["status"], "translated")
        self.assertIn("quota exhausted", report["chapters"][0]["warnings"][0])

    @patch("src.Perevod.graph_runner.DatabaseManager")
    @patch("src.Perevod.graph_runner.KnowledgeBaseManager")
    @patch("src.Perevod.graph_runner.LLMProvider")
    @patch("src.Perevod.graph_runner.build_graph")
    def test_report_surfaces_non_fatal_context_errors(
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

        run_translation_workflow(project_name, project_settings)

        with open(
            os.path.join(output_dir, "translation_report.json"),
            encoding="utf-8",
        ) as report_file:
            report = json.load(report_file)

        self.assertEqual(report["warning_count"], 1)
        self.assertEqual(report["chapters"][0]["status"], "translated")
        self.assertIn("memory db locked", report["chapters"][0]["warnings"][0])
