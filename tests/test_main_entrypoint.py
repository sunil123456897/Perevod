import sys
import types
from unittest.mock import MagicMock

import pytest

from Perevod import main as app_main


def test_main_doctor_mode_forwards_cli_options(monkeypatch):
    doctor_main = MagicMock(return_value=7)
    monkeypatch.setitem(
        sys.modules,
        "Perevod.doctor",
        types.SimpleNamespace(main=doctor_main),
    )
    monkeypatch.setattr(app_main, "setup_logging", MagicMock())

    result = app_main.main(
        [
            "--doctor",
            "--project",
            "Book",
            "--input-dir",
            "input",
            "--output-dir",
            "output",
            "--check-api",
            "--api-timeout",
            "33",
        ]
    )

    assert result == 7
    doctor_main.assert_called_once_with(
        [
            "--project",
            "Book",
            "--input-dir",
            "input",
            "--output-dir",
            "output",
            "--check-api",
            "--api-timeout",
            "33",
        ]
    )


def test_main_cli_mode_requires_project(monkeypatch):
    monkeypatch.setattr(app_main, "setup_logging", MagicMock())

    with pytest.raises(SystemExit) as exc_info:
        app_main.main(["--cli"])

    assert exc_info.value.code == 2


def test_main_cli_mode_passes_retry_and_io_overrides(monkeypatch):
    run_cli_translation = MagicMock(return_value=0)
    monkeypatch.setitem(
        sys.modules,
        "Perevod.cli",
        types.SimpleNamespace(run_cli_translation=run_cli_translation),
    )
    monkeypatch.setattr(app_main, "setup_logging", MagicMock())

    result = app_main.main(
        [
            "--cli",
            "--project",
            "Book",
            "--input-dir",
            "input",
            "--output-dir",
            "output",
            "--overwrite-existing",
            "--retry-incomplete",
        ]
    )

    assert result == 0
    run_cli_translation.assert_called_once_with(
        "Book",
        {
            "input_dir": "input",
            "output_dir": "output",
            "overwrite_existing": True,
            "retry_incomplete": True,
        },
    )


def test_main_gui_mode_starts_translator_gui(monkeypatch):
    app = MagicMock()
    translator_gui = MagicMock(return_value=app)
    monkeypatch.setitem(
        sys.modules,
        "Perevod.gui.main_window",
        types.SimpleNamespace(TranslatorGUI=translator_gui),
    )
    monkeypatch.setattr(app_main, "setup_logging", MagicMock())

    result = app_main.main(["--project", "Book"])

    assert result == 0
    translator_gui.assert_called_once()
    assert translator_gui.call_args.kwargs["cli_args"].project == "Book"
    app.mainloop.assert_called_once_with()
