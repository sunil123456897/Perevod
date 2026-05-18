import os
import subprocess
import sys
from pathlib import Path


DEFAULT_TESTS = [
    "tests/test_config.py",
    "tests/test_project_metadata.py",
    "tests/test_model_registry.py",
    "tests/test_agents.py",
    "tests/test_01_managers.py",
    "tests/test_project_and_db_managers.py",
    "tests/test_cli.py",
    "tests/test_doctor.py",
    "tests/test_api_errors.py",
    "tests/test_import_boundaries.py",
    "tests/test_graph_runner.py",
    "tests/test_03_graph_nodes.py",
    "tests/test_04_graph_integration.py",
    "tests/test_05_sprint1_schemas.py",
    "tests/test_06_judge_node.py",
    "tests/test_07_refine_node.py",
    "tests/test_08_graph_integration_v3.py",
    "tests/test_09_sprint2_schemas.py",
    "tests/test_10_summarization_node.py",
    "tests/test_11_retrieval_node.py",
    "tests/test_12_graph_integration_v3_2.py",
    "tests/test_knowledge_base_manager.py",
    "tests/test_api_usage.py",
    "tests/test_llm_utils.py",
    "tests/test_file_io.py",
    "tests/test_text_planning.py",
    "tests/test_translation_quality.py",
    "tests/test_logging_setup.py",
    "tests/test_gui_utils.py",
    "tests/test_llm_provider.py",
    "tests/test_translator_agent.py",
    "tests/test_update_files.py",
    "tests/test_database_schema.py",
    "tests/test_caching.py",
    "tests/test_rolling_memory_sequential.py",
    "tests/test_deep_audit_script.py",
    "tests/test_db_utility_scripts.py",
    "tests/test_repo_cleanliness_script.py",
    "tests/test_run_entrypoint.py",
    "tests/test_main_entrypoint.py",
    "tests/test_run_coverage_script.py",
]


def main() -> int:
    repo_root = Path(__file__).resolve().parents[1]
    tests = sys.argv[1:] or DEFAULT_TESTS

    env = os.environ.copy()
    env["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] = "1"
    env["PYTHONPATH"] = os.pathsep.join(
        path
        for path in [
            str(repo_root / "src"),
            env.get("PYTHONPATH", ""),
        ]
        if path
    )

    command = [
        sys.executable,
        "-m",
        "pytest",
        *tests,
        "-q",
        "-o",
        "addopts=-p no:cacheprovider",
        "-p",
        "no:cacheprovider",
    ]

    try:
        completed = subprocess.run(command, cwd=repo_root, env=env, timeout=90)
        return completed.returncode
    except subprocess.TimeoutExpired:
        print("Safe test run timed out after 90 seconds.", file=sys.stderr)
        return 124


if __name__ == "__main__":
    raise SystemExit(main())
