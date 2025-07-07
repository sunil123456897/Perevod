import logging

logger = logging.getLogger("NovelTranslator.FileIO")

def tool_read_chapter(path: str) -> str:
    """Reads the content of a chapter file."""
    try:
        with open(path, 'r', encoding='utf-8') as f:
            return f.read()
    except Exception as e:
        logger.error(f"Ошибка чтения файла {path}: {e}", exc_info=True)
        raise

def tool_write_chapter(path: str, content: str):
    """Writes content to a chapter file."""
    try:
        with open(path, 'w', encoding='utf-8') as f:
            f.write(content)
    except Exception as e:
        logger.error(f"Ошибка записи в файл {path}: {e}", exc_info=True)
        raise
