"""
trainiq.paths — Issue #70

DB/config/log locations, resolvable by CLI flag or environment variable
instead of hardcoded `~/Library/...` — needed to run headless on a
GitHub Actions Linux runner with no Mac and no `~/Library`.

Precedence, for each of the three paths independently: flag > env var >
default. Mac behaviour must stay unchanged (AC10): `platform_defaults()`
returns the exact pre-#70 `~/Library/...` paths when `sys.platform ==
"darwin"` (log dir taken straight from `trainiq.logging_setup.DEFAULT_LOG_DIR`
— one source of truth, not two copies of the same path). Anywhere else, it
returns XDG base-directory defaults — no `~/Library` assumption on Linux.

`trainiq/app.py` passes its own `APP_SUPPORT_DIR`/`CONFIG_PATH`/`LOG_DIR`
module constants in as `defaults` (those are themselves computed from
`platform_defaults()` once at import time) so existing tests that
monkeypatch those constants keep working unchanged — `resolve_paths()`
only falls back to `platform_defaults()` itself when no `defaults` is
supplied, which is what a direct test of this module does.
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Optional

from trainiq.logging_setup import DEFAULT_LOG_DIR

ENV_DB_PATH = "TRAINIQ_DB_PATH"
ENV_CONFIG_PATH = "TRAINIQ_CONFIG_PATH"
ENV_LOG_DIR = "TRAINIQ_LOG_DIR"


@dataclass(frozen=True)
class Paths:
    db_path: Path
    config_path: Path
    log_dir: Path


def _mac_defaults() -> Paths:
    app_support = Path.home() / "Library" / "Application Support" / "TrainIQ"
    return Paths(
        db_path=app_support / "trainiq.db",
        config_path=app_support / "config.json",
        log_dir=DEFAULT_LOG_DIR,
    )


def _xdg_defaults(env: Mapping[str, str]) -> Paths:
    data_home = Path(env.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share"))
    config_home = Path(env.get("XDG_CONFIG_HOME") or str(Path.home() / ".config"))
    state_home = Path(env.get("XDG_STATE_HOME") or str(Path.home() / ".local" / "state"))
    return Paths(
        db_path=data_home / "trainiq" / "trainiq.db",
        config_path=config_home / "trainiq" / "config.json",
        log_dir=state_home / "trainiq" / "logs",
    )


def platform_defaults(platform: Optional[str] = None, env: Optional[Mapping[str, str]] = None) -> Paths:
    """The default `Paths` for the given platform (`sys.platform` if not
    given) — macOS keeps today's `~/Library/...` values exactly; anything
    else gets the XDG defaults."""
    plat = platform if platform is not None else sys.platform
    environ = env if env is not None else os.environ
    return _mac_defaults() if plat == "darwin" else _xdg_defaults(environ)


def resolve_paths(
    args,
    env: Optional[Mapping[str, str]] = None,
    defaults: Optional[Paths] = None,
    platform: Optional[str] = None,
) -> Paths:
    """flag (`args.db_path`/`.config_path`/`.log_dir`) > env var > `defaults`
    (falling back to `platform_defaults()` if not given), independently
    for each of the three paths. `args` only needs to support `getattr`
    with a default, so a plain `argparse.Namespace` or any stand-in works."""
    environ = env if env is not None else os.environ
    base = defaults if defaults is not None else platform_defaults(platform=platform, env=environ)

    db_path = getattr(args, "db_path", None) or environ.get(ENV_DB_PATH) or str(base.db_path)
    config_path = getattr(args, "config_path", None) or environ.get(ENV_CONFIG_PATH) or str(base.config_path)
    log_dir = getattr(args, "log_dir", None) or environ.get(ENV_LOG_DIR) or str(base.log_dir)

    return Paths(db_path=Path(db_path), config_path=Path(config_path), log_dir=Path(log_dir))
