# project_manager.py

import os
import logging
from pathlib import Path
from Perevod.config import settings, PROJECT_ROOT
from Perevod.database.models import Project, get_engine_and_session
from Perevod.utils.validation import validate_project_name


logger = logging.getLogger("NovelTranslator.Projects")


def _repair_legacy_windows_user_path(path_value):
    if not isinstance(path_value, str) or not path_value:
        return path_value

    normalized = path_value.replace("\\", "/")
    legacy_prefix = "C:/Users/User/"
    if not normalized.lower().startswith(legacy_prefix.lower()):
        return path_value

    suffix = normalized[len(legacy_prefix):]
    return str(Path.home() / suffix)


def _repair_project_settings_paths(project_settings):
    repaired = dict(project_settings)
    for key in ("input_dir", "output_dir"):
        repaired[key] = _repair_legacy_windows_user_path(repaired.get(key))
    return repaired


def _create_knowledge_base_manager(project_name, api_key="", embedding_model_name=""):
    from Perevod.knowledge_base.knowledge_base_manager import KnowledgeBaseManager

    return KnowledgeBaseManager(
        project_name=project_name,
        api_key=api_key,
        embedding_model_name=embedding_model_name,
    )


class ProjectManager:
    """Управляет проектами, используя центральную базу данных SQLite."""

    def __init__(self, db_path=None):
        if db_path is None:
            self.db_path = os.path.join(
                PROJECT_ROOT, "_project_files", "projects_main.db"
            )
            os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        else:
            self.db_path = db_path

        self.engine, self.Session = get_engine_and_session(self.db_path)
        logger.debug(f"ProjectManager initialized. DB file: {self.db_path}")

    def add_or_update_project(self, name, settings):
        """
        [УПРОЩЕНО] Добавляет новый или ПОЛНОСТЬЮ ПЕРЕЗАПИСЫВАЕТ существующий проект.
        Это предотвращает накопление "мусорных" настроек. GUI - единственный источник правды.
        """
        with self.Session() as session:
            try:
                safe_name = validate_project_name(name)
                project = session.query(Project).filter_by(name=safe_name).first()
                if project:
                    # Просто перезаписываем все настройки
                    project.settings_json = settings
                else:
                    project = Project(name=safe_name, settings_json=settings)
                    session.add(project)
                session.commit()
                logger.info(f"Проект '{safe_name}' успешно сохранен/обновлен.")
                return True
            except Exception as e:
                logger.error(
                    f"Ошибка при сохранении проекта '{name}': {e}", exc_info=True
                )
                session.rollback()
                return False

    def delete_project(self, name):
        """Удаляет проект по имени из БД и соответствующую коллекцию ChromaDB."""
        with self.Session() as session:
            try:
                safe_name = validate_project_name(name)
                project = session.query(Project).filter_by(name=safe_name).first()
                if project:
                    # Сначала удаляем коллекцию ChromaDB
                    kb_manager = _create_knowledge_base_manager(safe_name)
                    kb_manager.delete_collection()

                    session.delete(project)
                    session.commit()
                    logger.info(
                        f"Проект '{safe_name}' и связанная с ним база знаний удалены."
                    )
                    return True
                logger.warning(f"Проект '{safe_name}' не найден для удаления.")
                return False
            except Exception as e:
                logger.error(
                    f"Ошибка при удалении проекта '{name}': {e}", exc_info=True
                )
                session.rollback()
                return False

    def get_project_settings(self, name):
        """
        [УПРОЩЕНО] Возвращает полные настройки проекта из БД, дополняя их значениями по умолчанию.
        """
        # Сначала берем полную копию настроек по умолчанию
        full_settings = settings.model_dump()

        if name == "Default":
            return full_settings
        safe_name = validate_project_name(name)

        with self.Session() as session:
            project = session.query(Project).filter_by(name=safe_name).first()
            if project and project.settings_json:
                # Обновляем дефолтные настройки сохраненными значениями.
                # Это гарантирует, что все ключи будут на месте.
                full_settings.update(_repair_project_settings_paths(project.settings_json))

        return full_settings

    def get_project_names(self):
        """Возвращает отсортированный список имен всех проектов из БД."""
        with self.Session() as session:
            projects = session.query(Project.name).order_by(Project.name).all()
            return [name for (name,) in projects]
