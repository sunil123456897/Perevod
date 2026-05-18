import subprocess
import sys
from pathlib import Path


def test_run_py_propagates_main_exit_code():
    repo_root = Path(__file__).resolve().parents[1]
    completed = subprocess.run(
        [
            sys.executable,
            "run.py",
            "--doctor",
            "--project",
            "Default",
            "--input-dir",
            "",
            "--output-dir",
            "",
        ],
        cwd=repo_root,
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert completed.returncode == 1
    assert "[FAIL] input_dir" in completed.stdout


def test_start_bat_preserves_python_exit_code():
    repo_root = Path(__file__).resolve().parents[1]
    start_bat = (repo_root / "start.bat").read_text(encoding="utf-8")

    assert "set EXIT_CODE=%ERRORLEVEL%" in start_bat
    assert "exit /b %EXIT_CODE%" in start_bat
