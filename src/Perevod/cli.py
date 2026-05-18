import logging
import argparse
import time

from Perevod.graph_runner import run_translation_workflow
from Perevod.project_manager import ProjectManager

logger = logging.getLogger("NovelTranslator.CLI")


def _progress_to_log(stage, current, total, message):
    logger.info("[%s %s/%s] %s", stage, current, total, message)


def _clean_overrides(overrides):
    return {key: value for key, value in (overrides or {}).items() if value is not None}


def run_cli_translation(project_name, overrides=None):
    """Запускает перевод проекта без GUI и возвращает process-style exit code."""
    logger.info(f"--- Режим командной строки: Перевод проекта '{project_name}' ---")

    try:
        start_time_cli = time.monotonic()
        project_settings = ProjectManager().get_project_settings(project_name)
        project_settings.update(_clean_overrides(overrides))

        final_state = run_translation_workflow(
            project_name,
            project_settings,
            progress_callback=_progress_to_log,
        )

        if final_state.get("error"):
            raise Exception(final_state["error"])

        duration_cli = time.monotonic() - start_time_cli
        processed_count = len(final_state.get("processed_chapters", []))
        logger.info(
            f"--- Перевод завершен успешно за {duration_cli:.2f} секунд ({processed_count} глав) ---"
        )
        if final_state.get("report_path"):
            logger.info("Отчет перевода: %s", final_state["report_path"])
        return 0

    except Exception as e:
        logger.critical(f"Критическая ошибка во время перевода: {e}", exc_info=True)
        return 1


def main(argv=None):
    parser = argparse.ArgumentParser(description="Run Novel Translator without GUI.")
    parser.add_argument(
        "--project",
        required=True,
        help="Название сохраненного проекта для перевода.",
    )
    parser.add_argument("--input-dir", help="Папка с исходными главами .txt/.md.")
    parser.add_argument("--output-dir", help="Папка для готового перевода.")
    parser.add_argument(
        "--overwrite-existing",
        action="store_true",
        help="Переводить главы заново, даже если выходной файл уже существует.",
    )
    parser.add_argument(
        "--retry-failed",
        action="store_true",
        help="Перезапустить только failed/qa_failed главы из последнего translation_report.json.",
    )
    parser.add_argument(
        "--retry-incomplete",
        action="store_true",
        help="Перезапустить failed/qa_failed и главы с failed stages/warnings из последнего отчета.",
    )
    args = parser.parse_args(argv)
    overrides = {
        "input_dir": args.input_dir,
        "output_dir": args.output_dir,
    }
    if args.overwrite_existing:
        overrides["overwrite_existing"] = True
    if args.retry_failed:
        overrides["retry_failed"] = True
    if args.retry_incomplete:
        overrides["retry_incomplete"] = True
    return run_cli_translation(args.project, _clean_overrides(overrides))


if __name__ == "__main__":
    raise SystemExit(main())
