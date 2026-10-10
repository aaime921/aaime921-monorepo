"""
trainiq.config — RC1-HF-002: non-secret application configuration

Deliberately NOT a database table. Per the explicit RC1 cost/benefit
decision: a schema migration (new version, migration tests, rollback
cases, documentation) is disproportionate to persisting one string
(Eufy's device_id). A plain JSON file under Application Support is the
simplest thing that is honestly correct for RC1 — revisit only if real
usage produces genuinely richer configuration needs, not preemptively.

This is explicitly NOT for secrets. Credentials remain exclusively in
Keychain via CredentialStore — nothing here ever stores a password or
token. If that boundary ever gets blurred, this file has failed at its
one job.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional


def load_config(config_path: Path) -> dict[str, Any]:
    """Returns an empty dict if no config file exists yet — a fresh
    install has no configuration, and that's a normal, expected state,
    not an error."""
    if not config_path.exists():
        return {}
    try:
        return json.loads(config_path.read_text())
    except (json.JSONDecodeError, OSError):
        # A corrupted config file should not crash the app — treat it the
        # same as "no config yet" and let the wizard (or the user) recreate
        # it, consistent with this project's "never let a data problem take
        # down the whole run" principle applied at a much smaller scale here.
        return {}


def save_config(config_path: Path, config: dict[str, Any]) -> None:
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(json.dumps(config, indent=2))


def get_eufy_device_id(config_path: Path) -> Optional[str]:
    return load_config(config_path).get("eufy", {}).get("device_id")


def set_eufy_device_id(config_path: Path, device_id: str) -> None:
    config = load_config(config_path)
    config.setdefault("eufy", {})["device_id"] = device_id
    save_config(config_path, config)


def get_athlete_timezone(config_path: Path) -> Optional[str]:
    return load_config(config_path).get("athlete", {}).get("timezone")


def set_athlete_timezone(config_path: Path, timezone_name: str) -> None:
    config = load_config(config_path)
    config.setdefault("athlete", {})["timezone"] = timezone_name
    save_config(config_path, config)


@dataclass(frozen=True)
class WeightGoal:
    """Issue #71: the athlete's weight-loss goal. Not in the database (no
    `athlete_profile` column for it) — config.json is the same "non-secret,
    not worth a migration" fit this module already exists for."""
    start_weight_kg: float
    goal_weight_kg: float


def get_weight_goal(config_path: Path) -> Optional[WeightGoal]:
    """None when either value is missing — never a partially-guessed goal.
    The export's profile.md/weight.md render "goal: not configured" in
    that case rather than inventing a number."""
    athlete = load_config(config_path).get("athlete", {})
    start_weight_kg = athlete.get("start_weight_kg")
    goal_weight_kg = athlete.get("goal_weight_kg")
    if start_weight_kg is None or goal_weight_kg is None:
        return None
    return WeightGoal(start_weight_kg=start_weight_kg, goal_weight_kg=goal_weight_kg)


def set_weight_goal(config_path: Path, start_weight_kg: float, goal_weight_kg: float) -> None:
    config = load_config(config_path)
    athlete = config.setdefault("athlete", {})
    athlete["start_weight_kg"] = start_weight_kg
    athlete["goal_weight_kg"] = goal_weight_kg
    save_config(config_path, config)
