# tests/test_03_graph_nodes.py
from unittest.mock import MagicMock, patch

import pytest

from Perevod.agents.nodes import (
    _dictionary_for_chapter,
    _chapter_index_from_title,
    _plan_translation_chunks,
    _report_progress,
    analysis_node,
    autonomous_curation_node,
    translation_node,
)
from Perevod.utils.api_errors import ApiErrorInfo, GeminiAPIError


class MockResponse:
    def __init__(self, text):
        self.text = text


@pytest.mark.parametrize(
    ("title", "expected"),
    [
        ("Chapter 1", 1),
        ("chapter1", 1),
        ("Ch 585", 585),
        ("ch585", 585),
        ("001_Chapter_2", 2),
        ("Chapter 3: The Beginning", 3),
        ("The Beginning", None),
    ],
)
def test_chapter_index_from_title_handles_common_file_title_formats(title, expected):
    assert _chapter_index_from_title(title) == expected


def test_report_progress_supports_legacy_gui_callback():
    calls = []

    def legacy_callback(value, text):
        calls.append((value, text))

    _report_progress(legacy_callback, "translation", 1, 4, "Глава переведена")

    assert calls == [(25, "Глава переведена")]


def test_plan_translation_chunks_prefers_whole_chapter_until_budget_is_exceeded():
    template = "{dictionary}{context}{style_section}{chunk_notice}{chapter_text}"

    assert _plan_translation_chunks(
        template,
        dictionary="",
        context="",
        style_section="",
        chapter_text="short chapter",
        token_budget=100,
    ) == ["short chapter"]

    assert _plan_translation_chunks(
        template,
        dictionary="",
        context="",
        style_section="",
        chapter_text=("A" * 20) + "\n\n" + ("B" * 20),
        token_budget=6,
    ) == ["A" * 20, "B" * 20]


def test_dictionary_for_chapter_filters_old_terms_but_keeps_current_verdicts():
    dictionary = {
        "Thunder Lotus": "Громовой лотос",
        "Hidden Cave": "Скрытая пещера",
        "Current Term": "Текущий термин",
    }

    assert _dictionary_for_chapter(
        dictionary,
        "The thunder lotus bloomed.",
        always_include_terms={"Current Term"},
    ) == {
        "Thunder Lotus": "Громовой лотос",
        "Current Term": "Текущий термин",
    }


def test_dictionary_for_chapter_avoids_substring_false_positives():
    dictionary = {
        "He": "Он",
        "Dao": "Дао",
        "Thunder-Fire": "Громовой огонь",
    }

    assert _dictionary_for_chapter(
        dictionary,
        "The equipment mentioned a thunder-fire array and Daomark.",
    ) == {
        "Thunder-Fire": "Громовой огонь",
    }


@patch("Perevod.agents.nodes.tool_read_chapter", return_value="Council met in Dawnkeep.")
def test_analysis_node_extracts_terms_from_chapters(mock_read_chapter, base_agent_state):
    analysis_model = base_agent_state["app_context"]["llm_provider"].get_model.return_value
    analysis_model.generate_content.return_value = MockResponse(
        '{"found_terms": ['
        '{"english_term": "Council", "russian_translation": "Совет", '
        '"category": "Faction", "description": "A ruling group."}'
        "]}"
    )

    result = analysis_node(base_agent_state)

    base_agent_state["app_context"]["llm_provider"].get_model.assert_called_once_with(
        "analysis"
    )
    mock_read_chapter.assert_called_once_with("ch1.txt")
    assert result["analysis_results"] == [
            {
                "english_term": "Council",
                "russian_translation": "Совет",
                "category": "Faction",
                "description": "A ruling group.",
                "source_chapter": "ch1.txt",
            }
        ]
    assert result["analysis_errors"] == []
    base_agent_state["app_context"]["db_manager"].mark_chapter_stage.assert_called_with(
        "ch1.txt",
        "analysis_done",
        "done",
    )


@patch("Perevod.agents.nodes.tool_read_chapter", return_value="Council met in Dawnkeep.")
def test_analysis_node_reports_non_fatal_api_errors(mock_read_chapter, base_agent_state):
    analysis_model = base_agent_state["app_context"]["llm_provider"].get_model.return_value
    analysis_model.generate_content.side_effect = RuntimeError("quota exhausted")

    result = analysis_node(base_agent_state)

    assert result["analysis_results"] == []
    assert result["analysis_errors"] == [
        {"title": "ch1.txt", "error": "quota exhausted"}
    ]
    base_agent_state["app_context"]["db_manager"].mark_chapter_stage.assert_called_with(
        "ch1.txt",
        "analysis_done",
        "failed",
        error="quota exhausted",
    )


@patch("Perevod.agents.nodes.tool_read_chapter", return_value="Council met in Dawnkeep.")
def test_analysis_node_preserves_gemini_error_metadata(
    mock_read_chapter, base_agent_state
):
    analysis_model = base_agent_state["app_context"]["llm_provider"].get_model.return_value
    analysis_model.generate_content.side_effect = GeminiAPIError(
        model_name="gemini-3-flash-preview",
        operation="generateContent",
        info=ApiErrorInfo(
            category="quota",
            retryable=False,
            status_code=429,
            message="quota exhausted",
        ),
        original_error=RuntimeError("quota exhausted"),
    )

    result = analysis_node(base_agent_state)

    analysis_error = result["analysis_errors"][0]
    assert analysis_error["title"] == "ch1.txt"
    assert analysis_error["error_category"] == "quota"
    assert analysis_error["error_retryable"] is False
    assert analysis_error["error_status_code"] == 429
    assert analysis_error["error_operation"] == "generateContent"
    assert analysis_error["error_model"] == "gemini-3-flash-preview"


@patch("Perevod.agents.nodes.tool_read_chapter")
def test_analysis_node_skips_chapter_with_done_checkpoint(
    mock_read_chapter, base_agent_state
):
    analysis_model = base_agent_state["app_context"]["llm_provider"].get_model.return_value
    base_agent_state["chapter_runs"] = {
        "ch1.txt": {"stages": {"analysis_done": "done"}}
    }

    result = analysis_node(base_agent_state)

    assert result == {"analysis_results": [], "analysis_errors": []}
    mock_read_chapter.assert_not_called()
    analysis_model.generate_content.assert_not_called()


def test_curation_node_accepts_fenced_json_response(base_agent_state):
    llm_provider = MagicMock()
    db_manager = MagicMock()
    curation_model = llm_provider.get_model.return_value
    curation_model.generate_content.return_value = MockResponse(
        '```json\n{"chosen_variant": "Совет Старейшин"}\n```'
    )
    base_agent_state["app_context"] = {
        "llm_provider": llm_provider,
        "db_manager": db_manager,
    }
    base_agent_state["chapters_to_process"] = [
        {"title": "Chapter 1", "input_path": "ch1.txt", "output_path": "out1.txt"}
    ]
    base_agent_state["analysis_results"] = [
        {
            "english_term": "Council",
            "russian_translation": "Совет",
            "category": "Faction",
        },
        {
            "english_term": "Council",
            "russian_translation": "Совет Старейшин",
            "category": "Faction",
        },
    ]

    result = autonomous_curation_node(base_agent_state)

    assert result["unification_verdicts"] == [
        {
            "english_term": "Council",
            "correct_variant": "Совет Старейшин",
            "category": "Faction",
            "reasoning": "Conflict resolved by LLM. Chosen from ['Совет', 'Совет Старейшин'].",
        }
    ]
    db_manager.mark_chapter_stage.assert_any_call(
        "Chapter 1",
        "glossary_updated",
        "done",
    )


def test_curation_node_preserves_category_for_single_option(base_agent_state):
    base_agent_state["analysis_results"] = [
        {
            "english_term": "Dawnkeep",
            "russian_translation": "Рассветная Крепость",
            "category": "Location",
        }
    ]

    result = autonomous_curation_node(base_agent_state)

    assert result["unification_verdicts"] == [
        {
            "english_term": "Dawnkeep",
            "correct_variant": "Рассветная Крепость",
            "category": "Location",
            "reasoning": "New term, single option.",
        }
    ]


def test_curation_node_skips_when_glossary_checkpoint_done(base_agent_state):
    llm_provider = MagicMock()
    base_agent_state["app_context"] = {
        "llm_provider": llm_provider,
        "db_manager": MagicMock(),
    }
    base_agent_state["chapters_to_process"] = [
        {"title": "Chapter 1", "input_path": "ch1.txt", "output_path": "out1.txt"}
    ]
    base_agent_state["chapter_runs"] = {
        "Chapter 1": {"stages": {"glossary_updated": "done"}}
    }
    base_agent_state["analysis_results"] = [
        {
            "english_term": "Council",
            "russian_translation": "Совет",
            "category": "Faction",
        },
        {
            "english_term": "Council",
            "russian_translation": "Совет Старейшин",
            "category": "Faction",
        },
    ]

    result = autonomous_curation_node(base_agent_state)

    assert result == {"unification_verdicts": []}
    llm_provider.get_model.assert_not_called()

@patch("Perevod.agents.nodes.tool_read_chapter", return_value="English chapter text.")
@patch("Perevod.agents.nodes.tool_write_chapter")
@patch("Perevod.agents.nodes.tool_translate_chunk", return_value="Переведенный текст главы.")
def test_translation_node_whole_chapter(
    mock_translate_chunk, mock_write_chapter, mock_read_chapter, base_agent_state
):
    """
    Тестирует, что translation_node корректно обрабатывает одну главу целиком.
    """
    # 1. Arrange
    state = base_agent_state
    state["chapters_to_process"] = [
        {"title": "Test Chapter", "input_path": "in.txt", "output_path": "out.txt"}
    ]
    state["unification_verdicts"] = [
        {"english_term": "Council", "correct_variant": "Совет", "category": "Faction"}
    ]
    state["app_context"]["db_manager"].get_from_cache.return_value = None

    # 2. Act
    state["rag_context"] = "Relevant context from KB."
    result = translation_node(state)

    # 3. Assert
    # Проверяем, что глава была прочитана
    mock_read_chapter.assert_called_once_with("in.txt")
    
    # Проверяем, что перевод был вызван с правильным промптом
    mock_translate_chunk.assert_called_once()
    call_args, _ = mock_translate_chunk.call_args
    prompt = call_args[1]
    assert '"Council": "Совет"' in prompt
    assert "English chapter text." in prompt
    assert "Relevant context from KB." in prompt
    assert "Do not summarize" in prompt
    assert "Translate every sentence" in prompt
    
    # Сырой API-перевод кэшируется только после успешной проверки judge.
    state["app_context"]["db_manager"].add_to_cache.assert_not_called()

    # Проверяем, что результат был записан
    mock_write_chapter.assert_called_once_with("out.txt", "Переведенный текст главы.")
    
    # Проверяем, что узел вернул правильное состояние
    assert "processed_chapters" in result
    assert len(result["processed_chapters"]) == 1
    assert result["processed_chapters"][0]["title"] == "Test Chapter"
    assert result["processed_chapters"][0]["translation_source"] == "api"
    assert result["processed_chapters"][0]["translation_mode"] == "whole_chapter"
    assert result["processed_chapters"][0]["translation_chunk_count"] == 1
    state["app_context"]["db_manager"].add_or_update_term.assert_called_once_with(
        "Council", "Совет", "Faction"
    )
    state["app_context"]["db_manager"].mark_chapter_stage.assert_any_call(
        "Test Chapter",
        "translation_done",
        "done",
    )


@patch("Perevod.agents.nodes.tool_read_chapter", return_value="English chapter text.")
@patch("Perevod.agents.nodes.tool_backup_file", return_value="out.txt.bak")
@patch("Perevod.agents.nodes.tool_write_chapter")
@patch("Perevod.agents.nodes.tool_translate_chunk", return_value="Новый перевод.")
def test_translation_node_backs_up_existing_output_before_overwrite(
    mock_translate_chunk,
    mock_write_chapter,
    mock_backup_file,
    mock_read_chapter,
    base_agent_state,
):
    state = base_agent_state
    state["chapters_to_process"] = [
        {
            "title": "Test Chapter",
            "input_path": "in.txt",
            "output_path": "out.txt",
            "backup_existing_output": True,
        }
    ]
    state["app_context"]["db_manager"].get_from_cache.return_value = None

    result = translation_node(state)

    assert result["error"] is None
    mock_backup_file.assert_called_once_with("out.txt")
    mock_write_chapter.assert_called_once_with("out.txt", "Новый перевод.")
    assert result["processed_chapters"][0]["output_backup_path"] == "out.txt.bak"
    state["app_context"]["db_manager"].mark_chapter_stage.assert_any_call(
        "Test Chapter",
        "output_written",
        "done",
    )


@patch("Perevod.agents.nodes.tool_read_chapter", side_effect=["First text.", "Second text."])
@patch("Perevod.agents.nodes.tool_write_chapter")
@patch("Perevod.agents.nodes.tool_translate_chunk", side_effect=["Первый.", "Второй."])
def test_translation_node_uses_chapter_specific_context(
    mock_translate_chunk, mock_write_chapter, mock_read_chapter, base_agent_state
):
    state = base_agent_state
    state["chapters_to_process"] = [
        {"title": "Chapter 1", "input_path": "in1.txt", "output_path": "out1.txt"},
        {"title": "Chapter 2", "input_path": "in2.txt", "output_path": "out2.txt"},
    ]
    state["chapter_contexts"] = {
        "Chapter 1": "Context only for chapter one.",
        "Chapter 2": "Context only for chapter two.",
    }
    state["rag_context"] = "Global fallback context."
    state["app_context"]["db_manager"].get_from_cache.return_value = None

    result = translation_node(state)

    assert result["error"] is None
    first_prompt = mock_translate_chunk.call_args_list[0].args[1]
    second_prompt = mock_translate_chunk.call_args_list[1].args[1]
    assert "Context only for chapter one." in first_prompt
    assert "Context only for chapter two." not in first_prompt
    assert "Context only for chapter two." in second_prompt
    assert "Context only for chapter one." not in second_prompt
    assert result["processed_chapters"][0]["relevant_context"] == "Context only for chapter one."
    assert result["processed_chapters"][1]["relevant_context"] == "Context only for chapter two."


@patch("Perevod.agents.nodes.tool_read_chapter", side_effect=["First text.", "Second text."])
@patch("Perevod.agents.nodes.tool_write_chapter")
@patch("Perevod.agents.nodes.tool_translate_chunk", side_effect=["Первый.", "Второй."])
def test_translation_node_does_not_leak_global_context_between_chapters(
    mock_translate_chunk, mock_write_chapter, mock_read_chapter, base_agent_state
):
    state = base_agent_state
    state["chapters_to_process"] = [
        {"title": "Chapter 1", "input_path": "in1.txt", "output_path": "out1.txt"},
        {"title": "Chapter 2", "input_path": "in2.txt", "output_path": "out2.txt"},
    ]
    state["chapter_contexts"] = {
        "Chapter 1": "Context only for chapter one.",
    }
    state["rag_context"] = "Global fallback context."
    state["app_context"]["db_manager"].get_from_cache.return_value = None

    result = translation_node(state)

    assert result["error"] is None
    first_prompt = mock_translate_chunk.call_args_list[0].args[1]
    second_prompt = mock_translate_chunk.call_args_list[1].args[1]
    assert "Context only for chapter one." in first_prompt
    assert "Global fallback context." not in first_prompt
    assert "Global fallback context." not in second_prompt
    assert result["processed_chapters"][1]["relevant_context"] == ""


@patch("Perevod.agents.nodes.tool_read_chapter", return_value="English chapter text.")
@patch("Perevod.agents.nodes.tool_write_chapter")
@patch("Perevod.agents.nodes.tool_translate_chunk", return_value="Переведенный текст главы.")
def test_translation_node_includes_project_style_guide(
    mock_translate_chunk, mock_write_chapter, mock_read_chapter, base_agent_state
):
    state = base_agent_state
    state["project_settings"] = {"style_guide": "Use restrained literary Russian."}
    state["chapters_to_process"] = [
        {"title": "Test Chapter", "input_path": "in.txt", "output_path": "out.txt"}
    ]
    state["unification_verdicts"] = []
    state["app_context"]["db_manager"].get_from_cache.return_value = None
    state["rag_context"] = ""

    translation_node(state)

    _, prompt, _ = mock_translate_chunk.call_args.args
    assert "PROJECT STYLE GUIDE" in prompt
    assert "Use restrained literary Russian." in prompt


@patch("Perevod.agents.nodes.tool_read_chapter", return_value="Thunder Lotus blooms.")
@patch("Perevod.agents.nodes.tool_write_chapter")
@patch("Perevod.agents.nodes.tool_translate_chunk", return_value="Громовой лотос цветет.")
def test_translation_node_includes_existing_project_dictionary(
    mock_translate_chunk, mock_write_chapter, mock_read_chapter, base_agent_state
):
    state = base_agent_state
    state["chapters_to_process"] = [
        {"title": "Test Chapter", "input_path": "in.txt", "output_path": "out.txt"}
    ]
    state["unification_verdicts"] = []
    state["app_context"]["db_manager"].get_from_cache.return_value = None
    state["app_context"]["db_manager"].get_terms_dictionary.return_value = {
        "Thunder Lotus": {"russian_term": "Громовой лотос", "category": "Item"},
        "Hidden Cave": {"russian_term": "Скрытая пещера", "category": "Location"},
    }

    result = translation_node(state)

    assert result["error"] is None
    _, prompt, _ = mock_translate_chunk.call_args.args
    assert '"Thunder Lotus": "Громовой лотос"' in prompt
    assert "Hidden Cave" not in prompt
    mock_write_chapter.assert_called_once_with("out.txt", "Громовой лотос цветет.")


@patch("Perevod.agents.nodes.tool_read_chapter", return_value="English chapter text.")
@patch("Perevod.agents.nodes.tool_write_chapter")
@patch("Perevod.agents.nodes.tool_translate_chunk", return_value="Переведенный текст главы.")
def test_translation_node_continues_when_kb_upsert_fails(
    mock_translate_chunk, mock_write_chapter, mock_read_chapter, base_agent_state
):
    state = base_agent_state
    state["chapters_to_process"] = [
        {"title": "Test Chapter", "input_path": "in.txt", "output_path": "out.txt"}
    ]
    state["unification_verdicts"] = [
        {
            "english_term": "Spirit Lotus",
            "correct_variant": "Духовный лотос",
            "category": "term",
        }
    ]
    state["app_context"]["db_manager"].get_from_cache.return_value = None
    state["rag_context"] = ""
    state["app_context"]["kb_manager"].upsert_from_verdicts.side_effect = RuntimeError(
        "embedding model unavailable"
    )

    result = translation_node(state)

    assert result["error"] is None
    assert len(result["processed_chapters"]) == 1
    mock_translate_chunk.assert_called_once()


@patch("Perevod.agents.nodes.tool_read_chapter", return_value="English chapter text.")
@patch("Perevod.agents.nodes.tool_write_chapter")
@patch("Perevod.agents.nodes.tool_translate_chunk", return_value="Переведенный текст главы.")
def test_translation_cache_key_changes_with_model_and_context(
    mock_translate_chunk, mock_write_chapter, mock_read_chapter, base_agent_state
):
    state = base_agent_state
    state["chapters_to_process"] = [
        {"title": "Test Chapter", "input_path": "in.txt", "output_path": "out.txt"}
    ]
    state["app_context"]["db_manager"].get_from_cache.return_value = None
    state["app_context"]["llm_provider"].model_configs = {"translation": "model-a"}
    state["rag_context"] = "Context A"

    translation_node(state)
    cache_key_for_model_a = state["app_context"]["db_manager"].get_from_cache.call_args.args[0]

    state["app_context"]["db_manager"].get_from_cache.reset_mock()
    state["app_context"]["db_manager"].add_to_cache.reset_mock()
    state["app_context"]["llm_provider"].model_configs = {"translation": "model-b"}
    state["rag_context"] = "Context B"

    translation_node(state)
    cache_key_for_model_b = state["app_context"]["db_manager"].get_from_cache.call_args.args[0]

    assert cache_key_for_model_a != cache_key_for_model_b


@patch("Perevod.agents.nodes.tool_read_chapter", return_value="English chapter text.")
@patch("Perevod.agents.nodes.tool_write_chapter")
@patch("Perevod.agents.nodes.tool_translate_chunk", return_value="Переведенный текст главы.")
def test_translation_cache_key_changes_with_style_guide(
    mock_translate_chunk, mock_write_chapter, mock_read_chapter, base_agent_state
):
    state = base_agent_state
    state["chapters_to_process"] = [
        {"title": "Test Chapter", "input_path": "in.txt", "output_path": "out.txt"}
    ]
    state["app_context"]["db_manager"].get_from_cache.return_value = None
    state["app_context"]["llm_provider"].model_configs = {"translation": "model-a"}
    state["rag_context"] = "Context A"
    state["project_settings"] = {"style_guide": "Use restrained literary Russian."}

    translation_node(state)
    restrained_key = state["app_context"]["db_manager"].get_from_cache.call_args.args[0]

    state["app_context"]["db_manager"].get_from_cache.reset_mock()
    state["app_context"]["db_manager"].add_to_cache.reset_mock()
    state["project_settings"] = {"style_guide": "Use archaic epic diction."}

    translation_node(state)
    archaic_key = state["app_context"]["db_manager"].get_from_cache.call_args.args[0]

    assert restrained_key != archaic_key


@patch("Perevod.agents.nodes.tool_read_chapter", return_value="English chapter text.")
@patch("Perevod.agents.nodes.tool_write_chapter")
@patch("Perevod.agents.nodes.tool_translate_chunk", return_value="Переведенный текст главы.")
def test_translation_cache_key_changes_with_generation_settings(
    mock_translate_chunk, mock_write_chapter, mock_read_chapter, base_agent_state
):
    state = base_agent_state
    state["chapters_to_process"] = [
        {"title": "Test Chapter", "input_path": "in.txt", "output_path": "out.txt"}
    ]
    state["app_context"]["db_manager"].get_from_cache.return_value = None
    state["app_context"]["llm_provider"].model_configs = {"translation": "model-a"}
    state["rag_context"] = "Context A"
    state["project_settings"] = {
        "temperature": 0.2,
        "top_p": 0.8,
    }

    translation_node(state)
    conservative_key = state["app_context"]["db_manager"].get_from_cache.call_args.args[0]

    state["app_context"]["db_manager"].get_from_cache.reset_mock()
    state["project_settings"] = {
        "temperature": 0.7,
        "top_p": 0.95,
    }

    translation_node(state)
    creative_key = state["app_context"]["db_manager"].get_from_cache.call_args.args[0]

    assert conservative_key != creative_key


@patch("Perevod.agents.nodes.tool_read_chapter", return_value="English chapter text.")
@patch("Perevod.agents.nodes.tool_write_chapter")
@patch("Perevod.agents.nodes.tool_translate_chunk", return_value="Переведенный текст главы.")
def test_translation_node_reports_progress_for_whole_chapters(
    mock_translate_chunk, mock_write_chapter, mock_read_chapter, base_agent_state
):
    progress_callback = MagicMock()
    state = base_agent_state
    state["progress_callback"] = progress_callback
    state["chapters_to_process"] = [
        {"title": "Chapter 1", "input_path": "in1.txt", "output_path": "out1.txt"},
        {"title": "Chapter 2", "input_path": "in2.txt", "output_path": "out2.txt"},
    ]
    state["app_context"]["db_manager"].get_from_cache.return_value = None

    result = translation_node(state)

    assert len(result["processed_chapters"]) == 2
    assert result["error"] is None
    assert progress_callback.call_args_list[0].args == (
        "translation",
        0,
        2,
        "Запуск перевода глав",
    )
    assert progress_callback.call_args_list[-1].args == (
        "translation",
        2,
        2,
        "Глава 'Chapter 2' переведена",
    )


@patch("Perevod.agents.nodes._plan_translation_chunks", return_value=["Part one.", "Part two."])
@patch("Perevod.agents.nodes.tool_read_chapter", return_value="Long English chapter.")
@patch("Perevod.agents.nodes.tool_write_chapter")
@patch(
    "Perevod.agents.nodes.tool_translate_chunk",
    side_effect=["Первая часть.", "Вторая часть."],
)
def test_translation_node_translates_planned_chunks_and_reassembles_chapter(
    mock_translate_chunk,
    mock_write_chapter,
    mock_read_chapter,
    mock_plan_chunks,
    base_agent_state,
):
    state = base_agent_state
    state["chapters_to_process"] = [
        {"title": "Long Chapter", "input_path": "in.txt", "output_path": "out.txt"}
    ]
    state["app_context"]["db_manager"].get_from_cache.return_value = None

    result = translation_node(state)

    assert result["error"] is None
    assert len(result["processed_chapters"]) == 1
    assert mock_translate_chunk.call_count == 2
    assert result["processed_chapters"][0]["translation_source"] == "api"
    assert result["processed_chapters"][0]["translation_mode"] == "chunked"
    assert result["processed_chapters"][0]["translation_chunk_count"] == 2
    assert "part 1/2" in mock_translate_chunk.call_args_list[0].args[1]
    assert "part 2/2" in mock_translate_chunk.call_args_list[1].args[1]
    mock_write_chapter.assert_called_once_with(
        "out.txt",
        "Первая часть.\n\nВторая часть.",
    )
    mock_plan_chunks.assert_called_once()


@patch("Perevod.agents.nodes._plan_translation_chunks", return_value=["Part one.", "Part two."])
@patch("Perevod.agents.nodes.tool_read_chapter", return_value="Long English chapter.")
@patch("Perevod.agents.nodes.tool_write_chapter")
@patch(
    "Perevod.agents.nodes.tool_translate_chunk",
    side_effect=["Первая часть.", "   "],
)
def test_translation_node_fails_when_chunk_translation_is_empty(
    mock_translate_chunk,
    mock_write_chapter,
    mock_read_chapter,
    mock_plan_chunks,
    base_agent_state,
):
    state = base_agent_state
    state["chapters_to_process"] = [
        {"title": "Long Chapter", "input_path": "in.txt", "output_path": "out.txt"}
    ]
    state["app_context"]["db_manager"].get_from_cache.return_value = None

    result = translation_node(state)

    assert result["processed_chapters"] == []
    assert len(result["failed_chapters"]) == 1
    assert result["failed_chapters"][0]["title"] == "Long Chapter"
    assert "Long Chapter" in result["error"]
    assert "часть 2/2" in result["error"]
    assert "пустой перевод" in result["error"]
    state["app_context"]["db_manager"].add_to_cache.assert_not_called()
    mock_write_chapter.assert_not_called()


@patch("Perevod.agents.nodes.tool_read_chapter", side_effect=["First text.", "Second text."])
@patch("Perevod.agents.nodes.tool_write_chapter")
@patch(
    "Perevod.agents.nodes.tool_translate_chunk",
    side_effect=["Первый перевод.", RuntimeError("quota exhausted")],
)
def test_translation_node_preserves_successes_and_returns_error(
    mock_translate_chunk, mock_write_chapter, mock_read_chapter, base_agent_state
):
    state = base_agent_state
    state["chapters_to_process"] = [
        {"title": "Chapter 1", "input_path": "in1.txt", "output_path": "out1.txt"},
        {"title": "Chapter 2", "input_path": "in2.txt", "output_path": "out2.txt"},
    ]
    state["app_context"]["db_manager"].get_from_cache.return_value = None

    result = translation_node(state)

    # Chapter 1 succeeded and is recorded.
    assert result["processed_chapters"][0]["title"] == "Chapter 1"
    assert result["processed_chapters"][0]["input_path"] == "in1.txt"
    assert result["processed_chapters"][0]["output_path"] == "out1.txt"
    assert result["processed_chapters"][0]["cache_key"]
    # Chapter 2 failed but did NOT abort the loop — it is reported as a
    # recoverable partial failure so resume can retry it later.
    assert len(result["failed_chapters"]) == 1
    assert result["failed_chapters"][0]["title"] == "Chapter 2"
    assert "Chapter 2" in result["error"]
    assert "quota exhausted" in result["error"]
    mock_write_chapter.assert_called_once_with("out1.txt", "Первый перевод.")


@patch("Perevod.agents.nodes.tool_read_chapter", return_value="English chapter text.")
@patch("Perevod.agents.nodes.tool_write_chapter")
@patch("Perevod.agents.nodes.tool_translate_chunk", return_value="   ")
def test_translation_node_treats_empty_translation_as_failure(
    mock_translate_chunk, mock_write_chapter, mock_read_chapter, base_agent_state
):
    state = base_agent_state
    state["chapters_to_process"] = [
        {"title": "Chapter 1", "input_path": "in1.txt", "output_path": "out1.txt"}
    ]
    state["app_context"]["db_manager"].get_from_cache.return_value = None

    result = translation_node(state)

    assert result["processed_chapters"] == []
    assert "Chapter 1" in result["error"]
    assert "пустой перевод" in result["error"]
    state["app_context"]["db_manager"].add_to_cache.assert_not_called()
    mock_write_chapter.assert_not_called()


@patch("Perevod.agents.nodes.tool_read_chapter", return_value="Spirit Lotus blooms.")
@patch("Perevod.agents.nodes.tool_write_chapter")
@patch("Perevod.agents.nodes.tool_translate_chunk", return_value="Лотос цветет.")
def test_translation_node_does_not_cache_sanity_failed_api_translation(
    mock_translate_chunk, mock_write_chapter, mock_read_chapter, base_agent_state
):
    state = base_agent_state
    state["chapters_to_process"] = [
        {"title": "Chapter 1", "input_path": "in1.txt", "output_path": "out1.txt"}
    ]
    state["app_context"]["db_manager"].get_from_cache.return_value = None
    state["app_context"]["db_manager"].get_terms_dictionary.return_value = {
        "Spirit Lotus": {"russian_term": "Духовный лотос", "category": "Item"}
    }

    result = translation_node(state)

    assert result["error"] is None
    mock_write_chapter.assert_called_once_with("out1.txt", "Лотос цветет.")
    state["app_context"]["db_manager"].add_to_cache.assert_not_called()


@patch("Perevod.agents.nodes.tool_read_chapter", return_value="English chapter text.")
@patch("Perevod.agents.nodes.tool_write_chapter")
@patch("Perevod.agents.nodes.tool_translate_chunk")
def test_translation_node_treats_blank_cached_translation_as_failure(
    mock_translate_chunk, mock_write_chapter, mock_read_chapter, base_agent_state
):
    state = base_agent_state
    state["chapters_to_process"] = [
        {"title": "Chapter 1", "input_path": "in1.txt", "output_path": "out1.txt"}
    ]
    state["app_context"]["db_manager"].get_from_cache.return_value = "   "

    result = translation_node(state)

    assert result["processed_chapters"] == []
    assert "Chapter 1" in result["error"]
    assert "кэш вернул пустой перевод" in result["error"]
    state["app_context"]["db_manager"].add_to_cache.assert_not_called()
    state["app_context"]["db_manager"].delete_from_cache.assert_called_once()
    mock_translate_chunk.assert_not_called()
    mock_write_chapter.assert_not_called()


@patch(
    "Perevod.agents.nodes.tool_read_chapter",
    side_effect=["English chapter text.", "Existing translation."],
)
@patch("Perevod.agents.nodes.tool_write_chapter")
@patch("Perevod.agents.nodes.tool_translate_chunk")
def test_translation_node_reuses_existing_output_for_post_translation_retry(
    mock_translate_chunk, mock_write_chapter, mock_read_chapter, base_agent_state
):
    state = base_agent_state
    state["chapters_to_process"] = [
        {
            "title": "Chapter 1",
            "input_path": "in1.txt",
            "output_path": "out1.txt",
            "reuse_existing_translation": True,
        }
    ]
    state["app_context"]["db_manager"].get_from_cache.return_value = None

    result = translation_node(state)

    assert result["error"] is None
    assert result["processed_chapters"][0]["title"] == "Chapter 1"
    assert result["processed_chapters"][0]["reused_existing_translation"] is True
    assert result["processed_chapters"][0]["translation_source"] == "existing_file"
    assert result["processed_chapters"][0]["translation_mode"] == "existing_file"
    assert result["processed_chapters"][0]["translation_chunk_count"] == 0
    mock_read_chapter.assert_any_call("in1.txt")
    mock_read_chapter.assert_any_call("out1.txt")
    mock_translate_chunk.assert_not_called()
    mock_write_chapter.assert_not_called()


@patch(
    "Perevod.agents.nodes.tool_read_chapter",
    side_effect=["English chapter text.", "   "],
)
@patch("Perevod.agents.nodes.tool_write_chapter")
@patch("Perevod.agents.nodes.tool_translate_chunk")
def test_translation_node_treats_blank_reused_translation_as_failure(
    mock_translate_chunk, mock_write_chapter, mock_read_chapter, base_agent_state
):
    state = base_agent_state
    state["chapters_to_process"] = [
        {
            "title": "Chapter 1",
            "input_path": "in1.txt",
            "output_path": "out1.txt",
            "reuse_existing_translation": True,
        }
    ]
    state["app_context"]["db_manager"].get_from_cache.return_value = None

    result = translation_node(state)

    assert result["processed_chapters"] == []
    assert "Chapter 1" in result["error"]
    assert "существующий файл вернул пустой перевод" in result["error"]
    mock_translate_chunk.assert_not_called()
    mock_write_chapter.assert_not_called()
