"""
Tests for trainiq.config — non-secret application configuration (RC1-HF-002).
No schema migration involved; a plain JSON file, deliberately.
"""

from __future__ import annotations

from pathlib import Path

from trainiq.config import (
    WeightGoal,
    get_athlete_timezone,
    get_eufy_device_id,
    get_weight_goal,
    load_config,
    save_config,
    set_athlete_timezone,
    set_eufy_device_id,
    set_weight_goal,
)


def test_load_config_returns_empty_dict_when_file_does_not_exist(tmp_path: Path):
    assert load_config(tmp_path / "does_not_exist.json") == {}


def test_save_then_load_round_trips(tmp_path: Path):
    path = tmp_path / "config.json"
    save_config(path, {"eufy": {"device_id": "abc123"}})

    assert load_config(path) == {"eufy": {"device_id": "abc123"}}


def test_save_config_creates_parent_directories(tmp_path: Path):
    path = tmp_path / "nested" / "dir" / "config.json"
    save_config(path, {"key": "value"})
    assert path.exists()


def test_corrupted_config_file_is_treated_as_empty_not_a_crash(tmp_path: Path):
    """A corrupted config file must not crash the app — same "never let a
    data problem take down the whole run" principle applied at a much
    smaller scale than everywhere else in this project, but the same
    principle."""
    path = tmp_path / "config.json"
    path.write_text("{ this is not valid json !!!")

    assert load_config(path) == {}


def test_get_eufy_device_id_returns_none_when_not_set(tmp_path: Path):
    assert get_eufy_device_id(tmp_path / "config.json") is None


def test_set_and_get_eufy_device_id_round_trips(tmp_path: Path):
    path = tmp_path / "config.json"
    set_eufy_device_id(path, "device-xyz")

    assert get_eufy_device_id(path) == "device-xyz"


def test_set_eufy_device_id_preserves_other_existing_config_keys(tmp_path: Path):
    """Setting Eufy's device_id must not clobber unrelated config that
    might exist alongside it in the future."""
    path = tmp_path / "config.json"
    save_config(path, {"some_other_setting": "unrelated_value"})

    set_eufy_device_id(path, "device-xyz")

    config = load_config(path)
    assert config["some_other_setting"] == "unrelated_value"
    assert config["eufy"]["device_id"] == "device-xyz"


def test_set_eufy_device_id_overwrites_previous_value(tmp_path: Path):
    path = tmp_path / "config.json"
    set_eufy_device_id(path, "old-device")
    set_eufy_device_id(path, "new-device")
    assert get_eufy_device_id(path) == "new-device"


def test_get_athlete_timezone_returns_none_when_not_set(tmp_path: Path):
    assert get_athlete_timezone(tmp_path / "config.json") is None


def test_set_and_get_athlete_timezone_round_trips(tmp_path: Path):
    path = tmp_path / "config.json"
    set_athlete_timezone(path, "Europe/London")

    assert get_athlete_timezone(path) == "Europe/London"


def test_set_athlete_timezone_preserves_other_existing_config_keys(tmp_path: Path):
    path = tmp_path / "config.json"
    save_config(path, {"eufy": {"device_id": "device-xyz"}})

    set_athlete_timezone(path, "Europe/London")

    config = load_config(path)
    assert config["eufy"]["device_id"] == "device-xyz"
    assert config["athlete"]["timezone"] == "Europe/London"


def test_set_athlete_timezone_overwrites_previous_value(tmp_path: Path):
    path = tmp_path / "config.json"
    set_athlete_timezone(path, "Europe/London")
    set_athlete_timezone(path, "America/New_York")
    assert get_athlete_timezone(path) == "America/New_York"


def test_get_weight_goal_returns_none_when_not_set(tmp_path: Path):
    assert get_weight_goal(tmp_path / "config.json") is None


def test_get_weight_goal_returns_none_when_only_one_value_set(tmp_path: Path):
    """Never a partially-guessed goal — both values or neither."""
    path = tmp_path / "config.json"
    save_config(path, {"athlete": {"start_weight_kg": 82.1}})
    assert get_weight_goal(path) is None


def test_set_and_get_weight_goal_round_trips(tmp_path: Path):
    path = tmp_path / "config.json"
    set_weight_goal(path, start_weight_kg=82.1, goal_weight_kg=72.0)

    goal = get_weight_goal(path)
    assert goal == WeightGoal(start_weight_kg=82.1, goal_weight_kg=72.0)


def test_set_weight_goal_preserves_other_existing_config_keys(tmp_path: Path):
    path = tmp_path / "config.json"
    save_config(path, {"athlete": {"timezone": "Europe/London"}, "eufy": {"device_id": "device-xyz"}})

    set_weight_goal(path, start_weight_kg=82.1, goal_weight_kg=72.0)

    config = load_config(path)
    assert config["athlete"]["timezone"] == "Europe/London"
    assert config["eufy"]["device_id"] == "device-xyz"
    assert config["athlete"]["goal_weight_kg"] == 72.0


def test_config_never_contains_a_secret_looking_key(tmp_path: Path):
    """Sanity check on the module's own stated boundary: nothing this
    module writes should ever include a password/token/secret-shaped key —
    that boundary belongs to CredentialStore/Keychain exclusively."""
    path = tmp_path / "config.json"
    set_eufy_device_id(path, "device-xyz")

    raw_text = path.read_text().lower()
    for forbidden in ("password", "token", "secret"):
        assert forbidden not in raw_text
