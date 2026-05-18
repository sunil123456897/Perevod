import pytest
from unittest.mock import MagicMock, patch
import customtkinter as ctk

from Perevod.gui.bible_editor import WorldBibleEditorWindow
from Perevod.gui.dictionary_editor import DictionaryEditorWindow
from Perevod.gui.quarantine_editor import QuarantineEditorWindow

class DummyVar:
    def __init__(self, value=""):
        self.value = value
    def get(self):
        return self.value
    def set(self, val):
        self.value = val
    def trace_add(self, mode, callback):
        return "trace_id"

class DummyTextbox:
    def __init__(self, text=""):
        self.text = text
    def get(self, start, end):
        return self.text
    def insert(self, index, text):
        self.text = text
    def delete(self, start, end):
        self.text = ""
    def bind(self, event, callback):
        pass

@pytest.fixture(autouse=True)
def mock_ctk_vars(monkeypatch):
    monkeypatch.setattr(ctk, "StringVar", DummyVar)
    monkeypatch.setattr(ctk, "CTkEntry", MagicMock)
    monkeypatch.setattr(ctk, "CTkLabel", MagicMock)
    monkeypatch.setattr(ctk, "CTkButton", MagicMock)
    monkeypatch.setattr(ctk, "CTkFrame", MagicMock)
    monkeypatch.setattr(ctk, "CTkTextbox", lambda *args, **kwargs: DummyTextbox())

# --- WorldBibleEditorWindow Tests ---

def test_bible_editor_load_data(monkeypatch):
    editor = WorldBibleEditorWindow.__new__(WorldBibleEditorWindow)
    editor.search_var = DummyVar("test_query")
    editor.current_page = 1
    editor.items_per_page = 15
    
    db_manager = MagicMock()
    db_manager.get_paginated_bible_entries.return_value = (["entry1"], 1)
    db_manager.get_paginated_world_bible_proposals.return_value = (["proposal1"], 1)
    editor.db_manager = db_manager
    
    editor._display_page = MagicMock()
    
    editor._load_data()
    
    db_manager.get_paginated_bible_entries.assert_called_once_with("test_query", 1, 15)
    db_manager.get_paginated_world_bible_proposals.assert_called_once_with("test_query", 1, 15)
    assert editor.total_items == 1
    editor._display_page.assert_called_once_with(["entry1"], ["proposal1"])

def test_bible_editor_save_entries(monkeypatch):
    editor = WorldBibleEditorWindow.__new__(WorldBibleEditorWindow)
    db_manager = MagicMock()
    editor.db_manager = db_manager
    
    # Setup modified entry widgets
    entry1 = {
        "eng_var": DummyVar("original_eng"),
        "rus_var": DummyVar("russian_translation"),
        "cat_var": DummyVar("char"),
        "desc_eng_textbox": DummyTextbox("desc eng"),
        "desc_rus_textbox": DummyTextbox("desc rus"),
        "is_new": False,
        "is_modified": True,
    }
    
    # Setup a new entry widget
    entry2 = {
        "eng_var": DummyVar("new_eng"),
        "rus_var": DummyVar("new_rus"),
        "cat_var": DummyVar("place"),
        "desc_eng_textbox": DummyTextbox("new desc eng"),
        "desc_rus_textbox": DummyTextbox("new desc rus"),
        "is_new": True,
        "is_modified": True,
    }
    
    editor.entry_widgets = {
        "original_eng": entry1,
        "__new_0": entry2
    }
    editor._load_data = MagicMock()
    
    editor._save_all_entries()
    
    # Verify db_manager calls
    db_manager.add_or_update_bible_entry.assert_any_call("original_eng", {
        "russian_name": "russian_translation",
        "category": "char",
        "description": "desc eng",
        "russian_description": "desc rus",
    })
    
    db_manager.add_or_update_bible_entry.assert_any_call("new_eng", {
        "russian_name": "new_rus",
        "category": "place",
        "description": "new desc eng",
        "russian_description": "new desc rus",
    })
    
    editor._load_data.assert_called_once()

def test_bible_editor_delete_entry_success(monkeypatch):
    editor = WorldBibleEditorWindow.__new__(WorldBibleEditorWindow)
    db_manager = MagicMock()
    editor.db_manager = db_manager
    
    master = MagicMock()
    kb_manager = MagicMock()
    kb_manager.collection = MagicMock()
    kb_manager.collection.get.return_value = {"ids": ["id1", "id2"]}
    master.kb_manager = kb_manager
    editor.master = master
    
    # Mock askyesno to return True
    monkeypatch.setattr("tkinter.messagebox.askyesno", lambda title, msg: True)
    
    editor._load_data = MagicMock()
    
    editor._delete_entry("test_name")
    
    kb_manager.collection.get.assert_called_once_with(
        where={"name": "test_name", "source": "bible"}
    )
    kb_manager.delete_entries.assert_called_once_with(ids=["id1", "id2"])
    db_manager.delete_bible_entry.assert_called_once_with("test_name")
    editor._load_data.assert_called_once()
    master.update_index_status.assert_called_once()

# --- DictionaryEditorWindow Tests ---

def test_dict_editor_load_data():
    editor = DictionaryEditorWindow.__new__(DictionaryEditorWindow)
    editor.search_var = DummyVar("query")
    editor.current_page = 0
    editor.items_per_page = 15
    
    db_manager = MagicMock()
    db_manager.get_paginated_terms.return_value = (["term1"], 1)
    db_manager.get_paginated_dictionary_proposals.return_value = (["prop1"], 1)
    editor.db_manager = db_manager
    
    editor._display_page = MagicMock()
    
    editor._load_data()
    
    db_manager.get_paginated_terms.assert_called_once_with("query", 0, 15)
    db_manager.get_paginated_dictionary_proposals.assert_called_once_with("query", 0, 15)
    assert editor.total_items == 1
    editor._display_page.assert_called_once_with(["term1"], ["prop1"])

def test_dict_editor_save_terms():
    editor = DictionaryEditorWindow.__new__(DictionaryEditorWindow)
    db_manager = MagicMock()
    editor.db_manager = db_manager
    
    term1 = {
        "eng_var": DummyVar("term_eng"),
        "rus_var": DummyVar("term_rus"),
        "category": "noun",
        "is_new": False,
        "is_modified": True,
    }
    
    editor.term_widgets = {"term_eng": term1}
    editor._load_data = MagicMock()
    
    editor._save_all_terms()
    
    db_manager.add_or_update_term.assert_called_once_with("term_eng", "term_rus", "noun")
    editor._load_data.assert_called_once()

def test_dict_editor_delete_term_success(monkeypatch):
    editor = DictionaryEditorWindow.__new__(DictionaryEditorWindow)
    db_manager = MagicMock()
    editor.db_manager = db_manager
    
    master = MagicMock()
    kb_manager = MagicMock()
    master.kb_manager = kb_manager
    editor.master = master
    
    monkeypatch.setattr("tkinter.messagebox.askyesno", lambda title, msg: True)
    editor._load_data = MagicMock()
    
    editor._delete_term("dict_term")
    
    kb_manager.delete_entries.assert_called_once_with(ids=["dict_dict_term"])
    db_manager.delete_term.assert_called_once_with("dict_term")
    editor._load_data.assert_called_once()
    master.update_index_status.assert_called_once()

# --- QuarantineEditorWindow Tests ---

def test_quarantine_editor_load_data():
    editor = QuarantineEditorWindow.__new__(QuarantineEditorWindow)
    editor.search_var = DummyVar("q")
    editor.current_page = 2
    editor.items_per_page = 10
    
    db_manager = MagicMock()
    db_manager.get_paginated_quarantined_terms.return_value = (["item1"], 5)
    editor.db_manager = db_manager
    
    editor._display_page = MagicMock()
    
    editor._load_data()
    
    db_manager.get_paginated_quarantined_terms.assert_called_once_with("q", 2, 10)
    assert editor.total_items == 5
    editor._display_page.assert_called_once_with(["item1"])

def test_quarantine_editor_restore_term():
    editor = QuarantineEditorWindow.__new__(QuarantineEditorWindow)
    db_manager = MagicMock()
    editor.db_manager = db_manager
    editor._load_data = MagicMock()
    
    editor._restore_term(123)
    
    db_manager.restore_term.assert_called_once_with(123)
    editor._load_data.assert_called_once()

def test_quarantine_editor_delete_term(monkeypatch):
    editor = QuarantineEditorWindow.__new__(QuarantineEditorWindow)
    db_manager = MagicMock()
    editor.db_manager = db_manager
    editor._load_data = MagicMock()
    
    monkeypatch.setattr("tkinter.messagebox.askyesno", lambda title, msg: True)
    
    editor._delete_term(456)
    
    db_manager.delete_from_quarantine.assert_called_once_with(456)
    editor._load_data.assert_called_once()
