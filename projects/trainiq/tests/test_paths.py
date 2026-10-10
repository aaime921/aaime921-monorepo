"""
Tests for trainiq.paths (issue #70): DB/config/log path resolution with no
`~/Library` assumption on Linux (AC5), flag > env > default precedence,
and the Mac default staying byte-for-byte what it was before #70 (AC10).
"""

from __future__ import annotations

import argparse
from pathlib import Path

from trainiq.logging_setup import DEFAULT_LOG_DIR
from trainiq.paths import Paths, platform_defaults, resolve_paths


def _args(db_path=None, config_path=None, log_dir=None) -> argparse.Namespace:
    return argparse.Namespace(db_path=db_path, config_path=config_path, log_dir=log_dir)


# --- platform defaults -------------------------------------------------------

def test_mac_default_is_unchanged_library_paths():
    paths = platform_defaults(platform="darwin", env={})
    app_support = Path.home() / "Library" / "Application Support" / "TrainIQ"
    assert paths.db_path == app_support / "trainiq.db"
    assert paths.config_path == app_support / "config.json"
    assert paths.log_dir == DEFAULT_LOG_DIR


def test_linux_default_uses_xdg_not_library():
    paths = platform_defaults(platform="linux", env={})
    assert "Library" not in str(paths.db_path)
    assert paths.db_path == Path.home() / ".local" / "share" / "trainiq" / "trainiq.db"
    assert paths.config_path == Path.home() / ".config" / "trainiq" / "config.json"
    assert paths.log_dir == Path.home() / ".local" / "state" / "trainiq" / "logs"


def test_linux_default_honors_xdg_env_vars():
    env = {
        "XDG_DATA_HOME": "/custom/data",
        "XDG_CONFIG_HOME": "/custom/config",
        "XDG_STATE_HOME": "/custom/state",
    }
    paths = platform_defaults(platform="linux", env=env)
    assert paths.db_path == Path("/custom/data/trainiq/trainiq.db")
    assert paths.config_path == Path("/custom/config/trainiq/config.json")
    assert paths.log_dir == Path("/custom/state/trainiq/logs")


# --- resolve_paths precedence: flag > env > default -------------------------

def test_resolve_paths_uses_default_when_nothing_else_given():
    defaults = Paths(db_path=Path("/default/db"), config_path=Path("/default/config"), log_dir=Path("/default/logs"))
    result = resolve_paths(_args(), env={}, defaults=defaults)
    assert result == defaults


def test_resolve_paths_env_var_overrides_default():
    defaults = Paths(db_path=Path("/default/db"), config_path=Path("/default/config"), log_dir=Path("/default/logs"))
    env = {"TRAINIQ_DB_PATH": "/env/db.sqlite"}
    result = resolve_paths(_args(), env=env, defaults=defaults)
    assert result.db_path == Path("/env/db.sqlite")
    assert result.config_path == defaults.config_path  # unaffected


def test_resolve_paths_flag_overrides_env_and_default():
    defaults = Paths(db_path=Path("/default/db"), config_path=Path("/default/config"), log_dir=Path("/default/logs"))
    env = {"TRAINIQ_DB_PATH": "/env/db.sqlite"}
    args = _args(db_path="/flag/db.sqlite")
    result = resolve_paths(args, env=env, defaults=defaults)
    assert result.db_path == Path("/flag/db.sqlite")


def test_resolve_paths_each_of_three_paths_independent():
    defaults = Paths(db_path=Path("/default/db"), config_path=Path("/default/config"), log_dir=Path("/default/logs"))
    args = _args(log_dir="/flag/logs")
    env = {"TRAINIQ_CONFIG_PATH": "/env/config.json"}
    result = resolve_paths(args, env=env, defaults=defaults)
    assert result.db_path == defaults.db_path
    assert result.config_path == Path("/env/config.json")
    assert result.log_dir == Path("/flag/logs")


def test_resolve_paths_falls_back_to_platform_defaults_when_none_given():
    result = resolve_paths(_args(), env={}, platform="linux")
    assert result == platform_defaults(platform="linux", env={})
