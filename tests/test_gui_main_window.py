import threading
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
        }
    )
    thread_instance = MagicMock()
    thread = MagicMock(return_value=thread_instance)
    monkeypatch.setattr(main_window.threading, "Thread", thread)

    gui.start_translation()
    gui.start_translation()

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
    gui.project_combo = MagicMock()

    gui.update_ui_for_translation_state(True)

    gui.start_button.configure.assert_called_once()
    start_kwargs = gui.start_button.configure.call_args.kwargs
    assert start_kwargs["state"] == main_window.ctk.DISABLED
    assert start_kwargs["text"] == "Перевод идет..."


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
