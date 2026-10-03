"""
trainiq.safety — runtime execution-location safety check

Prevents TrainIQ from running against a copy of itself sitting in macOS
Trash — an observed real failure mode, not a hypothetical one: a stale
copy of the project sitting in Trash got executed instead of the intended
one, producing confusing behavior differences that had nothing to do with
TrainIQ's actual logic, and took real debugging time to trace back to
"which copy of the code is actually running" rather than any bug in the
code itself.

Deliberately does NOT use os.getcwd() or $PWD — both reflect shell state,
which can disagree with what Python actually loaded (exactly what
happened in the incident this module exists to prevent). The only
reliable signal is the resolved, REAL filesystem path of the executing
package itself — `Path(__file__).resolve()` follows symlinks to their
real target, so a symlink that merely points into Trash is caught too,
not just a literal `/.Trash/` path typed directly.

Checks both `.Trash` (per-user trash, `~/.Trash`) and `.Trashes`
(volume-level trash on external/network volumes, `/Volumes/.../.Trashes/`)
as exact path segments — broader than the literal `/.Trash/` substring
originally specified, since `.Trashes` is the same category of risk on a
different kind of volume, worth covering by the same mechanism.
"""

from __future__ import annotations

from pathlib import Path

_TRASH_SEGMENT_NAMES = {".Trash", ".Trashes"}


class RunningFromTrashError(Exception):
    """Raised when TrainIQ detects it is executing from macOS Trash."""


def get_executing_package_path() -> Path:
    """The resolved, real absolute path of the directory containing this
    package — derived from this file's own location, never from cwd."""
    return Path(__file__).resolve().parent


def assert_not_running_from_trash(package_path: "Path | None" = None) -> Path:
    """Resolves the given (or the real executing) package path and raises
    RunningFromTrashError if any path segment is a macOS Trash directory.
    Returns the resolved path on success, so callers can log it
    regardless of outcome — the resolved path is useful diagnostic
    information either way, not just on failure."""
    path = (package_path if package_path is not None else get_executing_package_path()).resolve()

    if _TRASH_SEGMENT_NAMES & set(path.parts):
        raise RunningFromTrashError(
            "TrainIQ is being executed from macOS Trash.\n\n"
            f"Project:\n{path}\n\n"
            "Move or restore the project before running."
        )
    return path


def is_running_from_trash(package_path: "Path | None" = None) -> bool:
    """True if the given (or the real executing) package path resolves into
    macOS Trash, False otherwise. Delegates to assert_not_running_from_trash
    for the actual detection logic."""
    try:
        assert_not_running_from_trash(package_path)
    except RunningFromTrashError:
        return True
    return False
