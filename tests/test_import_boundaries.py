import subprocess
import sys


def test_project_manager_import_does_not_import_chromadb():
    command = [
        sys.executable,
        "-c",
        (
            "import sys; "
            "sys.path.insert(0, 'src'); "
            "import Perevod.project_manager; "
            "print('chromadb' in sys.modules)"
        ),
    ]

    completed = subprocess.run(command, capture_output=True, text=True, timeout=30)

    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.strip() == "False"
