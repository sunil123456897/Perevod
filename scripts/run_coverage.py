import os
import sys
from pathlib import Path

import coverage
import pytest

try:
    from scripts.run_safe_tests import DEFAULT_TESTS
except ModuleNotFoundError:  # pragma: no cover - used when invoked as a script.
    from run_safe_tests import DEFAULT_TESTS


def main() -> int:
    repo_root = Path(__file__).resolve().parents[1]
    os.environ["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] = "1"
    sys.path.insert(0, str(repo_root / "src"))

    cov = coverage.Coverage(config_file=str(repo_root / "pyproject.toml"))
    cov.erase()
    cov.start()
    try:
        result = pytest.main(
            [
                *DEFAULT_TESTS,
                "-q",
                "--basetemp",
                str(repo_root / ".pytest_tmp_coverage"),
                "-o",
                "addopts=-p no:cacheprovider",
                "-p",
                "no:cacheprovider",
            ]
        )
    finally:
        cov.stop()
        cov.save()
    cov.report()
    return int(result)


if __name__ == "__main__":
    raise SystemExit(main())
