# src/Perevod/workflow_lock.py
"""Cross-process lock that prevents two translation runs from writing to the
same output directory at once.

The lock is a marker file (``.translation.lock``) created with ``O_CREAT |
O_EXCL`` for atomicity. It records the owning PID and start time so that a
stale lock left by a crashed process can be detected and reclaimed. On Windows
the liveness check uses ``OpenProcess``; on POSIX it uses ``os.kill(pid, 0)``.
"""

import contextlib
import ctypes
import logging
import os
from datetime import datetime, timezone

logger = logging.getLogger("NovelTranslator.GraphRunner")

MALFORMED_LOCK_STALE_AFTER_SECONDS = 15 * 60


def _create_lock_file(lock_path: str) -> int:
    return os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)


def _read_lock_pid(lock_path: str) -> int | None:
    try:
        with open(lock_path, encoding="utf-8") as lock_file:
            for line in lock_file:
                if line.startswith("pid="):
                    return int(line.split("=", 1)[1].strip())
    except (OSError, ValueError):
        return None
    return None


def _can_remove_existing_lock(
    lock_path: str,
    lock_pid: int | None,
    *,
    is_running=None,
) -> bool:
    check_running = is_running or _is_process_running
    if lock_pid:
        return not check_running(lock_pid)
    try:
        lock_age_seconds = datetime.now(timezone.utc).timestamp() - os.path.getmtime(
            lock_path
        )
    except OSError:
        return False
    return lock_age_seconds >= MALFORMED_LOCK_STALE_AFTER_SECONDS


def _is_process_running(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name == "nt":
        process_query_limited_information = 0x1000
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.OpenProcess(
            process_query_limited_information,
            False,
            pid,
        )
        if handle:
            kernel32.CloseHandle(handle)
            return True
        return kernel32.GetLastError() == 5

    try:
        os.kill(pid, 0)
    except OSError:
        return False
    except ValueError:
        return False
    return True


def acquire_workflow_lock(
    output_dir: str,
    *,
    is_running=None,
) -> str:
    """Create an exclusive lock for ``output_dir``.

    Raises ``RuntimeError`` if a live lock already exists. Stale locks owned by
    dead processes (or older than ``MALFORMED_LOCK_STALE_AFTER_SECONDS`` when the
    PID cannot be read) are reclaimed automatically.

    ``is_running`` is an optional PID-liveness callable injected so callers that
    re-bind the helper (e.g. for test patching) can route the check through their
    own namespace.
    """
    check_running = is_running or _is_process_running
    lock_path = os.path.join(output_dir, ".translation.lock")
    try:
        fd = _create_lock_file(lock_path)
    except FileExistsError as exc:
        lock_pid = _read_lock_pid(lock_path)
        if _can_remove_existing_lock(lock_path, lock_pid, is_running=check_running):
            logger.warning(
                "Удаление устаревшего lock-файла '%s' от PID %s.",
                lock_path,
                lock_pid or "unknown",
            )
            os.remove(lock_path)
            fd = _create_lock_file(lock_path)
        else:
            raise RuntimeError(
                f"Translation is already running for output directory: {output_dir}"
            ) from exc
    with os.fdopen(fd, "w", encoding="utf-8") as lock_file:
        lock_file.write(f"pid={os.getpid()}\n")
        lock_file.write(f"started_at={datetime.now(timezone.utc).isoformat()}\n")
    return lock_path


def release_workflow_lock(lock_path: str | None) -> None:
    """Release a lock previously acquired via :func:`acquire_workflow_lock`.

    Only the owning PID is allowed to remove the file; a lock owned by another
    process is left in place with a warning.
    """
    if not lock_path:
        return
    lock_pid = _read_lock_pid(lock_path)
    if lock_pid != os.getpid():
        if os.path.exists(lock_path):
            logger.warning(
                "Lock-файл '%s' не удален: владелец PID %s, текущий PID %s.",
                lock_path,
                lock_pid or "unknown",
                os.getpid(),
            )
        return
    with contextlib.suppress(FileNotFoundError):
        os.remove(lock_path)


# Backwards-compatible underscore aliases. Historically these helpers lived in
# ``graph_runner`` with leading underscores and tests / callers patch them as
# ``Perevod.graph_runner._acquire_workflow_lock``. We keep the canonical public
# names above and re-export the private ones here so the public API and the
# test contract both keep working.
_acquire_workflow_lock = acquire_workflow_lock
_release_workflow_lock = release_workflow_lock
