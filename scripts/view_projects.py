import argparse
import os
import sys
from pathlib import Path

from sqlalchemy import create_engine, text

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from Perevod.config import PROJECT_ROOT

DB_PATH = os.path.join(PROJECT_ROOT, "_project_files", "projects_main.db")


def list_projects(db_path: str = DB_PATH) -> list[dict]:
    engine = create_engine(f"sqlite:///{db_path}")
    with engine.connect() as connection:
        result = connection.execute(
            text("SELECT id, name, settings_json FROM projects ORDER BY id")
        )
        return [
            {
                "id": row[0],
                "name": row[1],
                "settings_len": len(str(row[2])) if row[2] is not None else 0,
            }
            for row in result
        ]


def view_all_projects(*, db_path: str = DB_PATH) -> int:
    print(f"Database: {db_path}")
    if not os.path.exists(db_path):
        print("ERROR: database file not found.")
        return 2

    projects = list_projects(db_path)
    if not projects:
        print("No projects found.")
        return 0

    print(f"{'ID':<5} | {'Project':<25} | Settings JSON length")
    print("-" * 60)
    for project in projects:
        print(
            f"{project['id']:<5} | {project['name']:<25} | {project['settings_len']}"
        )
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Lists project rows from projects_main.db.")
    parser.add_argument("--db-path", default=DB_PATH)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    return view_all_projects(db_path=args.db_path)


if __name__ == "__main__":
    raise SystemExit(main())
