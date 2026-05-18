import os
import re


_WINDOWS_RESERVED_NAMES = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{index}" for index in range(1, 10)),
    *(f"LPT{index}" for index in range(1, 10)),
}
_WINDOWS_ILLEGAL_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def validate_project_name(project_name: str) -> str:
    normalized_name = project_name.strip()
    base_name = normalized_name.split(".", 1)[0].upper()
    if (
        not normalized_name
        or normalized_name in {".", ".."}
        or os.path.isabs(normalized_name)
        or os.path.basename(normalized_name) != normalized_name
        or _WINDOWS_ILLEGAL_CHARS.search(normalized_name)
        or normalized_name.endswith(".")
        or base_name in _WINDOWS_RESERVED_NAMES
    ):
        raise ValueError(f"Unsafe project name: {project_name!r}")
    return normalized_name
