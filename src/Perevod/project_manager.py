# project_manager.py

import os
import logging
from Perevod.config import DEFAULT_SETTINGS, PROJECT_ROOT
from Perevod.database.models import Project, get_engine_and_session

logger = logging.getLogger("NovelTranslator.Projects")

class ProjectManager:
    """Управляет проектами, используя центральную базу данных SQLite."""
    def __init__(self, db_path=None):
        if db_path is None:
            self.db_path = os.path.join(PROJECT_ROOT, '_project_files', 'projects_main.db')
            os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        else:
            self.db_path = db_path
        
        self.engine, self.Session = get_engine_and_session(self.db_path)
        logger.debug(f"Менеджер проектов инициализирован. Файл БД: {self.db_path}")

    def add_or_update_project(self, name, settings):
        """
        [УПРОЩЕНО] Добавляет новый или ПОЛНОСТЬЮ ПЕРЕЗАПИСЫВАЕТ существующий проект.
        Это предотвращает накопление "мусорных" настроек. GUI - единственный источник правды.
        """
        with self.Session() as session:
            try:
                project = session.query(Project).filter_by(name=name).first()
                if project:
                    # Просто перезаписываем все настройки
                    project.settings_json = settings
                else:
                    project = Project(name=name, settings_json=settings)
                    session.add(project)
                session.commit()
                logger.info(f"Проект '{name}' успешно сохранен/обновлен.")
                return True
            except Exception as e:
                logger.error(f"Ошибка при сохранении проекта '{name}': {e}", exc_info=True)
                session.rollback()
                return False

    def delete_project(self, name):
        """Удаляет проект по имени из БД."""
        with self.Session() as session:
            try:
                project = session.query(Project).filter_by(name=name).first()
                if project:
                    session.delete(project)
                    session.commit()
                    logger.info(f"Проект '{name}' удален.")
                    return True
                logger.warning(f"Проект '{name}' не найден для удаления.")
                return False
            except Exception as e:
                logger.error(f"Ошибка при удалении проекта '{name}': {e}", exc_info=True)
                session.rollback()
                return False

    def get_project_settings(self, name):
        """
        [УПРОЩЕНО] Возвращает полные настройки проекта из БД, дополняя их значениями по умолчанию.
        """
        # Сначала берем полную копию настроек по умолчанию
        full_settings = DEFAULT_SETTINGS.copy()

        if name == "Default":
            return full_settings

        with self.Session() as session:
            project = session.query(Project).filter_by(name=name).first()
            if project and project.settings_json:
                # Обновляем дефолтные настройки сохраненными значениями.
                # Это гарантирует, что все ключи будут на месте.
                full_settings.update(project.settings_json)
        
        return full_settings

    def get_project_names(self):
        """Возвращает отсортированный список имен всех проектов из БД."""
        with self.Session() as session:
            projects = session.query(Project.name).order_by(Project.name).all()
            return [name for (name,) in projects]
