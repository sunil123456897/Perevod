import argparse
import logging
import os
import sys
from collections import defaultdict
from pathlib import Path

from sqlalchemy import create_engine, text

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from Perevod.config import PROJECT_ROOT
from Perevod.utils.logging_setup import setup_logging

logger = logging.getLogger("DeepAuditor")
DB_PATH = os.path.join(PROJECT_ROOT, "_project_files", "projects_main.db")


def find_duplicate_projects(db_path: str = DB_PATH) -> dict[str, list[dict]]:
    engine = create_engine(f"sqlite:///{db_path}")
    duplicates: dict[str, list[dict]] = defaultdict(list)
    with engine.connect() as connection:
        result = connection.execute(text("SELECT id, name, settings_json FROM projects"))
        for row in result:
            project_data = dict(row._mapping)
            normalized_name = (project_data["name"] or "").strip()
            duplicates[normalized_name].append(project_data)
    return {
        name: entries
        for name, entries in duplicates.items()
        if name and len(entries) > 1
    }


def choose_duplicate_ids_to_delete(duplicates: dict[str, list[dict]]) -> list[int]:
    project_ids: list[int] = []
    for entries in duplicates.values():
        ordered_entries = sorted(
            entries,
            key=lambda item: len(str(item.get("settings_json") or "")),
            reverse=True,
        )
        project_ids.extend(int(entry["id"]) for entry in ordered_entries[1:])
    return project_ids


def delete_projects(db_path: str, project_ids: list[int]) -> None:
    if not project_ids:
        return

    engine = create_engine(f"sqlite:///{db_path}")
    with engine.begin() as connection:
        for project_id in project_ids:
            connection.execute(
                text("DELETE FROM projects WHERE id = :project_id"),
                {"project_id": project_id},
            )


def deep_audit(*, db_path: str = DB_PATH, apply: bool = False) -> int:
    setup_logging()
    logger.info("--- Аудит дубликатов проектов: %s ---", db_path)

    if not os.path.exists(db_path):
        logger.error("Файл базы данных не найден: %s", db_path)
        return 2

    duplicates = find_duplicate_projects(db_path)
    if not duplicates:
        logger.info("Дубликатов в таблице projects не найдено.")
        return 0

    project_ids_to_delete = choose_duplicate_ids_to_delete(duplicates)
    for name, entries in duplicates.items():
        kept_entry = max(
            entries,
            key=lambda item: len(str(item.get("settings_json") or "")),
        )
        logger.warning(
            "Проект '%s' имеет %s дублей; будет сохранен ID=%s.",
            name,
            len(entries),
            kept_entry["id"],
        )
        for entry in entries:
            if int(entry["id"]) in project_ids_to_delete:
                logger.warning("Кандидат на удаление: ID=%s", entry["id"])

    if not apply:
        logger.info(
            "Dry run: изменения не применены. Для удаления дублей запустите с --apply."
        )
        return 1

    delete_projects(db_path, project_ids_to_delete)
    logger.info("Удалено дубликатов проектов: %s", len(project_ids_to_delete))
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Audits duplicate project rows in projects_main.db."
    )
    parser.add_argument("--db-path", default=DB_PATH)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Apply duplicate deletion. Without this flag the script is dry-run only.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    return deep_audit(db_path=args.db_path, apply=args.apply)


if __name__ == "__main__":
    raise SystemExit(main())
