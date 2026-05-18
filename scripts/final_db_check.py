import argparse
import os
import sys
from pathlib import Path

from sqlalchemy import create_engine, text

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from Perevod.config import PROJECT_ROOT

DB_PATH = os.path.join(PROJECT_ROOT, "_project_files", "projects_main.db")


def find_project(db_path: str, project_name: str) -> dict | None:
    engine = create_engine(f"sqlite:///{db_path}")
    with engine.connect() as connection:
        row = (
            connection.execute(
                text(
                    "SELECT id, name, settings_json "
                    "FROM projects "
                    "WHERE name = :name"
                ),
                {"name": project_name},
            )
            .mappings()
            .first()
        )
        if not row:
            return None
        return {
            "id": row["id"],
            "name": row["name"],
            "settings_json": row["settings_json"],
        }


def final_check(*, db_path: str = DB_PATH, project_name: str) -> int:
    print(f"Database: {db_path}")
    if not os.path.exists(db_path):
        print("ERROR: database file not found.")
        return 2

    project = find_project(db_path, project_name)
    if not project:
        print(f"Project '{project_name}' was not found.")
        return 1

    print(f"Project found: ID={project['id']} name='{project['name']}'")
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Checks one project row by name.")
    parser.add_argument("project_name")
    parser.add_argument("--db-path", default=DB_PATH)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    return final_check(db_path=args.db_path, project_name=args.project_name)


if __name__ == "__main__":
    raise SystemExit(main())
