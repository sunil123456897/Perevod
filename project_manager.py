# project_manager.py

import os
import json
import logging
from config import DEFAULT_SETTINGS
from database.database_manager import DatabaseManager
from sqlalchemy.orm import Session
from database.models import Project, get_engine_and_session

logger = logging.getLogger("NovelTranslator.Projects")

class ProjectManager:
    """Управляет проектами, используя центральную базу данных SQLite."""
    def __init__(self):
        script_dir = os.path.dirname(os.path.abspath(__file__))
        self.db_path = os.path.join(script_dir, '_project_files', 'projects_main.db')
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        
        self.engine, self.Session = get_engine_and_session(self.db_path)
        logger.debug(f"Менеджер проектов инициализирован. Файл БД: {self.db_path}")

    def add_or_update_project(self, name, settings):
        """Добавляет новый или обновляет существующий проект в БД."""
        with self.Session() as session:
            project = session.query(Project).filter_by(name=name).first()
            if project:
                project.settings_json = settings
            else:
                project = Project(name=name, settings_json=settings)
                session.add(project)
            session.commit()
        logger.info(f"Проект '{name}' добавлен/обновлен.")
        return True

    def delete_project(self, name):
        """Удаляет проект по имени из БД."""
        with self.Session() as session:
            project = session.query(Project).filter_by(name=name).first()
            if project:
                session.delete(project)
                session.commit()
                logger.info(f"Проект '{name}' удален.")
                return True
        logger.warning(f"Проект '{name}' не найден для удаления.")
        return False

    def get_project_settings(self, name):
        """Возвращает полные настройки проекта из БД."""
        with self.Session() as session:
            project = session.query(Project).filter_by(name=name).first()
            if project:
                # Объединяем дефолтные настройки с сохраненными
                full_settings = DEFAULT_SETTINGS.copy()
                full_settings.update(project.settings_json)
                return full_settings
            else:
                return DEFAULT_SETTINGS.copy()

    def get_project_names(self):
        """Возвращает отсортированный список имен всех проектов из БД."""
        with self.Session() as session:
            projects = session.query(Project.name).order_by(Project.name).all()
            return [name for (name,) in projects]
