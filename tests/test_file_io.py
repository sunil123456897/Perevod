from Perevod.utils import file_io
from Perevod.utils.file_io import tool_backup_file, tool_read_chapter, tool_write_chapter


def test_tool_write_chapter_creates_parent_directories(tmp_path):
    test_root = tmp_path / "file_io"
    output_path = test_root / "nested" / "chapter.txt"

    tool_write_chapter(str(output_path), "Текст главы")

    assert tool_read_chapter(str(output_path)) == "Текст главы"


def test_tool_write_chapter_preserves_existing_file_on_replace_failure(monkeypatch, tmp_path):
    test_root = tmp_path / "file_io_atomic"
    output_path = test_root / "chapter.txt"
    test_root.mkdir(parents=True, exist_ok=True)
    output_path.write_text("Старый текст", encoding="utf-8")

    def fail_replace(src, dst):
        raise OSError("replace failed")

    monkeypatch.setattr(file_io.os, "replace", fail_replace)

    try:
        tool_write_chapter(str(output_path), "Новый текст")
    except OSError:
        pass

    assert output_path.read_text(encoding="utf-8") == "Старый текст"
    assert not list(test_root.glob("*.tmp"))


def test_tool_backup_file_creates_unique_sibling_backup(tmp_path):
    output_path = tmp_path / "chapter.txt"
    output_path.write_text("Старый текст", encoding="utf-8")
    first_backup = output_path.with_name("chapter.txt.bak")
    first_backup.write_text("Первый backup", encoding="utf-8")

    backup_path = tool_backup_file(str(output_path))

    assert backup_path == str(output_path) + ".bak.1"
    assert (tmp_path / "chapter.txt.bak.1").read_text(encoding="utf-8") == "Старый текст"
    assert output_path.read_text(encoding="utf-8") == "Старый текст"


def test_tool_backup_file_returns_none_for_missing_file(tmp_path):
    assert tool_backup_file(str(tmp_path / "missing.txt")) is None


def test_tool_read_chapter_strips_utf8_bom(tmp_path):
    test_root = tmp_path / "file_io_bom"
    input_path = test_root / "chapter.txt"
    test_root.mkdir(parents=True, exist_ok=True)
    input_path.write_bytes("Глава".encode("utf-8-sig"))

    assert tool_read_chapter(str(input_path)) == "Глава"


def test_normalize_win_path_long_path():
    from Perevod.utils.file_io import _normalize_win_path
    import sys

    long_path = "C:\\" + "a" * 300
    norm = _normalize_win_path(long_path)
    if sys.platform.startswith("win"):
        assert norm.startswith("\\\\?\\")
    else:
        assert norm == long_path
