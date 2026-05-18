import logging
from logging.handlers import RotatingFileHandler

from Perevod.utils import logging_setup


def test_setup_logging_uses_rotating_file_handler(monkeypatch, tmp_path):
    test_root = tmp_path / "logging"
    test_root.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("NovelTranslator")
    old_handlers = list(logger.handlers)
    for handler in old_handlers:
        logger.removeHandler(handler)

    try:
        monkeypatch.setattr(logging_setup, "PROJECT_ROOT", str(test_root))
        logging_setup.setup_logging()

        file_handlers = [
            handler
            for handler in logger.handlers
            if isinstance(handler, RotatingFileHandler)
        ]
        assert len(file_handlers) == 1
        assert file_handlers[0].maxBytes > 0
        assert file_handlers[0].backupCount >= 1
        assert logging.getLogger("google.generativeai").level == logging.WARNING
        assert logging.getLogger("google.genai").level == logging.WARNING
    finally:
        for handler in list(logger.handlers):
            handler.close()
            logger.removeHandler(handler)
        for handler in old_handlers:
            logger.addHandler(handler)
