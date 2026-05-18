import hashlib
from pathlib import Path


DEFAULT_EXTRA_FILES = ("changelog.txt", "translation.log", "requirements.txt")


def get_file_hash(path: Path) -> str | None:
    if not path.exists():
        return None
    hasher = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def update_program_files(
    *,
    project_root: Path | None = None,
    target_dir: Path | None = None,
    extra_files: tuple[str, ...] = DEFAULT_EXTRA_FILES,
) -> list[str]:
    project_root = project_root or Path(__file__).resolve().parents[2]
    target_dir = target_dir or project_root / "program_files_for_neronka"
    target_dir.mkdir(parents=True, exist_ok=True)

    updated_files: list[str] = []
    for path in project_root.rglob("*"):
        if not path.is_file():
            continue
        if "__pycache__" in path.parts:
            continue

        relative_path = path.relative_to(project_root)
        if path.suffix != ".py" and str(relative_path) not in extra_files:
            continue

        output_path = target_dir / f"{'_'.join(relative_path.parts)}.txt"
        if get_file_hash(path) == get_file_hash(output_path):
            continue

        output_path.write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
        updated_files.append(str(relative_path))

    return updated_files


def main() -> None:
    updated_files = update_program_files()
    if updated_files:
        print("Updated the following files:")
        for file_path in updated_files:
            print(f"- {file_path}")
    else:
        print("No files needed updating.")


if __name__ == "__main__":
    main()
