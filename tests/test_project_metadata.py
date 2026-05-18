import sys
from pathlib import Path

if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover - Python 3.10 compatibility
    import tomli as tomllib


def test_pyproject_metadata_is_not_placeholder():
    with open("pyproject.toml", "rb") as pyproject:
        data = tomllib.load(pyproject)

    project = data["project"]

    assert project["name"] == "perevod"
    assert project["description"] != "A small example package"
    assert project["authors"] == [{"name": "sunil123456897"}]
    assert "classifiers" in project
    assert "classifiers" not in project.get("optional-dependencies", {})


def test_project_dependencies_use_supported_gemini_sdk_only():
    with open("pyproject.toml", "rb") as pyproject:
        data = tomllib.load(pyproject)

    dependencies = data["project"]["dependencies"]

    assert any(dep.startswith("google-genai") for dep in dependencies)
    assert not any(dep.startswith("google-generativeai") for dep in dependencies)


def test_pyproject_is_the_only_packaging_source_of_truth():
    assert not Path("src/Perevod/setup.py").exists()
