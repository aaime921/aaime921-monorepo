"""
Tests for trainiq.config — non-secret application configuration (RC1-HF-002).
No schema migration involved; a plain JSON file, deliberately.
"""

from __future__ import annotations

from pathlib import Path

from trainiq.config import get_eufy_device_id, load_config, save_config, set_eufy_device_id


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


def test_config_never_contains_a_secret_looking_key(tmp_path: Path):
    """Sanity check on the module's own stated boundary: nothing this
    module writes should ever include a password/token/secret-shaped key —
    that boundary belongs to CredentialStore/Keychain exclusively."""
    path = tmp_path / "config.json"
    set_eufy_device_id(path, "device-xyz")

    raw_text = path.read_text().lower()
    for forbidden in ("password", "token", "secret"):
        assert forbidden not in raw_text
