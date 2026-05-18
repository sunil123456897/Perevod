import sqlite3

from scripts.final_db_check import find_project, final_check
from scripts.view_projects import list_projects, view_all_projects


def _create_projects_db(db_path):
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "CREATE TABLE projects (id INTEGER PRIMARY KEY, name TEXT, settings_json TEXT)"
        )
        conn.executemany(
            "INSERT INTO projects (id, name, settings_json) VALUES (?, ?, ?)",
            [
                (2, "Fermer", '{"input_dir": "input"}'),
                (5, "Default", "{}"),
            ],
        )


def test_final_db_check_finds_project_without_full_orm_schema(tmp_path, capsys):
    db_path = tmp_path / "projects_main.db"
    _create_projects_db(db_path)

    project = find_project(str(db_path), "Fermer")
    result = final_check(db_path=str(db_path), project_name="Fermer")

    assert project == {
        "id": 2,
        "name": "Fermer",
        "settings_json": '{"input_dir": "input"}',
    }
    assert result == 0
    assert "Project found: ID=2 name='Fermer'" in capsys.readouterr().out


def test_final_db_check_reports_missing_project(tmp_path):
    db_path = tmp_path / "projects_main.db"
    _create_projects_db(db_path)

    assert find_project(str(db_path), "Missing") is None
    assert final_check(db_path=str(db_path), project_name="Missing") == 1


def test_view_projects_lists_projects_without_mutating_schema(tmp_path, capsys):
    db_path = tmp_path / "projects_main.db"
    _create_projects_db(db_path)

    projects = list_projects(str(db_path))
    result = view_all_projects(db_path=str(db_path))

    assert projects == [
        {"id": 2, "name": "Fermer", "settings_len": 22},
        {"id": 5, "name": "Default", "settings_len": 2},
    ]
    assert result == 0
    output = capsys.readouterr().out
    assert "Fermer" in output
    assert "Default" in output
