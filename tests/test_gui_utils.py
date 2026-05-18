from unittest.mock import MagicMock

from Perevod.gui import utils


def test_open_file_in_editor_uses_windows_startfile(monkeypatch):
    startfile = MagicMock()
    monkeypatch.setattr(utils.platform, "system", lambda: "Windows")
    monkeypatch.setattr(utils.os, "startfile", startfile, raising=False)

    utils.open_file_in_editor("chapter.txt")

    startfile.assert_called_once_with("chapter.txt")


def test_open_file_in_editor_uses_xdg_open_on_linux(monkeypatch):
    run = MagicMock()
    monkeypatch.setattr(utils.platform, "system", lambda: "Linux")
    monkeypatch.setattr(utils.subprocess, "run", run)

    utils.open_file_in_editor("chapter.txt")

    run.assert_called_once_with(["xdg-open", "chapter.txt"], check=True)


def test_open_file_in_editor_reports_os_errors(monkeypatch):
    showerror = MagicMock()

    def fail_startfile(path):
        raise OSError("no association")

    monkeypatch.setattr(utils.platform, "system", lambda: "Windows")
    monkeypatch.setattr(utils.os, "startfile", fail_startfile, raising=False)
    monkeypatch.setattr(utils.messagebox, "showerror", showerror)

    utils.open_file_in_editor("chapter.txt")

    showerror.assert_called_once()
    assert "chapter.txt" in showerror.call_args.args[1]
    assert "no association" in showerror.call_args.args[1]
