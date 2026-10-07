"""
Tests for trainiq.normalization.renormalize (issue #36) — the one-off
re-normalization pass for already-stored raw_activities rows.

Fixture-based only, per this project's established live-verification
boundary (CI has no access to the BO's real database) — builds its own
small SQLite DB via open_db(), never touches APP_SUPPORT_DIR/trainiq.db.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from trainiq.connectors.strava_unofficial import PROVIDER, StravaUnofficialConnector
from trainiq.credentials.store import CredentialStore
from trainiq.normalization.renormalize import renormalize_provider
from trainiq.storage.schema import open_db


@pytest.fixture(autouse=True)
def in_memory_keyring():
    import keyring
    from keyring.backends.fail import Keyring as FailKeyring

    class _InMemoryKeyring(FailKeyring):
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

    original = keyring.get_keyring()
    keyring.set_keyring(_InMemoryKeyring())
    yield
    keyring.set_keyring(original)


@pytest.fixture
def db(tmp_path: Path):
    conn = open_db(tmp_path / "trainiq.db")
    yield conn
    conn.close()


@pytest.fixture
def connector(db):
    return StravaUnofficialConnector(CredentialStore(conn=db))


def _raw_payload(external_id: int, display_type: str, activity_type_display_name: str) -> dict:
    """Shaped like the BO's live-captured evidence table (requirements doc,
    issue #36): Ride/Run/Walk/Workout/Mountain-Bike-Ride."""
    return {
        "id": external_id,
        "name": f"{display_type} activity",
        "display_type": display_type,
        "activity_type_display_name": activity_type_display_name,
        "distance_raw": 10000.0,
        "moving_time_raw": 1800,
        "elapsed_time_raw": 1900,
        "elevation_gain_raw": 50.0,
        "start_date_local_raw": int(datetime(2026, 1, 5, 7, 0, 0, tzinfo=timezone.utc).timestamp()),
        "commute": False,
        "private": False,
        "has_latlng": True,
        "description": "",
    }


# (external_id, display_type, activity_type_display_name, expected discipline)
_FIXTURE_ROWS = [
    (1, "Run", "Run", "running"),
    (2, "Ride", "Ride", "cycling"),
    (3, "Walk", "Walk", "other"),
    (4, "Mountain Bike Ride", "Ride", "cycling"),
    (5, "Workout", "Workout", "strength"),
]


def _seed_raw_and_stale_normalized(db) -> dict[int, str]:
    """Inserts a raw_activities row for each fixture plus a pre-existing
    normalized_activities row stuck at discipline="other" (simulating the
    real bug: every strava_unofficial row was stored as "other" before
    issue #36's taxonomy fix). Returns {external_id: payload_json} for the
    byte-for-byte raw_activities comparison later."""
    payloads: dict[int, str] = {}
    for external_id, display_type, activity_type_display_name, _expected in _FIXTURE_ROWS:
        payload = _raw_payload(external_id, display_type, activity_type_display_name)
        payload_json = json.dumps(payload)
        payloads[external_id] = payload_json
        db.execute(
            "INSERT INTO raw_activities (provider, external_id, payload_json, fetched_at) "
            "VALUES (?, ?, ?, ?)",
            (PROVIDER, str(external_id), payload_json, "2026-10-06T00:00:00+00:00"),
        )
        db.execute(
            """
            INSERT INTO normalized_activities
                (provider, external_id, start_time, duration_s, discipline, distance_m,
                 avg_hr, max_hr, avg_power, max_power, calories,
                 training_load, training_load_method, source_confidence)
            VALUES (?, ?, ?, ?, 'other', ?, NULL, NULL, NULL, NULL, NULL, NULL, 'unknown', 0.5)
            """,
            (
                PROVIDER, str(external_id),
                datetime(2026, 1, 5, 7, 0, 0, tzinfo=timezone.utc).isoformat(),
                1900, 10000.0,
            ),
        )
    db.commit()
    return payloads


def test_renormalize_corrects_discipline_for_every_stale_row(db, connector):
    _seed_raw_and_stale_normalized(db)

    result = renormalize_provider(db, PROVIDER, connector)
    db.commit()

    assert result.read == 5
    assert result.inserted == 0
    assert result.updated == 5
    assert result.skipped_malformed == 0
    assert result.skipped_no_external_id == 0

    rows = {
        row["external_id"]: row["discipline"]
        for row in db.execute(
            "SELECT external_id, discipline FROM normalized_activities WHERE provider = ?", (PROVIDER,)
        ).fetchall()
    }
    for external_id, _display_type, _activity_type, expected_discipline in _FIXTURE_ROWS:
        assert rows[str(external_id)] == expected_discipline


def test_renormalize_never_modifies_raw_activities(db, connector):
    payloads = _seed_raw_and_stale_normalized(db)

    renormalize_provider(db, PROVIDER, connector)
    db.commit()

    for external_id, expected_payload_json in payloads.items():
        row = db.execute(
            "SELECT payload_json FROM raw_activities WHERE provider = ? AND external_id = ?",
            (PROVIDER, str(external_id)),
        ).fetchone()
        assert row["payload_json"] == expected_payload_json


def test_renormalize_is_idempotent_on_second_run(db, connector):
    _seed_raw_and_stale_normalized(db)

    first = renormalize_provider(db, PROVIDER, connector)
    db.commit()
    second = renormalize_provider(db, PROVIDER, connector)
    db.commit()

    assert first.updated == 5
    assert second.read == 5
    assert second.inserted == 0
    assert second.updated == 5  # every row already existed — still "updated", not "inserted"
    assert second.skipped_malformed == 0

    rows_after_first = {
        row["external_id"]: dict(row)
        for row in db.execute(
            "SELECT * FROM normalized_activities WHERE provider = ?", (PROVIDER,)
        ).fetchall()
    }
    renormalize_provider(db, PROVIDER, connector)
    db.commit()
    rows_after_third = {
        row["external_id"]: dict(row)
        for row in db.execute(
            "SELECT * FROM normalized_activities WHERE provider = ?", (PROVIDER,)
        ).fetchall()
    }
    assert rows_after_first == rows_after_third


def test_renormalize_unknown_provider_reads_zero_rows(db, connector):
    _seed_raw_and_stale_normalized(db)

    result = renormalize_provider(db, "some_other_provider", connector)

    assert result.read == 0
    assert result.inserted == 0
    assert result.updated == 0
