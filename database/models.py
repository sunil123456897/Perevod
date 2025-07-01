# database/models.py

from sqlalchemy import (create_engine, Column, Integer, String, Text, Float, ForeignKey, JSON)
from sqlalchemy.orm import relationship, sessionmaker, declarative_base

# Базовый класс для всех наших моделей ORM
Base = declarative_base()

class Project(Base):
    """Модель для хранения настроек проекта."""
    __tablename__ = 'projects'
    id = Column(Integer, primary_key=True)
    name = Column(String, unique=True, nullable=False)
    settings_json = Column(JSON, nullable=False, default={})

    # Отношения "один ко многим"
    terms = relationship("Term", back_populates="project", cascade="all, delete-orphan")
    bible_entries = relationship("WorldBibleEntry", back_populates="project", cascade="all, delete-orphan")
    dictionary_proposals = relationship("DictionaryProposal", back_populates="project", cascade="all, delete-orphan")
    world_bible_proposals = relationship("WorldBibleProposal", back_populates="project", cascade="all, delete-orphan")

    def __repr__(self):
        return f"<Project(name='{self.name}')>"

class Term(Base):
    """Модель для термина в словаре."""
    __tablename__ = 'terms'
    id = Column(Integer, primary_key=True)
    project_id = Column(Integer, ForeignKey('projects.id'), nullable=False)
    english_term = Column(String, nullable=False)
    russian_term = Column(String, nullable=False)
    category = Column(String, default='other')

    project = relationship("Project", back_populates="terms")

class WorldBibleEntry(Base):
    """Модель для записи в Библии Вселенной."""
    __tablename__ = 'world_bible_entries'
    id = Column(Integer, primary_key=True)
    project_id = Column(Integer, ForeignKey('projects.id'), nullable=False)
    english_name = Column(String, nullable=False)
    russian_name = Column(String)
    category = Column(String, default='other')
    description = Column(Text, nullable=False)

    project = relationship("Project", back_populates="bible_entries")

class DictionaryProposal(Base):
    """Модель для предложенного термина словаря."""
    __tablename__ = 'dictionary_proposals'
    id = Column(Integer, primary_key=True)
    project_id = Column(Integer, ForeignKey('projects.id'), nullable=False)
    english_term = Column(String, nullable=False)
    russian_term = Column(String, nullable=False)
    category = Column(String, default='other')
    confidence = Column(Float, default=0.0)

    project = relationship("Project", back_populates="dictionary_proposals")

class WorldBibleProposal(Base):
    """Модель для предложенной записи в Библии Вселенной."""
    __tablename__ = 'world_bible_proposals'
    id = Column(Integer, primary_key=True)
    project_id = Column(Integer, ForeignKey('projects.id'), nullable=False)
    english_name = Column(String, nullable=False)
    russian_name = Column(String)
    category = Column(String, default='other')
    description = Column(Text, nullable=False)

    project = relationship("Project", back_populates="world_bible_proposals")

def get_engine_and_session(db_path):
    """Вспомогательная функция для создания движка и сессии."""
    engine = create_engine(f'sqlite:///{db_path}')
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    return engine, Session