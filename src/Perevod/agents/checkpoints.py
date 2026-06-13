import logging

logger = logging.getLogger("NovelTranslator.Checkpoints")


def chapter_stage_done(state: dict, title: str | None, stage: str) -> bool:
    if not title:
        return False
    chapter_run = (state.get("chapter_runs") or {}).get(title) or {}
    return (chapter_run.get("stages") or {}).get(stage) == "done"


def get_chapter_checkpoint_value(state: dict, title: str | None, key: str):
    if not title:
        return None
    chapter_run = (state.get("chapter_runs") or {}).get(title) or {}
    return chapter_run.get(key)


def mark_chapter_stage(
    db_manager,
    title: str | None,
    stage: str,
    state: str,
    *,
    error: str | None = None,
) -> None:
    """Best-effort chapter checkpoint update."""
    if not db_manager or not title:
        return
    marker = getattr(db_manager, "mark_chapter_stage", None)
    if not marker:
        return
    try:
        marker(title, stage, state, error=error) if error else marker(title, stage, state)
    except Exception as exc:
        logger.warning(
            "Не удалось обновить checkpoint главы '%s' (%s=%s): %s",
            title,
            stage,
            state,
            exc,
            exc_info=True,
        )


def update_chapter_context(db_manager, title: str | None, context_text: str) -> None:
    if not db_manager or not title:
        return
    updater = getattr(db_manager, "update_chapter_context", None)
    if not updater:
        return
    try:
        updater(title, context_text)
    except Exception as exc:
        logger.warning(
            "Не удалось сохранить context checkpoint главы '%s': %s",
            title,
            exc,
            exc_info=True,
        )


def update_chapter_judge_result(
    db_manager,
    title: str | None,
    judge_result: dict,
) -> None:
    if not db_manager or not title:
        return
    updater = getattr(db_manager, "update_chapter_judge_result", None)
    if not updater:
        return
    try:
        updater(title, judge_result)
    except Exception as exc:
        logger.warning(
            "Не удалось сохранить judge checkpoint главы '%s': %s",
            title,
            exc,
            exc_info=True,
        )


def update_chapter_refine_result(
    db_manager,
    title: str | None,
    refine_result: dict,
) -> None:
    if not db_manager or not title:
        return
    updater = getattr(db_manager, "update_chapter_refine_result", None)
    if not updater:
        return
    try:
        updater(title, refine_result)
    except Exception as exc:
        logger.warning(
            "Не удалось сохранить refine checkpoint главы '%s': %s",
            title,
            exc,
            exc_info=True,
        )


def update_chapter_summary_result(
    db_manager,
    title: str | None,
    summary_result: dict,
) -> None:
    if not db_manager or not title:
        return
    updater = getattr(db_manager, "update_chapter_summary_result", None)
    if not updater:
        return
    try:
        updater(title, summary_result)
    except Exception as exc:
        logger.warning(
            "Не удалось сохранить summary checkpoint главы '%s': %s",
            title,
            exc,
            exc_info=True,
        )
