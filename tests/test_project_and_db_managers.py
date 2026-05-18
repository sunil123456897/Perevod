import pytest
import os
import uuid
from pathlib import Path
from unittest.mock import patch

from Perevod.config import PROJECT_ROOT
from Perevod.project_manager import ProjectManager
from Perevod.database.database_manager import DatabaseManager


@pytest.fixture(scope="function")
def db_path():
    path = os.path.join(PROJECT_ROOT, f"_test_project_managers_{uuid.uuid4().hex}.db")
    if os.path.exists(path):
        os.remove(path)
    yield path
    if os.path.exists(path):
        os.remove(path)


@pytest.fixture
def project_manager(db_path):
    manager = ProjectManager(db_path=db_path)
    yield manager
    manager.engine.dispose()


@pytest.fixture
def db_manager(project_manager, db_path):
    project_manager.add_or_update_project("TestProject", {})
    manager = DatabaseManager(project_name="TestProject", db_path=db_path)
    yield manager
    manager.engine.dispose()

# --- Тесты ---

def test_add_and_get_project(project_manager):
    project_name = "TestProject1"
    project_settings = {"api_key": "123"}
    project_manager.add_or_update_project(project_name, project_settings)

    # Проверяем, что проект появился в списке
    assert project_name in project_manager.get_project_names()

    retrieved = project_manager.get_project_settings(project_name)
    assert retrieved["api_key"] == "123"


def test_get_project_settings_repairs_legacy_windows_user_paths(project_manager, monkeypatch):
    monkeypatch.setattr("Perevod.project_manager.Path.home", lambda: Path(r"C:\Users\vanya"))
    project_manager.add_or_update_project(
        "LegacyPaths",
        {
            "input_dir": "C:/Users/User/Desktop/kod/Eng_Fermer",
            "output_dir": r"C:\Users\User\Desktop\kod\Rus_Fermer",
        },
    )

    settings = project_manager.get_project_settings("LegacyPaths")

    assert settings["input_dir"] == r"C:\Users\vanya\Desktop\kod\Eng_Fermer"
    assert settings["output_dir"] == r"C:\Users\vanya\Desktop\kod\Rus_Fermer"


@pytest.mark.parametrize(
    "unsafe_name",
    [
        "",
        "   ",
        ".",
        "..",
        r"C:\tmp\project",
        r"..\outside",
        "Project:ads",
        "Project?draft",
        "CON",
        "NUL.txt",
        "LPT1",
        "Project.",
    ],
)
def test_project_manager_rejects_unsafe_project_names(project_manager, unsafe_name):
    assert project_manager.add_or_update_project(unsafe_name, {}) is False
    assert unsafe_name not in project_manager.get_project_names()


def test_database_manager_rejects_unsafe_project_name(db_path):
    with pytest.raises(ValueError, match="Unsafe project name"):
        DatabaseManager(project_name=r"..\outside", db_path=db_path)

@patch("Perevod.project_manager._create_knowledge_base_manager")
def test_delete_project(mock_kb, project_manager):
    project_manager.add_or_update_project("ToDelete", {})
    assert "ToDelete" in project_manager.get_project_names()
    project_manager.delete_project("ToDelete")
    assert "ToDelete" not in project_manager.get_project_names()


@patch("Perevod.project_manager._create_knowledge_base_manager")
def test_delete_project_preserves_project_when_kb_delete_fails(mock_kb, project_manager):
    kb_manager = mock_kb.return_value
    kb_manager.delete_collection.side_effect = RuntimeError("ChromaDB is down")
    project_manager.add_or_update_project("KeepOnKbFailure", {})

    assert project_manager.delete_project("KeepOnKbFailure") is False

    assert "KeepOnKbFailure" in project_manager.get_project_names()


def test_add_and_get_term(db_manager):
    db_manager.add_or_update_term("hello", "привет")
    terms = db_manager.get_terms_dictionary()
    assert "hello" in terms
    assert terms["hello"]["russian_term"] == "привет"

def test_add_and_get_bible_entry(db_manager):
    data = {"russian_name": "ПерсонажА", "description": "Описание"}
    db_manager.add_or_update_bible_entry("CharacterA", data)
    bible = db_manager.get_world_bible()
    assert "CharacterA" in bible
    assert bible["CharacterA"]["russian_name"] == "ПерсонажА"

def test_proposals(db_manager):
    db_manager.add_dictionary_proposal("prop1", "предложение1")
    assert "prop1" in db_manager.get_dictionary_proposals()
    db_manager.delete_dictionary_proposal("prop1")
    assert "prop1" not in db_manager.get_dictionary_proposals()

def test_merge_terms(db_manager):
    db_manager.add_or_update_term("main", "главный")
    db_manager.add_or_update_term("alias", "псевдоним")
    db_manager.merge_dictionary_terms("main", ["alias"])
    terms = db_manager.get_terms_dictionary()
    assert "main" in terms
    assert "alias" not in terms


def test_merge_terms_ignores_primary_term_in_aliases(db_manager):
    db_manager.add_or_update_term("main", "главный")

    assert db_manager.merge_dictionary_terms("main", ["main"]) is True

    terms = db_manager.get_terms_dictionary()
    assert terms == {
        "main": {
            "russian_term": "главный",
            "category": "other",
        }
    }


def test_restore_quarantined_term_merges_existing_dictionary_entry(db_manager):
    db_manager.add_or_update_term("Council", "Совет", "organization")
    db_manager.quarantine_term("Council", "duplicate")
    quarantined_terms, total = db_manager.get_paginated_quarantined_terms("", 0, 10)
    db_manager.add_or_update_term("Council", "Новый совет", "other")

    db_manager.restore_term(quarantined_terms[0]["id"])

    terms = db_manager.get_terms_dictionary()
    remaining_quarantine, remaining_total = db_manager.get_paginated_quarantined_terms(
        "", 0, 10
    )
    assert total == 1
    assert terms["Council"] == {
        "russian_term": "Совет",
        "category": "organization",
    }
    assert remaining_quarantine == []
    assert remaining_total == 0


def test_quarantine_term_updates_existing_quarantine_entry(db_manager):
    db_manager.add_or_update_term("Council", "Совет", "organization")
    db_manager.quarantine_term("Council", "first duplicate")
    db_manager.add_or_update_term("Council", "Новый совет", "other")

    db_manager.quarantine_term("Council", "second duplicate")

    terms = db_manager.get_terms_dictionary()
    quarantined_terms, total = db_manager.get_paginated_quarantined_terms("", 0, 10)
    assert terms == {}
    assert total == 1
    assert quarantined_terms[0]["english_term"] == "Council"
    assert quarantined_terms[0]["russian_term"] == "Новый совет"
    assert quarantined_terms[0]["category"] == "other"
    assert quarantined_terms[0]["reason"] == "second duplicate"
