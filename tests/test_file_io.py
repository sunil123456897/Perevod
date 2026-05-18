from Perevod.utils import file_io
from Perevod.utils.file_io import tool_read_chapter, tool_write_chapter


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
