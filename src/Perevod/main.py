# main.py

import argparse
import logging
import sys
from Perevod.gui.main_window import TranslatorGUI

# ======================================================================================
# Логирование
# ======================================================================================

def setup_logging():
    """Настраивает базовую конфигурацию логирования."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(levelname)s - %(name)s - %(message)s",
        stream=sys.stdout
    )
    # Отключаем слишком подробные логи от сторонних библиотек
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("chromadb.telemetry.posthog").setLevel(logging.WARNING)

# ======================================================================================
# Точка входа
# ======================================================================================

def main():
    """Главная функция для запуска GUI."""
    setup_logging()
    
    parser = argparse.ArgumentParser(description="GUI for Novel Translator.")
    parser.add_argument("--project", type=str, help="Название проекта для загрузки при старте.")
    cli_args = parser.parse_args()

    app = TranslatorGUI(cli_args=cli_args)
    app.mainloop()

if __name__ == "__main__":
    main()