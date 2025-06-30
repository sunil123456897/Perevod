# database/database_manager.py

import os
import logging
from contextlib import contextmanager
from .models import (Project, Term, WorldBibleEntry, DictionaryProposal, 
                     WorldBibleProposal, get_engine_and_session)

logger = logging.getLogger("NovelTranslator.DBManager")

class DatabaseManager:
    """Управляет всеми операциями с реляционной базой данных SQLite."""
    def __init__(self, project_name):
        self.project_name = project_name
        
        # Путь к общей базе данных всех проектов
        script_dir = os.path.dirname(os.path.abspath(__file__))
        self.db_path = os.path.join(script_dir, '..', '_project_files', 'projects_main.db')
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        
        self.engine, self.Session = get_engine_and_session(self.db_path)
        
        self.project_id = self._get_or_create_project_id()
        logger.info(f"DatabaseManager инициализирован для проекта '{project_name}' (ID: {self.project_id})")

    @contextmanager
    def session_scope(self):
        """Обеспечивает транзакционную область видимости для сессии."""
        session = self.Session()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            logger.error("Ошибка в транзакции, выполнен откат.", exc_info=True)
            raise
        finally:
            session.close()

    def _get_or_create_project_id(self):
        """Получает или создает проект в БД и возвращает его ID."""
        with self.session_scope() as session:
            project = session.query(Project).filter_by(name=self.project_name).first()
            if project:
                return project.id
            else:
                new_project = Project(name=self.project_name, settings_json={})
                session.add(new_project)
                session.flush() # Получаем ID до коммита
                return new_project.id

    def get_project_settings(self):
        """Получает настройки для текущего проекта."""
        with self.session_scope() as session:
            project = session.query(Project).filter_by(id=self.project_id).one()
            return project.settings_json

    def update_project_settings(self, settings_dict):
        """Обновляет настройки для текущего проекта."""
        with self.session_scope() as session:
            project = session.query(Project).filter_by(id=self.project_id).one()
            project.settings_json = settings_dict

    # --- Методы для работы со словарем ---
    def get_terms_dictionary(self):
        """Возвращает словарь {eng: rus} для текущего проекта."""
        with self.session_scope() as session:
            terms = session.query(Term).filter_by(project_id=self.project_id).all()
            return {term.english_term: term.russian_term for term in terms}

    def add_or_update_term(self, english, russian, category='other'):
        """Добавляет или обновляет термин в словаре."""
        with self.session_scope() as session:
            term = session.query(Term).filter_by(project_id=self.project_id, english_term=english).first()
            if term:
                term.russian_term = russian
                term.category = category
            else:
                term = Term(project_id=self.project_id, english_term=english, russian_term=russian, category=category)
                session.add(term)

    # --- Методы для работы с Библией Вселенной ---
    def get_world_bible(self):
        """Возвращает Библию Вселенной в виде словаря."""
        with self.session_scope() as session:
            entries = session.query(WorldBibleEntry).filter_by(project_id=self.project_id).all()
            return {
                entry.english_name: {
                    "russian_name": entry.russian_name,
                    "category": entry.category,
                    "description": entry.description
                } for entry in entries
            }

    def add_or_update_bible_entry(self, english_name, data):
        """Добавляет или обновляет запись в Библии."""
        with self.session_scope() as session:
            entry = session.query(WorldBibleEntry).filter_by(project_id=self.project_id, english_name=english_name).first()
            if entry:
                entry.russian_name = data.get("russian_name")
                entry.category = data.get("category")
                entry.description = data.get("description")
            else:
                entry = WorldBibleEntry(
                    project_id=self.project_id,
                    english_name=english_name,
                    russian_name=data.get("russian_name"),
                    category=data.get("category"),
                    description=data.get("description")
                )
                session.add(entry)

    def get_all_world_bible_entries(self):
        """Возвращает все записи Библии Вселенной для текущего проекта."""
        with self.session_scope() as session:
            return session.query(WorldBibleEntry).filter_by(project_id=self.project_id).all()

    def get_all_terms(self):
        """Возвращает все термины для текущего проекта."""
        with self.session_scope() as session:
            return session.query(Term).filter_by(project_id=self.project_id).all()

    def get_term_by_english(self, english_term):
        """Находит термин по английскому варианту."""
        with self.session_scope() as session:
            return session.query(Term).filter_by(project_id=self.project_id, english_term=english_term).first()

    def get_dictionary_proposal_by_english(self, english_term):
        """Находит предложение для словаря по английскому варианту."""
        with self.session_scope() as session:
            return session.query(DictionaryProposal).filter_by(project_id=self.project_id, english_term=english_term).first()

    def count_dictionary_proposals(self):
        """Считает количество предложений для словаря."""
        with self.session_scope() as session:
            return session.query(DictionaryProposal).filter_by(project_id=self.project_id).count()

    def count_world_bible_proposals(self):
        """Считает количество предложений для Библии Вселенной."""
        with self.session_scope() as session:
            return session.query(WorldBibleProposal).filter_by(project_id=self.project_id).count()
    
    # --- Методы для работы с предложениями словаря ---
    def add_dictionary_proposal(self, english_term, russian_translation, category='other', confidence=0.0):
        """Добавляет новое предложение для словаря."""
        with self.session_scope() as session:
            proposal = DictionaryProposal(
                project_id=self.project_id,
                english_term=english_term,
                russian_term=russian_translation,
                category=category,
                confidence=confidence
            )
            session.add(proposal)

    # --- Методы для работы с предложениями Библии Вселенной ---
    def add_world_bible_proposal(self, english_name, russian_name, category, description):
        """Добавляет новое предложение для Библии Вселенной."""
        with self.session_scope() as session:
            proposal = WorldBibleProposal(
                project_id=self.project_id,
                english_name=english_name,
                russian_name=russian_name,
                category=category,
                description=description
            )
            session.add(proposal)

    def get_world_bible_proposals(self):
        """Возвращает все предложения для Библии Вселенной."""
        with self.session_scope() as session:
            proposals = session.query(WorldBibleProposal).filter_by(project_id=self.project_id).all()
            return {
                p.english_name: {
                    "russian_name": p.russian_name,
                    "category": p.category,
                    "description": p.description
                } for p in proposals
            }
    
    def get_world_bible_proposal(self, name):
        """Возвращает одно предложение для Библии Вселенной по имени."""
        with self.session_scope() as session:
            proposal = session.query(WorldBibleProposal).filter_by(project_id=self.project_id, english_name=name).first()
            if not proposal:
                return None
            return {
                "russian_name": proposal.russian_name,
                "category": proposal.category,
                "description": proposal.description
            }

    def delete_world_bible_proposal(self, name):
        """Удаляет предложение для Библии Вселенной по им��ни."""
        with self.session_scope() as session:
            session.query(WorldBibleProposal).filter_by(project_id=self.project_id, english_name=name).delete()

    def clear_world_bible_proposals(self):
        """Удаляет все предложения для Библии Вселенной."""
        with self.session_scope() as session:
            session.query(WorldBibleProposal).filter_by(project_id=self.project_id).delete()

    def delete_bible_entry(self, name):
        """Удаляет запись из Библии Вселенной по имени."""
        with self.session_scope() as session:
            session.query(WorldBibleEntry).filter_by(project_id=self.project_id, english_name=name).delete()

    def count_terms(self):
        """Считает количество терминов в словаре."""
        with self.session_scope() as session:
            return session.query(Term).filter_by(project_id=self.project_id).count()

    def count_world_bible_entries(self):
        """Считает количество записей в Библии Вселенной."""
        with self.session_scope() as session:
            return session.query(WorldBibleEntry).filter_by(project_id=self.project_id).count()

    def get_dictionary_proposals(self):
        """Возвращает все предложения для словаря."""
        with self.session_scope() as session:
            proposals = session.query(DictionaryProposal).filter_by(project_id=self.project_id).all()
            return {
                p.english_term: {
                    "russian": p.russian_term,
                    "category": p.category,
                    "confidence": p.confidence
                } for p in proposals
            }

    def get_dictionary_proposal(self, term):
        """Возвращает одно предложение для словаря по термину."""
        with self.session_scope() as session:
            proposal = session.query(DictionaryProposal).filter_by(project_id=self.project_id, english_term=term).first()
            if not proposal:
                return None
            return {
                "russian": proposal.russian_term,
                "category": proposal.category,
                "confidence": proposal.confidence
            }
    
    def delete_dictionary_proposal(self, term):
        """Удаляет предложение для словаря по термину."""
        with self.session_scope() as session:
            session.query(DictionaryProposal).filter_by(project_id=self.project_id, english_term=term).delete()

    def clear_dictionary_proposals(self):
        """Удаляет все предложения для словаря."""
        with self.session_scope() as session:
            session.query(DictionaryProposal).filter_by(project_id=self.project_id).delete()

    def delete_term(self, term):
        """Удаляет термин из словаря."""
        with self.session_scope() as session:
            session.query(Term).filter_by(project_id=self.project_id, english_term=term).delete()