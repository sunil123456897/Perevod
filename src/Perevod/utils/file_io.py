import logging
import os
import tempfile

logger = logging.getLogger("NovelTranslator.FileIO")


def _normalize_win_path(path: str) -> str:
    """Prepends '\\?\\' to absolute Windows paths if the path length exceeds 250 characters."""
    import sys
    if sys.platform.startswith("win"):
        abs_path = os.path.abspath(path)
        if len(abs_path) > 250 and not abs_path.startswith("\\\\?\\"):
            return "\\\\?\\" + abs_path
        return abs_path
    return path


def tool_read_chapter(path: str) -> str:
    """Reads the content of a chapter file."""
    path = _normalize_win_path(path)
    try:
        with open(path, "r", encoding="utf-8-sig") as f:
            return f.read()
    except Exception as e:
        logger.error(f"Ошибка чтения файла {path}: {e}", exc_info=True)
        raise


def tool_write_chapter(path: str, content: str):
    """Writes content to a chapter file."""
    path = _normalize_win_path(path)
    try:
        abs_path = os.path.abspath(path)
        parent_dir = os.path.dirname(abs_path)
        if parent_dir:
            parent_dir_norm = _normalize_win_path(parent_dir)
            os.makedirs(parent_dir_norm, exist_ok=True)
        temp_path = None
        parent_dir_norm = _normalize_win_path(parent_dir) if parent_dir else None
        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            dir=parent_dir_norm,
            delete=False,
            suffix=".tmp",
        ) as f:
            temp_path = _normalize_win_path(f.name)
            f.write(content)
        try:
            os.replace(temp_path, path)
        except Exception:
            if temp_path and os.path.exists(temp_path):
                os.remove(temp_path)
            raise
    except Exception as e:
        logger.error(f"Ошибка записи в файл {path}: {e}", exc_info=True)
        raise
