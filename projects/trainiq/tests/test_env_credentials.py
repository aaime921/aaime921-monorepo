"""
Tests for Issue #70's env-var credential backend
(trainiq/credentials/env_backend.py) and its selection/rotation wiring in
CredentialStore (trainiq/credentials/store.py).

Covers requirements doc ACs 1-4 and 11 (env backend get/exists/missing,
rotation write-back file content/permissions, no secret leakage into
status.json/logs is exercised in test_headless.py instead — this file is
scoped to the credential layer itself).
"""

from __future__ import annotations

import json
import os
import stat
from pathlib import Path

import keyring
import pytest
from keyring.backends.fail import Keyring as FailKeyring

from trainiq.credentials.env_backend import EnvBackend
from trainiq.credentials.store import CredentialStore


class _InMemoryKeyring(FailKeyring):
    """Same deterministic in-memory keyring double test_credentials.py
    uses, so AC2 ("without TRAINIQ_CREDENTIAL_BACKEND, behaviour is
    Keychain exactly as today") can be checked from this file too without
    touching a real OS keychain."""

    priority = 1

    def __init__(self):
        self._store: dict[tuple[str, str], str] = {}

    def set_password(self, service, username, password):
        self._store[(service, username)] = password

    def get_password(self, service, username):
        return self._store.get((service, username))

    def delete_password(self, service, username):
        key = (service, username)
        if key not in self._store:
            from keyring.errors import PasswordDeleteError
            raise PasswordDeleteError("not found")
        del self._store[key]


@pytest.fixture(autouse=True)
def in_memory_keyring():
    original = keyring.get_keyring()
    keyring.set_keyring(_InMemoryKeyring())
    yield
    keyring.set_keyring(original)


# --- EnvBackend, used directly ----------------------------------------------

def test_env_backend_name_matches_push_secrets_to_github_convention():
    assert EnvBackend.env_var_name("peloton", "oauth_refresh_token") == "TRAINIQ_PELOTON_OAUTH_REFRESH_TOKEN"
    assert EnvBackend.env_var_name("strava_unofficial", "session_cookie") == "TRAINIQ_STRAVA_UNOFFICIAL_SESSION_COOKIE"


def test_env_backend_reads_from_os_environ(monkeypatch):
    monkeypatch.setenv("TRAINIQ_STRAVA_REFRESH_TOKEN", "from-env")
    backend = EnvBackend()
    assert backend.get("strava", "refresh_token") == "from-env"


def test_env_backend_missing_var_behaves_like_missing_credential(monkeypatch):
    monkeypatch.delenv("TRAINIQ_PELOTON_OAUTH_REFRESH_TOKEN", raising=False)
    backend = EnvBackend()
    assert backend.get("peloton", "oauth_refresh_token") is None


def test_env_backend_empty_string_var_behaves_like_missing(monkeypatch):
    monkeypatch.setenv("TRAINIQ_EUFY_PASSWORD", "")
    backend = EnvBackend()
    assert backend.get("eufy", "password") is None


def test_env_backend_set_does_not_mutate_os_environ(monkeypatch):
    monkeypatch.delenv("TRAINIQ_STRAVA_REFRESH_TOKEN", raising=False)
    backend = EnvBackend()
    backend.set("strava", "refresh_token", "new-value")
    assert backend.get("strava", "refresh_token") == "new-value"
    assert "TRAINIQ_STRAVA_REFRESH_TOKEN" not in os.environ


def test_env_backend_set_overlay_overrides_os_environ(monkeypatch):
    monkeypatch.setenv("TRAINIQ_STRAVA_REFRESH_TOKEN", "stale-env-value")
    backend = EnvBackend()
    backend.set("strava", "refresh_token", "rotated-value")
    assert backend.get("strava", "refresh_token") == "rotated-value"


def test_env_backend_delete_removes_overlay_and_masks_os_environ(monkeypatch):
    monkeypatch.setenv("TRAINIQ_STRAVA_REFRESH_TOKEN", "still-in-os-environ")
    backend = EnvBackend()
    backend.set("strava", "refresh_token", "overlay-value")
    backend.delete("strava", "refresh_token")
    assert backend.get("strava", "refresh_token") is None


# --- Rotation write-back (--credentials-out / TRAINIQ_CREDENTIALS_OUT, AC3/4) -

def test_rotation_writes_credentials_out_file(tmp_path: Path):
    out_path = tmp_path / "rotated.json"
    backend = EnvBackend(out_path=str(out_path))
    backend.set("peloton", "oauth_refresh_token", "refresh-value-1")

    assert out_path.exists()
    payload = json.loads(out_path.read_text())
    assert payload == {
        "version": 1,
        "credentials": {"TRAINIQ_PELOTON_OAUTH_REFRESH_TOKEN": "refresh-value-1"},
    }


def test_rotation_file_has_owner_only_permissions(tmp_path: Path):
    out_path = tmp_path / "rotated.json"
    backend = EnvBackend(out_path=str(out_path))
    backend.set("peloton", "oauth_refresh_token", "refresh-value-1")

    mode = stat.S_IMODE(out_path.stat().st_mode)
    assert mode == 0o600


def test_second_rotation_rewrites_not_appends(tmp_path: Path):
    out_path = tmp_path / "rotated.json"
    backend = EnvBackend(out_path=str(out_path))
    backend.set("peloton", "oauth_refresh_token", "first-value")
    backend.set("peloton", "oauth_access_token", "second-value")

    payload = json.loads(out_path.read_text())
    assert payload["credentials"] == {
        "TRAINIQ_PELOTON_OAUTH_REFRESH_TOKEN": "first-value",
        "TRAINIQ_PELOTON_OAUTH_ACCESS_TOKEN": "second-value",
    }


def test_credentials_out_file_survives_without_out_path_set():
    """No --credentials-out/TRAINIQ_CREDENTIALS_OUT: rotation must still
    work in-memory for the lifetime of the run, just without persisting
    anywhere (per the architecture doc's "rotation stays in memory"
    fallback)."""
    backend = EnvBackend(out_path=None)
    backend.set("peloton", "oauth_refresh_token", "value")
    assert backend.get("peloton", "oauth_refresh_token") == "value"


def test_rotation_file_present_even_if_a_later_set_call_happens(tmp_path: Path):
    """AC3: a rotated credential is written before the run can exit,
    including on partial failure — simulated here by writing once, then
    confirming the file still holds that value even though the caller
    never gets to make a second call (e.g. a later provider raises)."""
    out_path = tmp_path / "rotated.json"
    backend = EnvBackend(out_path=str(out_path))
    backend.set("peloton", "oauth_refresh_token", "must-not-be-lost")

    assert json.loads(out_path.read_text())["credentials"]["TRAINIQ_PELOTON_OAUTH_REFRESH_TOKEN"] == "must-not-be-lost"


# --- CredentialStore backend selection (AC1/AC2) ----------------------------

def test_credential_store_defaults_to_keyring_backend():
    store = CredentialStore()
    store.set("strava", "refresh_token", "keychain-value")
    assert store.get("strava", "refresh_token") == "keychain-value"
    # Proof it actually went through Keychain, not an env overlay:
    assert keyring.get_password("trainiq.strava.refresh_token", "trainiq-user") == "keychain-value"


def test_credential_store_selects_env_backend_when_env_var_set(monkeypatch):
    monkeypatch.setenv("TRAINIQ_CREDENTIAL_BACKEND", "env")
    monkeypatch.setenv("TRAINIQ_STRAVA_REFRESH_TOKEN", "from-secret")

    store = CredentialStore()

    assert store.get("strava", "refresh_token") == "from-secret"
    # And Keychain must be completely untouched by this read.
    assert keyring.get_password("trainiq.strava.refresh_token", "trainiq-user") is None


def test_credential_store_env_backend_missing_var_is_missing_not_error(monkeypatch):
    monkeypatch.setenv("TRAINIQ_CREDENTIAL_BACKEND", "env")
    monkeypatch.delenv("TRAINIQ_PELOTON_OAUTH_REFRESH_TOKEN", raising=False)

    store = CredentialStore()

    assert store.get("peloton", "oauth_refresh_token") is None
    assert not store.exists("peloton", "oauth_refresh_token")


def test_credential_store_rotate_under_env_backend_writes_credentials_out(monkeypatch, tmp_path):
    monkeypatch.setenv("TRAINIQ_CREDENTIAL_BACKEND", "env")
    out_path = tmp_path / "rotated.json"

    store = CredentialStore(credentials_out=str(out_path))
    store.rotate("peloton", "oauth_refresh_token", "new-refresh-token")

    assert store.get("peloton", "oauth_refresh_token") == "new-refresh-token"
    payload = json.loads(out_path.read_text())
    assert payload["credentials"]["TRAINIQ_PELOTON_OAUTH_REFRESH_TOKEN"] == "new-refresh-token"


def test_credentials_out_env_var_used_when_flag_not_given(monkeypatch, tmp_path):
    monkeypatch.setenv("TRAINIQ_CREDENTIAL_BACKEND", "env")
    out_path = tmp_path / "rotated.json"
    monkeypatch.setenv("TRAINIQ_CREDENTIALS_OUT", str(out_path))

    store = CredentialStore()  # no explicit credentials_out — must fall back to the env var
    store.rotate("peloton", "oauth_refresh_token", "new-refresh-token")

    assert json.loads(out_path.read_text())["credentials"]["TRAINIQ_PELOTON_OAUTH_REFRESH_TOKEN"] == "new-refresh-token"


def test_existing_test_suite_assumption_unaffected_when_backend_unset():
    """A fresh CredentialStore() with no TRAINIQ_CREDENTIAL_BACKEND set at
    all must behave exactly like the pre-#70 Keychain-only store — this is
    AC2's core promise, re-checked from this file's own fixtures."""
    store = CredentialStore()
    assert not store.exists("eufy", "refresh_token")
    store.set("eufy", "refresh_token", "xyz")
    assert store.exists("eufy", "refresh_token")
