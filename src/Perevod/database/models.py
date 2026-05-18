# database/models.py

from sqlalchemy import (
    create_engine,
    Column,
    Integer,
    String,
    Text,
    Float,
    ForeignKey,
    JSON,
    UniqueConstraint,
    event,
)
from sqlalchemy.orm import relationship, sessionmaker, declarative_base

# Базовый класс для всех наших моделей ORM
Base = declarative_base()


class Project(Base):
    """Модель для хранения настроек проекта."""

    __tablename__ = "projects"
    id = Column(Integer, primary_key=True)
    name = Column(String, unique=True, nullable=False)
    settings_json = Column(JSON, nullable=False, default=lambda: {})

    # Отношения "один ко многим"
    terms = relationship("Term", back_populates="project", cascade="all, delete-orphan")
    bible_entries = relationship(
        "WorldBibleEntry", back_populates="project", cascade="all, delete-orphan"
    )
    dictionary_proposals = relationship(
        "DictionaryProposal", back_populates="project", cascade="all, delete-orphan"
    )
    world_bible_proposals = relationship(
        "WorldBibleProposal", back_populates="project", cascade="all, delete-orphan"
    )
    translation_cache_entries = relationship(
        "TranslationCache", back_populates="project", cascade="all, delete-orphan"
    )
    quarantined_terms = relationship(
        "QuarantinedTerm", back_populates="project", cascade="all, delete-orphan"
    )

    def __repr__(self):
        return f"<Project(name='{self.name}')>"


class Term(Base):
    """Модель для термина в словаре."""

    __tablename__ = "terms"
    __table_args__ = (
        UniqueConstraint("project_id", "english_term", name="uq_terms_project_term"),
    )
    id = Column(Integer, primary_key=True)
    project_id = Column(Integer, ForeignKey("projects.id"), nullable=False)
    english_term = Column(String, nullable=False)
    russian_term = Column(String, nullable=False)
    category = Column(String, default="other")

    project = relationship("Project", back_populates="terms")


class QuarantinedTerm(Base):
    """Модель для термина в карантине."""

    __tablename__ = "quarantined_terms"
    __table_args__ = (
        UniqueConstraint(
            "project_id", "english_term", name="uq_quarantine_project_term"
        ),
    )
    id = Column(Integer, primary_key=True)
    project_id = Column(Integer, ForeignKey("projects.id"), nullable=False)
    english_term = Column(String, nullable=False)
    russian_term = Column(String, nullable=False)
    category = Column(String, default="other")
    reason = Column(String, nullable=False)

    project = relationship("Project", back_populates="quarantined_terms")


class WorldBibleEntry(Base):
    """Модель для записи в Библии Вселенной."""

    __tablename__ = "world_bible_entries"
    __table_args__ = (
        UniqueConstraint(
            "project_id", "english_name", name="uq_bible_project_name"
        ),
    )
    id = Column(Integer, primary_key=True)
    project_id = Column(Integer, ForeignKey("projects.id"), nullable=False)
    english_name = Column(String, nullable=False)
    russian_name = Column(String)
    category = Column(String, default="other")
    description = Column(Text, nullable=False)
    russian_description = Column(Text)

    project = relationship("Project", back_populates="bible_entries")


class DictionaryProposal(Base):
    """Модель для предложенного термина словаря."""

    __tablename__ = "dictionary_proposals"
    __table_args__ = (
        UniqueConstraint(
            "project_id", "english_term", name="uq_dictionary_proposals_project_term"
        ),
    )
    id = Column(Integer, primary_key=True)
    project_id = Column(Integer, ForeignKey("projects.id"), nullable=False)
    english_term = Column(String, nullable=False)
    russian_term = Column(String, nullable=False)
    category = Column(String, default="other")
    confidence = Column(Float, default=0.0)

    project = relationship("Project", back_populates="dictionary_proposals")


class WorldBibleProposal(Base):
    """Модель для предложенной записи в Библии Вселенной."""

    __tablename__ = "world_bible_proposals"
    __table_args__ = (
        UniqueConstraint(
            "project_id", "english_name", name="uq_bible_proposals_project_name"
        ),
    )
    id = Column(Integer, primary_key=True)
    project_id = Column(Integer, ForeignKey("projects.id"), nullable=False)
    english_name = Column(String, nullable=False)
    russian_name = Column(String)
    category = Column(String, default="other")
    description = Column(Text, nullable=False)

    project = relationship("Project", back_populates="world_bible_proposals")


class TranslationCache(Base):
    """Модель для кэширования переводов."""

    __tablename__ = "translation_cache"
    __table_args__ = (
        UniqueConstraint(
            "project_id", "cache_key", name="uq_translation_cache_project_key"
        ),
    )
    id = Column(Integer, primary_key=True)
    project_id = Column(Integer, ForeignKey("projects.id"), nullable=False)
    cache_key = Column(String, nullable=False, index=True)
    translated_text = Column(Text, nullable=False)

    project = relationship("Project", back_populates="translation_cache_entries")


def get_engine_and_session(db_path):
    """Вспомогательная функция для создания движка и сессии."""
    engine = create_engine(f"sqlite:///{db_path}")

    @event.listens_for(engine, "connect")
    def _enable_sqlite_foreign_keys(dbapi_connection, connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    return engine, Session
