import importlib


def test_run_coverage_measures_tests_and_reports(monkeypatch):
    run_coverage = importlib.import_module("scripts.run_coverage")
    calls = []

    class FakeCoverage:
        def __init__(self, *, config_file):
            calls.append(("init", config_file.endswith("pyproject.toml")))

        def erase(self):
            calls.append(("erase",))

        def start(self):
            calls.append(("start",))

        def stop(self):
            calls.append(("stop",))

        def save(self):
            calls.append(("save",))

        def report(self):
            calls.append(("report",))
            return 88.0

    def fake_pytest_main(args):
        calls.append(("pytest", "tests/test_config.py" in args))
        return 0

    monkeypatch.setattr(run_coverage.coverage, "Coverage", FakeCoverage)
    monkeypatch.setattr(run_coverage.pytest, "main", fake_pytest_main)

    assert run_coverage.main() == 0
    assert calls == [
        ("init", True),
        ("erase",),
        ("start",),
        ("pytest", True),
        ("stop",),
        ("save",),
        ("report",),
    ]
