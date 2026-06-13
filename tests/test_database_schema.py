import os
import sqlite3
import uuid

import pytest
from sqlalchemy import inspect
from sqlalchemy.exc import IntegrityError

from Perevod.config import PROJECT_ROOT
from Perevod.database.models import (
    ChapterRun,
    DictionaryProposal,
    Project,
    Term,
    get_engine_and_session,
)


@pytest.fixture
def db_session():
    db_path = os.path.join(PROJECT_ROOT, f"_test_schema_{uuid.uuid4().hex}.db")
    engine, Session = get_engine_and_session(db_path)
    try:
        yield engine, Session
    finally:
        engine.dispose()
        if os.path.exists(db_path):
            os.remove(db_path)


def test_schema_has_project_scoped_unique_term_constraint(db_session):
    engine, _ = db_session

    constraints = {
        constraint["name"]
        for constraint in inspect(engine).get_unique_constraints("terms")
    }

    assert "uq_terms_project_term" in constraints


def test_schema_has_project_scoped_unique_chapter_run_constraint(db_session):
    engine, _ = db_session

    constraints = {
        constraint["name"]
        for constraint in inspect(engine).get_unique_constraints("chapter_runs")
    }

    assert "uq_chapter_runs_project_title" in constraints


def test_dictionary_proposals_have_review_metadata_columns(db_session):
    engine, _ = db_session

    columns = {
        column["name"] for column in inspect(engine).get_columns("dictionary_proposals")
    }

    assert {"status", "source_chapter", "reason"} <= columns


def test_schema_repairs_legacy_chapter_runs_missing_context_column():
    db_path = os.path.join(PROJECT_ROOT, f"_test_legacy_schema_{uuid.uuid4().hex}.db")
    connection = sqlite3.connect(db_path)
    try:
        connection.executescript(
            """
            CREATE TABLE projects (
                id INTEGER PRIMARY KEY,
                name VARCHAR NOT NULL UNIQUE,
                settings_json JSON NOT NULL
            );
            CREATE TABLE chapter_runs (
                id INTEGER PRIMARY KEY,
                project_id INTEGER NOT NULL,
                title VARCHAR NOT NULL,
                input_path VARCHAR NOT NULL,
                output_path VARCHAR NOT NULL,
                status VARCHAR NOT NULL,
                stages_json JSON NOT NULL,
                error TEXT,
                CONSTRAINT uq_chapter_runs_project_title UNIQUE (project_id, title),
                FOREIGN KEY(project_id) REFERENCES projects (id)
            );
            """
        )
        connection.commit()
    finally:
        connection.close()

    engine, _ = get_engine_and_session(db_path)
    try:
        columns = {column["name"] for column in inspect(engine).get_columns("chapter_runs")}
        assert "context" in columns
        assert "judge_result_json" in columns
        assert "refine_result_json" in columns
        assert "summary_result_json" in columns
    finally:
        engine.dispose()
        if os.path.exists(db_path):
            os.remove(db_path)


def test_schema_rejects_duplicate_terms_for_same_project(db_session):
    _, Session = db_session

    with Session() as session:
        project = Project(name="schema_test", settings_json={})
        session.add(project)
        session.flush()
        session.add_all(
            [
                Term(
                    project_id=project.id,
                    english_term="Council",
                    russian_term="Совет",
                ),
                Term(
                    project_id=project.id,
                    english_term="Council",
                    russian_term="Совет Старейшин",
                ),
            ]
        )

        with pytest.raises(IntegrityError):
            session.commit()


def test_schema_rejects_duplicate_chapter_runs_for_same_project(db_session):
    _, Session = db_session

    with Session() as session:
        project = Project(name="chapter_run_schema_test", settings_json={})
        session.add(project)
        session.flush()
        session.add_all(
            [
                ChapterRun(
                    project_id=project.id,
                    title="Chapter 1",
                    input_path="in1.txt",
                    output_path="out1.txt",
                    status="discovered",
                    stages_json={"discovered": "done"},
                ),
                ChapterRun(
                    project_id=project.id,
                    title="Chapter 1",
                    input_path="in1.txt",
                    output_path="out1.txt",
                    status="discovered",
                    stages_json={"discovered": "done"},
                ),
            ]
        )

        with pytest.raises(IntegrityError):
            session.commit()


def test_dictionary_proposal_metadata_roundtrip(db_session):
    _, Session = db_session

    with Session() as session:
        project = Project(name="proposal_metadata_test", settings_json={})
        session.add(project)
        session.flush()
        session.add(
            DictionaryProposal(
                project_id=project.id,
                english_term="Council",
                russian_term="Совет Старейшин",
                category="organization",
                confidence=0.65,
                status="candidate",
                source_chapter="chapter1",
                reason="self-learning synonym",
            )
        )
        session.commit()

        proposal = session.query(DictionaryProposal).one()
        assert proposal.status == "candidate"
        assert proposal.source_chapter == "chapter1"
        assert proposal.reason == "self-learning synonym"
