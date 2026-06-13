from unittest.mock import MagicMock

import pytest

from Perevod.agents import checkpoints


def test_chapter_stage_done_reads_per_chapter_stage_status():
    state = {
        "chapter_runs": {
            "Chapter 1": {"stages": {"context_retrieved": "done"}},
            "Chapter 2": {"stages": {"context_retrieved": "failed"}},
        }
    }

    assert checkpoints.chapter_stage_done(state, "Chapter 1", "context_retrieved") is True
    assert checkpoints.chapter_stage_done(state, "Chapter 2", "context_retrieved") is False
    assert checkpoints.chapter_stage_done(state, None, "context_retrieved") is False


def test_get_chapter_checkpoint_value_is_scoped_to_requested_chapter():
    state = {
        "chapter_runs": {
            "Chapter 1": {"context": "chapter 1 context"},
            "Chapter 2": {"context": "chapter 2 context"},
        }
    }

    assert (
        checkpoints.get_chapter_checkpoint_value(state, "Chapter 2", "context")
        == "chapter 2 context"
    )
    assert checkpoints.get_chapter_checkpoint_value(state, None, "context") is None


def test_mark_chapter_stage_delegates_error_to_db_manager():
    db_manager = MagicMock()

    checkpoints.mark_chapter_stage(
        db_manager,
        "Chapter 1",
        "judge_done",
        "failed",
        error="invalid judge response",
    )

    db_manager.mark_chapter_stage.assert_called_once_with(
        "Chapter 1",
        "judge_done",
        "failed",
        error="invalid judge response",
    )


def test_mark_chapter_stage_delegates_without_error_to_db_manager():
    db_manager = MagicMock()

    checkpoints.mark_chapter_stage(
        db_manager,
        "Chapter 1",
        "context_retrieved",
        "done",
    )

    db_manager.mark_chapter_stage.assert_called_once_with(
        "Chapter 1",
        "context_retrieved",
        "done",
    )


def test_mark_chapter_stage_ignores_missing_db_title_or_method(caplog):
    checkpoints.mark_chapter_stage(None, "Chapter 1", "context_retrieved", "done")
    checkpoints.mark_chapter_stage(MagicMock(), None, "context_retrieved", "done")
    checkpoints.mark_chapter_stage(object(), "Chapter 1", "context_retrieved", "done")

    assert caplog.text == ""


def test_mark_chapter_stage_is_best_effort(caplog):
    db_manager = MagicMock()
    db_manager.mark_chapter_stage.side_effect = RuntimeError("db locked")

    checkpoints.mark_chapter_stage(
        db_manager,
        "Chapter 1",
        "context_retrieved",
        "done",
    )

    assert "Не удалось обновить checkpoint главы" in caplog.text


def test_checkpoint_updates_are_best_effort(caplog):
    db_manager = MagicMock()
    db_manager.update_chapter_context.side_effect = RuntimeError("db locked")

    checkpoints.update_chapter_context(db_manager, "Chapter 1", "context")

    assert "Не удалось сохранить context checkpoint главы" in caplog.text


@pytest.mark.parametrize(
    "helper,args",
    [
        (checkpoints.update_chapter_context, ("context",)),
        (checkpoints.update_chapter_judge_result, ({"pass_check": True},)),
        (checkpoints.update_chapter_refine_result, ({"refined": True},)),
        (checkpoints.update_chapter_summary_result, ({"summary": "Chapter summary"},)),
    ],
)
def test_artifact_updates_ignore_missing_db_title_or_method(helper, args, caplog):
    helper(None, "Chapter 1", *args)
    helper(MagicMock(), None, *args)
    helper(object(), "Chapter 1", *args)

    assert caplog.text == ""


@pytest.mark.parametrize(
    "helper,method_name,args,expected_log",
    [
        (
            checkpoints.update_chapter_judge_result,
            "update_chapter_judge_result",
            ({"pass_check": True},),
            "Не удалось сохранить judge checkpoint главы",
        ),
        (
            checkpoints.update_chapter_refine_result,
            "update_chapter_refine_result",
            ({"refined": True},),
            "Не удалось сохранить refine checkpoint главы",
        ),
        (
            checkpoints.update_chapter_summary_result,
            "update_chapter_summary_result",
            ({"summary": "Chapter summary"},),
            "Не удалось сохранить summary checkpoint главы",
        ),
    ],
)
def test_structured_artifact_updates_are_best_effort(
    helper,
    method_name,
    args,
    expected_log,
    caplog,
):
    db_manager = MagicMock()
    getattr(db_manager, method_name).side_effect = RuntimeError("db locked")

    helper(db_manager, "Chapter 1", *args)

    assert expected_log in caplog.text


def test_artifact_update_helpers_delegate_to_db_manager():
    db_manager = MagicMock()
    judge_result = {"pass_check": False, "blocking_issues": ["term drift"]}
    refine_result = {"refined": True, "issues_fixed": ["term drift"]}
    summary_result = {"summary": "Chapter summary"}

    checkpoints.update_chapter_judge_result(db_manager, "Chapter 1", judge_result)
    checkpoints.update_chapter_refine_result(db_manager, "Chapter 1", refine_result)
    checkpoints.update_chapter_summary_result(db_manager, "Chapter 1", summary_result)

    db_manager.update_chapter_judge_result.assert_called_once_with(
        "Chapter 1",
        judge_result,
    )
    db_manager.update_chapter_refine_result.assert_called_once_with(
        "Chapter 1",
        refine_result,
    )
    db_manager.update_chapter_summary_result.assert_called_once_with(
        "Chapter 1",
        summary_result,
    )
