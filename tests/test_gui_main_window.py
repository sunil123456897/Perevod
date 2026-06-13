import threading
import json
from unittest.mock import MagicMock

from Perevod.gui import main_window


class DummyVar:
    def __init__(self, value):
        self.value = value

    def get(self):
        return self.value


def test_start_translation_rejects_placeholder_api_key_before_thread(monkeypatch):
    gui = main_window.TranslatorGUI.__new__(main_window.TranslatorGUI)
    gui.translation_running = False
    gui.get_current_settings_from_ui = MagicMock(
        return_value={
            "GOOGLE_API_KEY": "test_api_key",
            "input_dir": "input",
            "output_dir": "output",
        }
    )
    showerror = MagicMock()
    thread = MagicMock()
    monkeypatch.setattr(main_window.messagebox, "showerror", showerror)
    monkeypatch.setattr(main_window.threading, "Thread", thread)

    gui.start_translation()

    thread.assert_not_called()
    showerror.assert_called_once()
    assert "test/fake" in showerror.call_args.args[1]


def test_start_translation_marks_running_before_thread_start(monkeypatch):
    gui = main_window.TranslatorGUI.__new__(main_window.TranslatorGUI)
    gui.translation_running = False
    gui.get_current_settings_from_ui = MagicMock(
        return_value={
            "GOOGLE_API_KEY": "AIza-real-looking-key",
            "input_dir": "input",
            "output_dir": "output",
            "overwrite_existing": False,
        }
    )
    thread_instance = MagicMock()
    thread = MagicMock(return_value=thread_instance)
    monkeypatch.setattr(main_window.threading, "Thread", thread)

    gui.start_translation()
    gui.start_translation()

    thread.assert_called_once()
    thread_instance.start.assert_called_once()


def test_start_translation_requires_confirmation_for_overwrite(monkeypatch):
    gui = main_window.TranslatorGUI.__new__(main_window.TranslatorGUI)
    gui.translation_running = False
    gui.get_current_settings_from_ui = MagicMock(
        return_value={
            "GOOGLE_API_KEY": "AIza-real-looking-key",
            "input_dir": "input",
            "output_dir": "output",
            "overwrite_existing": True,
        }
    )
    gui.update_progress = MagicMock()
    askyesno = MagicMock(return_value=False)
    thread = MagicMock()
    monkeypatch.setattr(main_window.messagebox, "askyesno", askyesno)
    monkeypatch.setattr(main_window.threading, "Thread", thread)

    gui.start_translation()

    askyesno.assert_called_once()
    thread.assert_not_called()
    gui.update_progress.assert_called_once_with(
        0,
        "Перевод отменен: перезапись не подтверждена.",
    )


def test_start_translation_allows_confirmed_overwrite(monkeypatch):
    gui = main_window.TranslatorGUI.__new__(main_window.TranslatorGUI)
    gui.translation_running = False
    gui.get_current_settings_from_ui = MagicMock(
        return_value={
            "GOOGLE_API_KEY": "AIza-real-looking-key",
            "input_dir": "input",
            "output_dir": "output",
            "overwrite_existing": True,
        }
    )
    askyesno = MagicMock(return_value=True)
    thread_instance = MagicMock()
    thread = MagicMock(return_value=thread_instance)
    monkeypatch.setattr(main_window.messagebox, "askyesno", askyesno)
    monkeypatch.setattr(main_window.threading, "Thread", thread)

    gui.start_translation()

    askyesno.assert_called_once()
    thread.assert_called_once()
    thread_instance.start.assert_called_once()


def test_update_ui_for_translation_state_disables_start_button_while_running():
    gui = main_window.TranslatorGUI.__new__(main_window.TranslatorGUI)
    gui.start_button = MagicMock()
    gui.init_button = MagicMock()
    gui.dict_button = MagicMock()
    gui.bible_button = MagicMock()
    gui.build_index_button = MagicMock()
    gui.quarantine_button = MagicMock()
    gui.diag_button = MagicMock()
    gui.report_button = MagicMock()
    gui.project_combo = MagicMock()

    gui.update_ui_for_translation_state(True)

    gui.start_button.configure.assert_called_once()
    start_kwargs = gui.start_button.configure.call_args.kwargs
    assert start_kwargs["state"] == main_window.ctk.DISABLED
    assert start_kwargs["text"] == "Перевод идет..."


def test_format_translation_report_details_lists_problem_chapters(tmp_path):
    report_path = tmp_path / "translation_report.json"
    report_path.write_text(
        json.dumps(
            {
                "total_chapters": 2,
                "processed_count": 1,
                "failed_count": 1,
                "warning_count": 1,
                "chapters": [
                    {"title": "ok", "status": "translated", "warnings": []},
                    {
                        "title": "bad",
                        "status": "failed",
                        "stages": {"judge": "failed"},
                        "blocking_issues": ["term missing"],
                        "warnings": ["judge failed"],
                        "error": "invalid judge json",
                        "error_category": "auth",
                        "error_retryable": False,
                        "error_status_code": 401,
                        "error_operation": "generateContent",
                        "error_model": "gemini-3-flash-preview",
                    },
                ],
            }
        ),
        encoding="utf-8",
    )

    details = main_window.format_translation_report_details(str(report_path))

    assert "Сводка: всего 2" in details
    assert "Отчет:" in details
    assert "- bad: failed" in details
    assert "failed stages: judge" in details
    assert "blocking: term missing" in details
    assert "warnings: judge failed" in details
    assert (
        "api: auth, status 401, retryable False, "
        "op generateContent, model gemini-3-flash-preview"
    ) in details
    assert "error: invalid judge json" in details


def test_format_translation_report_details_lists_context_warnings(tmp_path):
    report_path = tmp_path / "translation_report.json"
    report_path.write_text(
        json.dumps(
            {
                "total_chapters": 1,
                "processed_count": 1,
                "failed_count": 0,
                "chapters": [
                    {
                        "title": "chapter1",
                        "status": "translated",
                        "warnings": [],
                        "context_warnings": [
                            {
                                "scope": "semantic_lore",
                                "error": "embedding quota exhausted",
                            }
                        ],
                    },
                ],
            }
        ),
        encoding="utf-8",
    )

    details = main_window.format_translation_report_details(str(report_path))

    assert "- chapter1: translated" in details
    assert "context warnings: semantic_lore: embedding quota exhausted" in details


def test_format_translation_report_details_handles_missing_report(tmp_path):
    assert (
        main_window.format_translation_report_details(str(tmp_path / "missing.json"))
        == "translation_report.json не найден."
    )


def test_format_workflow_progress_includes_stage_and_chapter_count():
    percent, text = main_window.format_workflow_progress(
        "translation",
        2,
        4,
        "Глава 2 готова",
    )

    assert percent == 50
    assert text == "Перевод: 2/4 - Глава 2 готова"


def test_update_workflow_progress_adapts_workflow_callback_to_gui_progress():
    gui = main_window.TranslatorGUI.__new__(main_window.TranslatorGUI)
    gui.after = lambda _delay, callback, *args: callback(*args)
    gui.update_progress = MagicMock()

    gui.update_workflow_progress("judge", 1, 2, "Проверка главы")

    gui.update_progress.assert_called_once_with(50, "Judge: 1/2 - Проверка главы")


def test_summarize_translation_report_includes_counts_and_problem_titles(tmp_path):
    report_path = tmp_path / "translation_report.json"
    report_path.write_text(
        json.dumps(
            {
                "total_chapters": 3,
                "processed_count": 2,
                "failed_count": 2,
                "warning_count": 1,
                "chapters": [
                    {"title": "chapter1", "status": "translated", "warnings": []},
                    {"title": "chapter2", "status": "failed", "warnings": ["err"]},
                    {"title": "chapter3", "status": "qa_failed", "warnings": []},
                ],
            }
        ),
        encoding="utf-8",
    )

    summary = main_window.summarize_translation_report(str(report_path))

    assert "всего 3" in summary
    assert "обработано 2" in summary
    assert "failed 2" in summary
    assert "qa_failed 1" in summary
    assert "warnings 1" in summary
    assert "chapter2, chapter3" in summary


def test_summarize_translation_report_counts_context_warnings(tmp_path):
    report_path = tmp_path / "translation_report.json"
    report_path.write_text(
        json.dumps(
            {
                "total_chapters": 1,
                "processed_count": 1,
                "failed_count": 0,
                "chapters": [
                    {
                        "title": "chapter1",
                        "status": "translated",
                        "warnings": [],
                        "context_warnings": [
                            {
                                "scope": "semantic_lore",
                                "error": "embedding quota exhausted",
                            }
                        ],
                    },
                ],
            }
        ),
        encoding="utf-8",
    )

    summary = main_window.summarize_translation_report(str(report_path))

    assert "warnings 1" in summary


def test_summarize_translation_report_does_not_hide_context_warnings_with_stale_count(tmp_path):
    report_path = tmp_path / "translation_report.json"
    report_path.write_text(
        json.dumps(
            {
                "total_chapters": 1,
                "processed_count": 1,
                "failed_count": 0,
                "warning_count": 0,
                "chapters": [
                    {
                        "title": "chapter1",
                        "status": "translated",
                        "warnings": [],
                        "context_warnings": [
                            {
                                "scope": "semantic_lore",
                                "error": "embedding quota exhausted",
                            }
                        ],
                    },
                ],
            }
        ),
        encoding="utf-8",
    )

    summary = main_window.summarize_translation_report(str(report_path))

    assert "warnings 1" in summary


def test_summarize_translation_report_returns_empty_for_missing_file(tmp_path):
    assert main_window.summarize_translation_report(str(tmp_path / "missing.json")) == ""


def test_join_status_parts_avoids_double_periods():
    assert main_window.join_status_parts("Готово.", "Сводка.", "Отчет: path") == (
        "Готово. Сводка. Отчет: path"
    )


def test_translation_thread_uses_workflow_progress_adapter_and_shows_report(monkeypatch):
    gui = main_window.TranslatorGUI.__new__(main_window.TranslatorGUI)
    gui.translation_running = False
    gui.after = lambda _delay, callback, *args: callback(*args)
    gui.update_ui_for_translation_state = MagicMock()
    gui.update_progress = MagicMock()
    gui.get_current_settings_from_ui = MagicMock(
        return_value={
            "project_name": "Book",
            "GOOGLE_API_KEY": "AIza-real-looking-key",
            "input_dir": "input",
            "output_dir": "output",
        }
    )
    workflow = MagicMock(
        return_value={
            "processed_chapters": [{"title": "chapter1"}],
            "report_path": "output/translation_report.json",
        }
    )
    monkeypatch.setattr(main_window, "run_translation_workflow", workflow)
    monkeypatch.setattr(main_window, "summarize_translation_report", lambda _path: "")

    gui._translation_thread()

    workflow.assert_called_once()
    assert workflow.call_args.kwargs["progress_callback"] == gui.update_workflow_progress
    gui.update_progress.assert_any_call(
        100,
        "Перевод и аудит успешно завершены! "
        "Обработано глав: 1. Отчет: output/translation_report.json",
    )


def test_translation_thread_appends_report_summary(monkeypatch, tmp_path):
    report_path = tmp_path / "translation_report.json"
    report_path.write_text(
        json.dumps(
            {
                "total_chapters": 2,
                "processed_count": 1,
                "failed_count": 1,
                "warning_count": 1,
                "chapters": [
                    {"title": "chapter1", "status": "translated", "warnings": []},
                    {"title": "chapter2", "status": "failed", "warnings": ["err"]},
                ],
            }
        ),
        encoding="utf-8",
    )
    gui = main_window.TranslatorGUI.__new__(main_window.TranslatorGUI)
    gui.translation_running = False
    gui.after = lambda _delay, callback, *args: callback(*args)
    gui.update_ui_for_translation_state = MagicMock()
    gui.update_progress = MagicMock()
    gui.get_current_settings_from_ui = MagicMock(
        return_value={
            "project_name": "Book",
            "GOOGLE_API_KEY": "AIza-real-looking-key",
            "input_dir": "input",
            "output_dir": "output",
        }
    )
    workflow = MagicMock(
        return_value={
            "processed_chapters": [{"title": "chapter1"}],
            "report_path": str(report_path),
        }
    )
    monkeypatch.setattr(main_window, "run_translation_workflow", workflow)

    gui._translation_thread()

    status_text = gui.update_progress.call_args_list[-1].args[1]
    assert "Сводка: всего 2, обработано 1, failed 1, qa_failed 0, warnings 1." in status_text
    assert "Проблемные главы: chapter2." in status_text
    assert str(report_path) in status_text


def test_translation_thread_exception_path_shows_output_report(monkeypatch, tmp_path):
    output_dir = tmp_path / "output"
    output_dir.mkdir()
    report_path = output_dir / "translation_report.json"
    report_path.write_text(
        json.dumps(
            {
                "total_chapters": 1,
                "processed_count": 1,
                "failed_count": 1,
                "warning_count": 1,
                "chapters": [
                    {
                        "title": "chapter1",
                        "status": "failed",
                        "warnings": ["summary failed"],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    gui = main_window.TranslatorGUI.__new__(main_window.TranslatorGUI)
    gui.translation_running = False
    gui.after = lambda _delay, callback, *args: callback(*args)
    gui.update_ui_for_translation_state = MagicMock()
    gui.update_progress = MagicMock()
    gui.get_current_settings_from_ui = MagicMock(
        return_value={
            "project_name": "Book",
            "GOOGLE_API_KEY": "AIza-real-looking-key",
            "input_dir": "input",
            "output_dir": str(output_dir),
        }
    )
    monkeypatch.setattr(
        main_window,
        "run_translation_workflow",
        MagicMock(side_effect=RuntimeError("Summary failed")),
    )
    showerror = MagicMock()
    monkeypatch.setattr(main_window.messagebox, "showerror", showerror)

    gui._translation_thread()

    status_text = gui.update_progress.call_args_list[-1].args[1]
    error_dialog = showerror.call_args.args[1]
    assert str(report_path) in status_text
    assert str(report_path) in error_dialog
    assert "Сводка: всего 1, обработано 1, failed 1" in status_text


def test_show_translation_report_reads_output_dir_report(monkeypatch, tmp_path):
    output_dir = tmp_path / "out"
    output_dir.mkdir()
    report_path = output_dir / "translation_report.json"
    report_path.write_text(
        json.dumps({"chapters": [], "total_chapters": 0, "processed_count": 0}),
        encoding="utf-8",
    )
    gui = main_window.TranslatorGUI.__new__(main_window.TranslatorGUI)
    gui.get_current_settings_from_ui = MagicMock(
        return_value={"output_dir": str(output_dir)}
    )
    showinfo = MagicMock()
    monkeypatch.setattr(main_window.messagebox, "showinfo", showinfo)

    gui.show_translation_report()

    showinfo.assert_called_once()
    assert str(report_path) in showinfo.call_args.args[1]


def test_show_translation_report_requires_output_dir(monkeypatch):
    gui = main_window.TranslatorGUI.__new__(main_window.TranslatorGUI)
    gui.get_current_settings_from_ui = MagicMock(return_value={"output_dir": ""})
    showerror = MagicMock()
    monkeypatch.setattr(main_window.messagebox, "showerror", showerror)

    gui.show_translation_report()

    showerror.assert_called_once()


def test_initialize_thread_rejects_placeholder_api_key_before_managers(monkeypatch):
    gui = main_window.TranslatorGUI.__new__(main_window.TranslatorGUI)
    gui.init_lock = threading.Lock()
    gui.current_init_id = 1
    gui.settings_vars = {
        "GOOGLE_API_KEY": DummyVar("test_api_key"),
        "embedding_model_name": DummyVar("fake_model"),
    }
    gui.update_progress = MagicMock()
    gui._init_success = MagicMock()
    gui.after = lambda _delay, callback, *args: callback(*args)
    gui._init_failure = MagicMock()
    database_manager = MagicMock()
    knowledge_base_manager = MagicMock()
    monkeypatch.setattr(main_window, "DatabaseManager", database_manager)
    monkeypatch.setattr(main_window, "KnowledgeBaseManager", knowledge_base_manager)

    gui._initialize_thread("Book", 1)

    database_manager.assert_not_called()
    knowledge_base_manager.assert_not_called()
    gui._init_failure.assert_called_once()
    assert "test/fake" in str(gui._init_failure.call_args.args[0])
