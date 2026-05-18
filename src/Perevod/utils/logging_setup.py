# src/Perevod/utils/logging_setup.py
import logging
import os
import sys
from logging.handlers import RotatingFileHandler
from Perevod.config import PROJECT_ROOT


def setup_logging():
    """Настраивает систему логирования для всего приложения."""
    root_logger = logging.getLogger("NovelTranslator")

    if root_logger.handlers:
        return

    root_logger.setLevel(logging.DEBUG)

    log_formatter = logging.Formatter(
        "%(asctime)s - %(name)-15s - %(levelname)-8s - [%(threadName)s] - %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    log_file_path = os.path.join(PROJECT_ROOT, "translation.log")
    file_handler = RotatingFileHandler(
        log_file_path,
        encoding="utf-8",
        mode="a",
        maxBytes=5 * 1024 * 1024,
        backupCount=3,
    )
    file_handler.setFormatter(log_formatter)
    file_handler.setLevel(logging.DEBUG)

    stream_handler = logging.StreamHandler(sys.stdout)
    stream_handler.setFormatter(log_formatter)
    stream_handler.setLevel(logging.INFO)  # Выводим в консоль только INFO и выше

    root_logger.addHandler(file_handler)
    root_logger.addHandler(stream_handler)

    # Уменьшаем "шум" от сторонних библиотек
    logging.getLogger("google.api_core").setLevel(logging.WARNING)
    logging.getLogger("google.generativeai").setLevel(logging.WARNING)
    logging.getLogger("google.genai").setLevel(logging.WARNING)
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)

    root_logger.info("=" * 50)
    root_logger.info("Система логирования успешно настроена. Новый запуск.")
    root_logger.info("=" * 50)
