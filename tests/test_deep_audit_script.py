import sqlite3

from scripts.deep_audit import (
    choose_duplicate_ids_to_delete,
    delete_projects,
    find_duplicate_projects,
)


def test_deep_audit_finds_and_deletes_duplicate_project_rows(tmp_path):
    db_path = tmp_path / "projects_main.db"
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "CREATE TABLE projects (id INTEGER PRIMARY KEY, name TEXT, settings_json TEXT)"
        )
        conn.executemany(
            "INSERT INTO projects (id, name, settings_json) VALUES (?, ?, ?)",
            [
                (1, "Fermer", "{}"),
                (2, "Fermer", '{"input_dir": "input", "output_dir": "output"}'),
                (3, "Other", "{}"),
            ],
        )

    duplicates = find_duplicate_projects(str(db_path))
    ids_to_delete = choose_duplicate_ids_to_delete(duplicates)
    delete_projects(str(db_path), ids_to_delete)

    assert sorted(duplicates) == ["Fermer"]
    assert ids_to_delete == [1]
    with sqlite3.connect(db_path) as conn:
        rows = conn.execute("SELECT id, name FROM projects ORDER BY id").fetchall()
    assert rows == [(2, "Fermer"), (3, "Other")]
