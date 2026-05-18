from Perevod.update_files import update_program_files


def test_update_program_files_is_explicit_and_portable(tmp_path):
    project_root = tmp_path / "project"
    target_dir = tmp_path / "combined"
    package_dir = project_root / "src" / "Perevod"
    package_dir.mkdir(parents=True)
    source_file = package_dir / "module.py"
    source_file.write_text("print('hello')\n", encoding="utf-8")
    (project_root / "changelog.txt").write_text("changes\n", encoding="utf-8")

    updated = update_program_files(project_root=project_root, target_dir=target_dir)

    assert sorted(updated) == [
        "changelog.txt",
        str(source_file.relative_to(project_root)),
    ]
    assert (target_dir / "src_Perevod_module.py.txt").read_text(
        encoding="utf-8"
    ) == "print('hello')\n"
    assert update_program_files(project_root=project_root, target_dir=target_dir) == []
