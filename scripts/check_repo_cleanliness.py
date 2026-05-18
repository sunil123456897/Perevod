import argparse
import fnmatch
import subprocess
import sys
from pathlib import Path


ARTIFACT_PATTERNS = (
    ".env",
    "*.egg-info/**",
    "**/*.egg-info/**",
    "__pycache__/**",
    "**/__pycache__/**",
    "*.py[cod]",
    "**/*.py[cod]",
    "_project_files/**",
    "src/_project_files/**",
    "src/Perevod/_project_files/**",
    ".tmp_*/**",
    "program_files_for_neronka/**",
    "combined_program_files.txt",
    "create_combined_file.py",
    "commit_message.txt",
    "*.log",
    "**/*.log",
    "build/**",
    "dist/**",
    "*.db",
    "**/*.db",
    "*.sqlite",
    "**/*.sqlite",
    "*.sqlite3",
    "**/*.sqlite3",
)


def _normalize_path(path: str) -> str:
    return path.replace("\\", "/").strip("/")


def is_tracked_artifact(path: str) -> bool:
    normalized_path = _normalize_path(path)
    return any(
        fnmatch.fnmatchcase(normalized_path, pattern)
        for pattern in ARTIFACT_PATTERNS
    )


def find_tracked_artifacts(paths: list[str]) -> list[str]:
    return sorted(
        {
            _normalize_path(path)
            for path in paths
            if is_tracked_artifact(path)
        }
    )


def load_git_tracked_files(repo_root: Path) -> list[str]:
    completed = subprocess.run(
        ["git", "ls-files", "-z"],
        cwd=repo_root,
        check=True,
        capture_output=True,
        text=False,
    )
    return [
        path.decode("utf-8")
        for path in completed.stdout.split(b"\0")
        if path
    ]


def check_repo_cleanliness(repo_root: Path) -> int:
    tracked_artifacts = find_tracked_artifacts(load_git_tracked_files(repo_root))
    if not tracked_artifacts:
        print("No tracked generated/local artifacts found.")
        return 0

    print(
        "Tracked generated/local artifacts found. "
        "Remove them from Git and keep them ignored:",
        file=sys.stderr,
    )
    for path in tracked_artifacts:
        print(f"- {path}", file=sys.stderr)
    return 1


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Fail if generated files, local databases, logs or secrets are tracked by Git."
    )
    parser.add_argument(
        "--repo-root",
        type=Path,
        default=Path(__file__).resolve().parents[1],
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    return check_repo_cleanliness(args.repo_root)


if __name__ == "__main__":
    raise SystemExit(main())
