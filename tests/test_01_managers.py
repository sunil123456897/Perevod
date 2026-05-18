import pytest
from unittest.mock import MagicMock, patch
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker # Правильный импорт

from Perevod.database.database_manager import DatabaseManager
from Perevod.knowledge_base.knowledge_base_manager import KnowledgeBaseManager
from Perevod.gui.dictionary_editor import DictionaryEditorWindow
from Perevod.database.models import Base

@pytest.fixture(scope="function")
def db_manager():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    
    # Мокаем get_engine_and_session, чтобы DatabaseManager использовал нашу тестовую БД
    with patch("Perevod.database.database_manager.get_engine_and_session", return_value=(engine, Session)):
        # Создаем проект внутри тестовой БД, чтобы у DatabaseManager был project_id
        with Session() as s:
            from Perevod.database.models import Project
            s.add(Project(name="manager_test"))
            s.commit()
        
        manager = DatabaseManager(project_name="manager_test", db_path=":memory:")
        yield manager
        manager.engine.dispose()

def test_db_add_get_delete_term(db_manager):
    assert db_manager.get_terms_dictionary() == {}
    db_manager.add_or_update_term("test_term", "тестовый термин")
    terms = db_manager.get_terms_dictionary()
    assert "test_term" in terms
    db_manager.delete_term("test_term")
    assert db_manager.get_terms_dictionary() == {}

def test_db_cache_operations(db_manager):
    assert db_manager.get_from_cache("key1") is None
    db_manager.add_to_cache("key1", "cached_text")
    assert db_manager.get_from_cache("key1") == "cached_text"
    db_manager.add_to_cache("key1", "updated_cached_text")
    assert db_manager.get_from_cache("key1") == "updated_cached_text"
    db_manager.delete_from_cache("key1")
    assert db_manager.get_from_cache("key1") is None

@patch("Perevod.knowledge_base.knowledge_base_manager.genai.Client")
@patch("chromadb.PersistentClient")
def test_kb_delete_entries(mock_chromadb_client, mock_embedding_function):
    mock_collection = MagicMock()
    mock_chromadb_client.return_value.get_or_create_collection.return_value = mock_collection
    kb_manager = KnowledgeBaseManager("test_kb", "fake_key", "fake_model")
    ids_to_delete = ["id1", "id2"]
    kb_manager.delete_entries(ids_to_delete)
    mock_collection.delete.assert_called_once_with(ids=ids_to_delete)

@patch("tkinter.messagebox.askyesno", return_value=True)
@patch("tkinter.messagebox.showerror")
@patch("Perevod.gui.dictionary_editor.DictionaryEditorWindow._load_data")
@patch("Perevod.gui.main_window.TranslatorGUI.update_index_status")
def test_delete_term_handles_kb_failure(
    mock_update_status, mock_load_data, mock_showerror, mock_askyesno, db_manager
):
    mock_master = MagicMock()
    mock_kb_manager = MagicMock()
    mock_master.kb_manager = mock_kb_manager
    mock_kb_manager.delete_entries.side_effect = Exception("ChromaDB is down")
    db_manager.delete_term = MagicMock()
    editor = DictionaryEditorWindow.__new__(DictionaryEditorWindow)
    editor.master = mock_master
    editor.db_manager = db_manager
    editor._delete_term("test_term")
    mock_kb_manager.delete_entries.assert_called_once_with(ids=["dict_test_term"])
    db_manager.delete_term.assert_not_called()
    mock_showerror.assert_called_once()
