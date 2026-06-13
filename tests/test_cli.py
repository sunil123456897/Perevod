from unittest.mock import MagicMock

from Perevod import cli


def test_run_cli_translation_loads_project_settings_and_reports_success(monkeypatch):
    project_manager = MagicMock()
    project_manager.get_project_settings.return_value = {
        "input_dir": "in",
        "output_dir": "out",
        "GOOGLE_API_KEY": "test-key",
    }
    monkeypatch.setattr(cli, "ProjectManager", lambda: project_manager)

    workflow = MagicMock(
        return_value={
            "processed_chapters": [{"title": "chapter1"}],
            "error": None,
            "report_path": "out/translation_report.json",
        }
    )
    monkeypatch.setattr(cli, "run_translation_workflow", workflow)
    info = MagicMock()
    monkeypatch.setattr(cli.logger, "info", info)

    exit_code = cli.run_cli_translation("Book")

    assert exit_code == 0
    project_manager.get_project_settings.assert_called_once_with("Book")
    workflow.assert_called_once()
    assert workflow.call_args.args[:2] == ("Book", project_manager.get_project_settings.return_value)
    assert any("translation_report.json" in str(call.args) for call in info.call_args_list)


def test_run_cli_translation_applies_cli_overrides(monkeypatch):
    project_manager = MagicMock()
    project_manager.get_project_settings.return_value = {
        "input_dir": "old-in",
        "output_dir": "old-out",
        "overwrite_existing": False,
    }
    monkeypatch.setattr(cli, "ProjectManager", lambda: project_manager)

    workflow = MagicMock(return_value={"processed_chapters": [], "error": None})
    monkeypatch.setattr(cli, "run_translation_workflow", workflow)

    exit_code = cli.run_cli_translation(
        "Book",
        {
            "input_dir": "new-in",
            "output_dir": "new-out",
            "overwrite_existing": True,
        },
    )

    assert exit_code == 0
    assert workflow.call_args.args[1]["input_dir"] == "new-in"
    assert workflow.call_args.args[1]["output_dir"] == "new-out"
    assert workflow.call_args.args[1]["overwrite_existing"] is True


def test_run_cli_translation_returns_error_code_when_workflow_fails(monkeypatch):
    monkeypatch.setattr(cli, "ProjectManager", lambda: MagicMock())
    monkeypatch.setattr(cli, "run_translation_workflow", MagicMock(side_effect=ValueError("bad settings")))

    assert cli.run_cli_translation("Book") == 1


def test_run_cli_translation_logs_report_path_when_workflow_returns_error(monkeypatch):
    project_manager = MagicMock()
    project_manager.get_project_settings.return_value = {
        "input_dir": "in",
        "output_dir": "out",
        "GOOGLE_API_KEY": "test-key",
    }
    monkeypatch.setattr(cli, "ProjectManager", lambda: project_manager)
    monkeypatch.setattr(
        cli,
        "run_translation_workflow",
        MagicMock(
            return_value={
                "processed_chapters": [{"title": "chapter1"}],
                "error": "Summary failed for chapter 'chapter1'",
                "report_path": "out/translation_report.json",
            }
        ),
    )
    info = MagicMock()
    monkeypatch.setattr(cli.logger, "info", info)

    assert cli.run_cli_translation("Book") == 1
    assert any("translation_report.json" in str(call.args) for call in info.call_args_list)


def test_run_cli_translation_logs_existing_report_path_when_workflow_raises(
    monkeypatch, tmp_path
):
    output_dir = tmp_path / "out"
    output_dir.mkdir()
    report_path = output_dir / "translation_report.json"
    report_path.write_text("{}", encoding="utf-8")
    project_manager = MagicMock()
    project_manager.get_project_settings.return_value = {
        "input_dir": "in",
        "output_dir": str(output_dir),
        "GOOGLE_API_KEY": "test-key",
    }
    monkeypatch.setattr(cli, "ProjectManager", lambda: project_manager)
    monkeypatch.setattr(
        cli,
        "run_translation_workflow",
        MagicMock(side_effect=RuntimeError("Summary failed")),
    )
    info = MagicMock()
    monkeypatch.setattr(cli.logger, "info", info)

    assert cli.run_cli_translation("Book") == 1
    assert any(
        len(call.args) > 1 and call.args[1] == str(report_path)
        for call in info.call_args_list
    )


def test_main_cli_mode_uses_cli_runner(monkeypatch):
    called = {}

    def fake_run_cli_translation(project_name, overrides=None):
        called["project"] = project_name
        called["overrides"] = overrides
        return 0

    monkeypatch.setattr(cli, "run_cli_translation", fake_run_cli_translation)

    assert cli.main(["--project", "Book", "--input-dir", "in", "--output-dir", "out"]) == 0
    assert called == {
        "project": "Book",
        "overrides": {"input_dir": "in", "output_dir": "out"},
    }


def test_main_passes_retry_incomplete_override(monkeypatch):
    called = {}

    def fake_run_cli_translation(project_name, overrides=None):
        called["project"] = project_name
        called["overrides"] = overrides
        return 0

    monkeypatch.setattr(cli, "run_cli_translation", fake_run_cli_translation)

    assert cli.main(["--project", "Book", "--retry-incomplete"]) == 0
    assert called == {
        "project": "Book",
        "overrides": {"retry_incomplete": True},
    }
