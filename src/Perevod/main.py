# src/Perevod/main.py
import argparse
import sys
import tkinter as tk
from tkinter import messagebox
from pydantic import ValidationError

# Попытка импортировать настройки до всего остального
try:
    # ИСПРАВЛЕНИЕ: Импортируем из нового места
    from Perevod.utils.logging_setup import setup_logging
except ValidationError as e:
    root = tk.Tk()
    root.withdraw()
    messagebox.showerror(
        "Ошибка конфигурации",
        f"Не удалось загрузить настройки. Убедитесь, что файл .env существует и содержит GOOGLE_API_KEY.\n\nОшибка: {e}",
    )
    sys.exit(1)
except ImportError as e:
    # Обработка других возможных ошибок импорта
    root = tk.Tk()
    root.withdraw()
    messagebox.showerror(
        "Ошибка импорта", f"Не удалось загрузить компоненты приложения.\n\nОшибка: {e}"
    )
    sys.exit(1)

def main(argv=None):
    """Главная функция для запуска GUI."""
    # Настраиваем логирование в самом начале
    setup_logging()

    parser = argparse.ArgumentParser(description="GUI for Novel Translator.")
    parser.add_argument(
        "--project", type=str, help="Название проекта для загрузки при старте."
    )
    parser.add_argument(
        "--cli",
        action="store_true",
        help="Запустить перевод выбранного проекта без GUI.",
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
    parser.add_argument(
        "--rejudge-existing",
        action="store_true",
        help="Перепроверить судьёй уже переведённые главы по текущим правилам QA.",
    )
    parser.add_argument(
        "--chapters",
        help="Обрабатывать только указанные главы, например '604-871' или '604,610,620'.",
    )
    parser.add_argument(
        "--doctor",
        action="store_true",
        help="Проверить окружение и настройки проекта без запуска GUI.",
    )
    parser.add_argument(
        "--check-api",
        action="store_true",
        help="Включить live API проверки в doctor-режиме, если доступен VPN/API.",
    )
    parser.add_argument(
        "--api-timeout",
        type=int,
        default=20,
        help="Таймаут live API проверки Gemini в секундах.",
    )
    cli_args = parser.parse_args(argv)

    if cli_args.doctor:
        from Perevod.doctor import main as doctor_main

        doctor_args = ["--project", cli_args.project or "Default"]
        if cli_args.input_dir:
            doctor_args.extend(["--input-dir", cli_args.input_dir])
        if cli_args.output_dir:
            doctor_args.extend(["--output-dir", cli_args.output_dir])
        if cli_args.check_api:
            doctor_args.append("--check-api")
            doctor_args.extend(["--api-timeout", str(cli_args.api_timeout)])
        return doctor_main(doctor_args)

    if cli_args.cli:
        if not cli_args.project:
            parser.error("--cli requires --project")
        from Perevod.cli import run_cli_translation

        overrides = {
            "input_dir": cli_args.input_dir,
            "output_dir": cli_args.output_dir,
        }
        if cli_args.overwrite_existing:
            overrides["overwrite_existing"] = True
        if cli_args.retry_failed:
            overrides["retry_failed"] = True
        if cli_args.retry_incomplete:
            overrides["retry_incomplete"] = True
        if cli_args.rejudge_existing:
            overrides["rejudge_existing"] = True
        if cli_args.chapters:
            overrides["chapter_filter"] = cli_args.chapters
        return run_cli_translation(
            cli_args.project,
            {key: value for key, value in overrides.items() if value is not None},
        )

    from Perevod.gui.main_window import TranslatorGUI

    app = TranslatorGUI(cli_args=cli_args)
    app.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
