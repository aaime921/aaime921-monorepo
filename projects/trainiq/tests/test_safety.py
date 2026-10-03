"""
Tests for trainiq.safety — the runtime execution-location check. Added
after an observed real incident where a stale copy of the project sitting
in macOS Trash was executed instead of the intended copy, producing
confusing behavior differences unrelated to any actual bug in the code.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from trainiq.safety import (
    RunningFromTrashError,
    assert_not_running_from_trash,
    get_executing_package_path,
    is_running_from_trash,
)


def test_normal_project_path_passes(tmp_path: Path):
    normal_path = tmp_path / "Users" / "andre" / "Downloads" / "trainiq" / "trainiq"
    normal_path.mkdir(parents=True)

    result = assert_not_running_from_trash(normal_path)

    assert result == normal_path.resolve()


def test_trash_path_is_rejected(tmp_path: Path):
    trash_path = tmp_path / "Users" / "andre" / ".Trash" / "trainiq" / "trainiq"
    trash_path.mkdir(parents=True)

    with pytest.raises(RunningFromTrashError) as excinfo:
        assert_not_running_from_trash(trash_path)

    assert "macOS Trash" in str(excinfo.value)
    assert str(trash_path.resolve()) in str(excinfo.value)


def test_volume_level_trashes_path_is_also_rejected(tmp_path: Path):
    """.Trashes (note the plural) is the volume-level trash used on
    external/network volumes — a different, real macOS location than the
    per-user ~/.Trash, worth covering by the same mechanism even though
    it wasn't the literal string originally specified."""
    trashes_path = tmp_path / "Volumes" / "External" / ".Trashes" / "501" / "trainiq"
    trashes_path.mkdir(parents=True)

    with pytest.raises(RunningFromTrashError):
        assert_not_running_from_trash(trashes_path)


def test_symlink_resolving_into_trash_is_detected(tmp_path: Path):
    """The core requirement: os.getcwd()/$PWD-style checks would miss
    this entirely, since a symlink's own location doesn't look like a
    Trash path at all — only resolving it to its real target reveals the
    danger. Path.resolve() follows symlinks, which is exactly why this
    module uses it rather than checking the unresolved path."""
    real_trash_target = tmp_path / "Users" / "andre" / ".Trash" / "trainiq" / "trainiq"
    real_trash_target.mkdir(parents=True)

    innocent_looking_symlink = tmp_path / "totally_normal_folder"
    innocent_looking_symlink.symlink_to(real_trash_target)

    # The symlink's own path contains no ".Trash" segment at all —
    # confirming this test actually exercises symlink resolution, not
    # just a second copy of the direct-path test above.
    assert ".Trash" not in innocent_looking_symlink.parts

    with pytest.raises(RunningFromTrashError):
        assert_not_running_from_trash(innocent_looking_symlink)


def test_symlink_resolving_to_a_normal_path_passes(tmp_path: Path):
    """Complementary case: a symlink is not inherently suspicious — only
    one that resolves INTO Trash should be rejected."""
    real_target = tmp_path / "actual" / "project" / "location"
    real_target.mkdir(parents=True)
    symlink = tmp_path / "shortcut"
    symlink.symlink_to(real_target)

    result = assert_not_running_from_trash(symlink)

    assert result == real_target.resolve()


def test_differently_cased_trash_like_directory_is_not_detected(tmp_path: Path):
    """Documents existing behavior: the trash-segment check is an exact,
    case-sensitive match against ".Trash"/".Trashes", so a differently
    cased segment like ".trash" is not recognized as Trash."""
    lowercase_trash_path = tmp_path / "Users" / "andre" / ".trash" / "trainiq" / "trainiq"
    lowercase_trash_path.mkdir(parents=True)

    result = assert_not_running_from_trash(lowercase_trash_path)

    assert result == lowercase_trash_path.resolve()


def test_is_running_from_trash_returns_true_for_trash_path(tmp_path: Path):
    trash_path = tmp_path / "Users" / "andre" / ".Trash" / "trainiq" / "trainiq"
    trash_path.mkdir(parents=True)

    assert is_running_from_trash(trash_path) is True


def test_is_running_from_trash_returns_false_for_normal_path(tmp_path: Path):
    normal_path = tmp_path / "Users" / "andre" / "Downloads" / "trainiq" / "trainiq"
    normal_path.mkdir(parents=True)

    assert is_running_from_trash(normal_path) is False


def test_get_executing_package_path_returns_this_packages_real_directory():
    """Confirms the real (no-argument) code path — not just the
    injectable-path test seam used above — resolves to somewhere sane:
    the actual trainiq/ package directory, derived from __file__, never
    from cwd."""
    path = get_executing_package_path()
    assert path.name == "trainiq"
    assert path.is_absolute()
    assert (path / "safety.py").exists()


def test_error_message_includes_clear_guidance():
    """The message shown to a real user must be self-explanatory without
    needing this codebase's context to understand — checked directly
    rather than just asserting an exception type."""
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        trash_path = Path(d) / ".Trash" / "trainiq"
        trash_path.mkdir(parents=True)
        with pytest.raises(RunningFromTrashError) as excinfo:
            assert_not_running_from_trash(trash_path)
        message = str(excinfo.value)
        assert "FATAL" not in message  # the "FATAL:" prefix is added by the caller (app.py), not this module
        assert "Move or restore" in message
