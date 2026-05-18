from scripts.check_repo_cleanliness import find_tracked_artifacts, is_tracked_artifact


def test_repo_cleanliness_flags_generated_and_local_data_paths():
    tracked_files = [
        ".env",
        "src/Perevod/__pycache__/main.cpython-312.pyc",
        "Perevod.egg-info/PKG-INFO",
        "_project_files/Fermer/project.db",
        "src/_project_files/projects_main.db",
        "program_files_for_neronka/src_Perevod_graph_runner.py.txt",
        "combined_program_files.txt",
        "create_combined_file.py",
        "src/Perevod/translation.log",
        ".tmp_live_workflow_abcd/output/translation_report.json",
        ".tmp_doctor_input/chapter.txt",
        "src/Perevod/graph_runner.py",
        "README.md",
        "assets/icon.ico",
    ]

    assert find_tracked_artifacts(tracked_files) == [
        ".env",
        ".tmp_doctor_input/chapter.txt",
        ".tmp_live_workflow_abcd/output/translation_report.json",
        "Perevod.egg-info/PKG-INFO",
        "_project_files/Fermer/project.db",
        "combined_program_files.txt",
        "create_combined_file.py",
        "program_files_for_neronka/src_Perevod_graph_runner.py.txt",
        "src/Perevod/__pycache__/main.cpython-312.pyc",
        "src/Perevod/translation.log",
        "src/_project_files/projects_main.db",
    ]


def test_repo_cleanliness_allows_source_docs_tests_and_assets():
    allowed_paths = [
        ".env.example",
        "README.md",
        "docs/building-better-novel-translator.md",
        "scripts/check_repo_cleanliness.py",
        "src/Perevod/graph_runner.py",
        "tests/test_graph_runner.py",
        "assets/icon.ico",
    ]

    assert [path for path in allowed_paths if is_tracked_artifact(path)] == []
