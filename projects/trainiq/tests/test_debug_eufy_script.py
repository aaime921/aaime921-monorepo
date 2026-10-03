"""
Tests for scripts/debug_eufy.py. This is a diagnostic dev tool, not part of
the shipped `trainiq` package, so coverage here is intentionally lighter
than the main test suite — it verifies the one property that actually
matters (this script cannot accidentally touch sync/persistence) and the
one failure path that doesn't require real network access. The live
success path (a real Eufy account's actual response) can only be verified
by running the script itself, per its stated purpose.
"""

from __future__ import annotations

import ast
import runpy
import sys
from pathlib import Path

import pytest

SCRIPT_PATH = Path(__file__).resolve().parent.parent / "scripts" / "debug_eufy.py"


def test_debug_script_never_imports_the_sync_engine():
    """Structural guarantee, not just a description: this script cannot
    accidentally trigger a real sync, because it never imports the one
    thing that could (SynchronizationEngine / run_once)."""
    with open(SCRIPT_PATH) as f:
        tree = ast.parse(f.read())

    imported_names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            imported_names.add(node.module)
            imported_names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.Import):
            imported_names.update(alias.name for alias in node.names)

    assert "SynchronizationEngine" not in imported_names
    assert not any("sync.engine" in name for name in imported_names)


def test_debug_script_never_references_normalized_tables_in_executable_code():
    """A second, complementary check to the import-level one above — but
    precise this time: an earlier version of this test did a blind
    substring search and immediately flagged the script's OWN docstring,
    which explains what it does NOT touch using those exact words. First
    fix attempt (string-equality against ast.get_docstring()'s cleaned
    output) also failed, since that function strips whitespace the raw
    AST node retains — fixed properly by identifying the docstring via
    its AST position (the first statement, if it's a bare string
    expression) rather than comparing string content at all."""
    with open(SCRIPT_PATH) as f:
        tree = ast.parse(f.read())

    docstring_node = None
    if (
        tree.body
        and isinstance(tree.body[0], ast.Expr)
        and isinstance(tree.body[0].value, ast.Constant)
        and isinstance(tree.body[0].value.value, str)
    ):
        docstring_node = tree.body[0].value

    forbidden = ("normalized_activities", "weigh_ins", "raw_activities", "sync_checkpoints")

    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            if node is docstring_node:
                continue
            for term in forbidden:
                assert term not in node.value, (
                    f"Found {term!r} in a non-docstring string literal: {node.value!r}"
                )


def test_debug_script_exits_cleanly_with_no_device_id_configured(tmp_path, monkeypatch, capsys):
    """The one failure path testable without real network access: no
    device_id in config.json (Eufy never connected) must exit with a
    clear message, not a traceback."""
    import trainiq.config as config_module
    monkeypatch.setattr(config_module, "load_config", lambda path: {})

    db_path = tmp_path / "trainiq.db"
    config_path = tmp_path / "config.json"  # deliberately does not exist

    monkeypatch.setattr(sys, "argv", [
        "debug_eufy.py",
        "--db-path", str(db_path),
        "--config-path", str(config_path),
        "--out", str(tmp_path / "out.json"),
    ])

    with pytest.raises(SystemExit) as excinfo:
        runpy.run_path(str(SCRIPT_PATH), run_name="__main__")

    assert excinfo.value.code == 1
    captured = capsys.readouterr()
    assert "run the setup wizard first" in captured.out
