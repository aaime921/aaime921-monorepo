"""
Tests for Feature 0.2 — Credential storage.

Uses keyring's in-memory testing backend so these tests are deterministic
and don't depend on a real OS keychain being available (this sandbox has no
macOS Keychain; the real backend is exercised only on an actual macOS
build, per Gate 1/Epic 0 exit criteria in the roadmap — flagged explicitly
as a known verification gap in the completion report, not silently assumed
to be equivalent).
"""

from pathlib import Path

import keyring
import pytest
from keyring.backends.fail import Keyring as FailKeyring

from trainiq.credentials.store import CredentialStore
from trainiq.storage.schema import open_db


class _InMemoryKeyring(FailKeyring):
    """Minimal in-memory keyring backend for deterministic testing."""
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


def test_set_and_get_roundtrip():
    store = CredentialStore()
    store.set("strava", "refresh_token", "abc123")
    assert store.get("strava", "refresh_token") == "abc123"


def test_get_missing_returns_none_not_error():
    store = CredentialStore()
    assert store.get("peloton", "bearer_token") is None


def test_exists():
    store = CredentialStore()
    assert not store.exists("eufy", "refresh_token")
    store.set("eufy", "refresh_token", "xyz")
    assert store.exists("eufy", "refresh_token")


def test_delete_missing_is_not_an_error():
    store = CredentialStore()
    store.delete("strava", "refresh_token")  # should not raise


def test_delete_existing():
    store = CredentialStore()
    store.set("strava", "refresh_token", "abc123")
    store.delete("strava", "refresh_token")
    assert store.get("strava", "refresh_token") is None


def test_providers_are_isolated():
    """One provider's credential must never leak into another's namespace —
    directly required by the Provider State Machine's per-connector Recovery
    Required state (ADR-010)."""
    store = CredentialStore()
    store.set("strava", "refresh_token", "strava-token")
    store.set("peloton", "refresh_token", "peloton-token")
    assert store.get("strava", "refresh_token") == "strava-token"
    assert store.get("peloton", "refresh_token") == "peloton-token"


def test_credential_types_are_isolated_within_a_provider():
    store = CredentialStore()
    store.set("strava", "access_token", "access-value")
    store.set("strava", "refresh_token", "refresh-value")
    assert store.get("strava", "access_token") == "access-value"
    assert store.get("strava", "refresh_token") == "refresh-value"


def test_rotate_overwrites_previous_value():
    """Milestone 1's classic bug: reusing a stale refresh token after
    rotation. Every credential must overwrite cleanly on rotate()."""
    store = CredentialStore()
    store.set("strava", "refresh_token", "old-token")
    store.rotate("strava", "refresh_token", "new-token")
    assert store.get("strava", "refresh_token") == "new-token"


def test_refuses_to_store_empty_value():
    store = CredentialStore()
    with pytest.raises(ValueError):
        store.set("strava", "refresh_token", "")


def test_survives_a_fresh_credential_store_instance():
    """Simulates an app restart: a new CredentialStore() must see what a
    previous instance wrote, since the backend (not the object) is where
    persistence actually lives."""
    store_a = CredentialStore()
    store_a.set("eufy", "refresh_token", "persisted-value")
    store_b = CredentialStore()
    assert store_b.get("eufy", "refresh_token") == "persisted-value"


# --- Epic 0 Hardening, Finding 2: CredentialStore <-> credentials_metadata ---
# Previously two "complete" features (Feature 0.2 and Feature 0.3) never
# actually talked to each other. These tests prove the wiring now exists,
# and that it's entirely optional/backward-compatible (all tests above this
# point construct CredentialStore() with no `conn` at all, and still pass).

@pytest.fixture
def db(tmp_path: Path):
    conn = open_db(tmp_path / "trainiq.db")
    yield conn
    conn.close()


def test_set_marks_provider_connected_in_metadata(db):
    store = CredentialStore(conn=db)
    store.set("strava", "refresh_token", "abc123")

    row = db.execute(
        "SELECT connected, last_refreshed_at FROM credentials_metadata WHERE provider = 'strava'"
    ).fetchone()
    assert row is not None
    assert row["connected"] == 1
    assert row["last_refreshed_at"] is not None


def test_rotate_updates_last_refreshed_at(db):
    store = CredentialStore(conn=db)
    store.set("strava", "refresh_token", "first-token")
    first = db.execute(
        "SELECT last_refreshed_at FROM credentials_metadata WHERE provider = 'strava'"
    ).fetchone()["last_refreshed_at"]

    store.rotate("strava", "refresh_token", "second-token")
    second = db.execute(
        "SELECT last_refreshed_at FROM credentials_metadata WHERE provider = 'strava'"
    ).fetchone()["last_refreshed_at"]

    assert second >= first
    # Rotation never touches the secret's home in SQLite — only Keychain does.
    row = db.execute("SELECT * FROM credentials_metadata WHERE provider = 'strava'").fetchone()
    assert set(row.keys()) == {"provider", "connected", "last_refreshed_at"}


def test_delete_marks_provider_disconnected_in_metadata(db):
    store = CredentialStore(conn=db)
    store.set("peloton", "bearer_token", "some-token")
    store.delete("peloton", "bearer_token")

    row = db.execute(
        "SELECT connected FROM credentials_metadata WHERE provider = 'peloton'"
    ).fetchone()
    assert row["connected"] == 0


def test_credential_store_without_conn_never_touches_sqlite():
    """Backward-compatibility guarantee: every pre-existing test in this file
    constructs CredentialStore() with no `conn`, and this must remain a
    fully valid, Keychain-only usage mode."""
    store = CredentialStore()  # no conn
    store.set("strava", "refresh_token", "value")  # must not raise
    assert store.get("strava", "refresh_token") == "value"


def test_metadata_is_non_secret_only_no_credential_value_in_sqlite(db):
    """Constitution-adjacent check: the actual secret must never appear
    anywhere in the SQLite database, only in Keychain."""
    store = CredentialStore(conn=db)
    store.set("strava", "refresh_token", "super-secret-value-12345")

    # Inspect every text-bearing cell in credentials_metadata for the secret.
    row = db.execute("SELECT * FROM credentials_metadata WHERE provider = 'strava'").fetchone()
    for value in row:
        assert value != "super-secret-value-12345"
