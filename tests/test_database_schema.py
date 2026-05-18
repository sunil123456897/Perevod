import os
import uuid

import pytest
from sqlalchemy import inspect
from sqlalchemy.exc import IntegrityError

from Perevod.config import PROJECT_ROOT
from Perevod.database.models import Project, Term, get_engine_and_session


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
