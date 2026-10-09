"""
Tests for trainiq.normalization.renormalize (issue #36) — the one-off
re-normalization pass for already-stored raw_activities rows.

Fixture-based only, per this project's established live-verification
boundary (CI has no access to the BO's real database) — builds its own
small SQLite DB via open_db(), never touches APP_SUPPORT_DIR/trainiq.db.
"""

from __future__ import annotations

import json
import runpy
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

from trainiq.connectors.peloton import PelotonConnector
from trainiq.connectors.peloton import PROVIDER as PELOTON_PROVIDER
from trainiq.connectors.strava_unofficial import PROVIDER, StravaUnofficialConnector
from trainiq.credentials.store import CredentialStore
from trainiq.normalization.renormalize import renormalize_provider
from trainiq.storage.schema import open_db

SCRIPT_PATH = Path(__file__).resolve().parent.parent / "scripts" / "renormalize_strava_unofficial.py"


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
        "start_time": "2026-01-05T07:00:00+0000",
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


# --- Issue #48: elevation gain, moving time, indoor/outdoor flag -----------

def test_renormalize_backfills_elevation_moving_time_indoor_from_stored_raw_payload(db, connector):
    """AC4's proof, end-to-end through renormalize_provider() (not just
    normalize() in isolation): a strava_unofficial raw_activities row
    already carries elevation_gain_raw/moving_time_raw/trainer today, so a
    renormalize pass alone (no re-fetch) must populate the three new
    normalized_activities columns from that already-stored payload."""
    payload = {
        **_raw_payload(99, "Ride", "Ride"),
        "moving_time_raw": 2800,
        "elapsed_time_raw": 3000,
        "elevation_gain_raw": 80.0,
        "trainer": False,
    }
    db.execute(
        "INSERT INTO raw_activities (provider, external_id, payload_json, fetched_at) "
        "VALUES (?, ?, ?, ?)",
        (PROVIDER, "99", json.dumps(payload), "2026-10-06T00:00:00+00:00"),
    )
    db.execute(
        """
        INSERT INTO normalized_activities
            (provider, external_id, start_time, duration_s, discipline, distance_m,
             avg_hr, max_hr, avg_power, max_power, calories,
             training_load, training_load_method, source_confidence)
        VALUES (?, '99', ?, 3000, 'other', 10000.0, NULL, NULL, NULL, NULL, NULL, NULL, 'unknown', 0.5)
        """,
        (PROVIDER, datetime(2026, 1, 5, 7, 0, 0, tzinfo=timezone.utc).isoformat()),
    )
    db.commit()

    result = renormalize_provider(db, PROVIDER, connector)
    db.commit()

    assert result.updated == 1
    row = db.execute(
        "SELECT elevation_gain_m, moving_time_s, is_indoor FROM normalized_activities "
        "WHERE provider = ? AND external_id = '99'",
        (PROVIDER,),
    ).fetchone()
    assert row["elevation_gain_m"] == 80.0
    assert row["moving_time_s"] == 2800
    assert row["is_indoor"] == 0  # False, stored as SQLite INTEGER


def test_renormalize_corrects_bst_shifted_start_time(db, connector):
    """AC4 (issue #43): a row stored under the old bug — start_time
    shifted +1h because start_date_local_raw was read as UTC — is
    corrected to the real UTC instant once re-normalized against the
    unchanged raw payload (which already carries the correct start_time
    field), without touching raw_activities."""
    correct_utc = datetime(2026, 10, 7, 18, 57, 34, tzinfo=timezone.utc)
    wrong_stored = datetime(2026, 10, 7, 19, 57, 34, tzinfo=timezone.utc)  # old bug's +1h
    payload = {
        "id": 99,
        "name": "Evening Ride",
        "display_type": "Ride",
        "activity_type_display_name": "Ride",
        "distance_raw": 20000.0,
        "moving_time_raw": 2800,
        "elapsed_time_raw": 2900,
        "elevation_gain_raw": 80.0,
        "start_time": correct_utc.strftime("%Y-%m-%dT%H:%M:%S+0000"),
        "start_date_local_raw": int(wrong_stored.timestamp()),
        "commute": False,
        "private": False,
        "has_latlng": True,
        "description": "",
    }
    payload_json = json.dumps(payload)
    db.execute(
        "INSERT INTO raw_activities (provider, external_id, payload_json, fetched_at) "
        "VALUES (?, ?, ?, ?)",
        (PROVIDER, "99", payload_json, "2026-10-07T20:00:00+00:00"),
    )
    db.execute(
        """
        INSERT INTO normalized_activities
            (provider, external_id, start_time, duration_s, discipline, distance_m,
             avg_hr, max_hr, avg_power, max_power, calories,
             training_load, training_load_method, source_confidence)
        VALUES (?, ?, ?, ?, 'cycling', ?, NULL, NULL, NULL, NULL, NULL, NULL, 'unknown', 0.5)
        """,
        (PROVIDER, "99", wrong_stored.isoformat(), 2900, 20000.0),
    )
    db.commit()

    renormalize_provider(db, PROVIDER, connector)
    db.commit()

    row = db.execute(
        "SELECT start_time FROM normalized_activities WHERE provider = ? AND external_id = ?",
        (PROVIDER, "99"),
    ).fetchone()
    assert row["start_time"] == correct_utc.isoformat()

    raw_row = db.execute(
        "SELECT payload_json FROM raw_activities WHERE provider = ? AND external_id = ?",
        (PROVIDER, "99"),
    ).fetchone()
    assert raw_row["payload_json"] == payload_json


# --- Console noise (AC1/AC6, issue #44) ------------------------------------

class _StopAfterConfigure(Exception):
    """Raised by the fake configure() below to short-circuit the script
    before it opens a real database — this test only needs to confirm the
    call happens, and happens first, not exercise the rest of main()."""


def test_renormalize_script_calls_logging_setup_configure_first(monkeypatch, tmp_path):
    """Structural guarantee behind AC1/AC6: without this call, loguru's
    default stderr handler stays active and every per-record diagnostic
    message (e.g. "training_load unknown") floods the console. Asserts
    the script's main() calls trainiq.logging_setup.configure() as its
    first action, before the real DB open."""
    import trainiq.logging_setup as logging_setup_module

    calls: list[Path] = []

    def _fake_configure(log_dir):
        calls.append(log_dir)
        raise _StopAfterConfigure()

    monkeypatch.setattr(logging_setup_module, "configure", _fake_configure)
    monkeypatch.setattr(sys, "argv", [
        "renormalize_strava_unofficial.py",
        "--db-path", str(tmp_path / "trainiq.db"),
    ])

    with pytest.raises(_StopAfterConfigure):
        runpy.run_path(str(SCRIPT_PATH), run_name="__main__")

    assert calls == [logging_setup_module.DEFAULT_LOG_DIR]


# --- Issue #46: renormalize_provider() must never wipe backfilled class metadata ---

def test_renormalize_peloton_never_wipes_already_backfilled_class_metadata(db):
    """The specific regression the COALESCE-based upsert (sync/engine.py)
    exists to prevent: a stored Peloton raw payload predating issue #46's
    download() has no `_class_*` keys at all, so connector.normalize(raw)
    returns None for all 5 new fields. Re-running renormalize_provider()
    for an unrelated future reason (e.g. a taxonomy fix, like issue #36's
    original strava_unofficial case) must NOT overwrite the class metadata
    the backfill script already wrote for this row."""
    raw_payload = {
        "id": "w1", "start_time": 1790014244, "end_time": 1790014543,
        "fitness_discipline": "cycling", "total_work": 23646.98, "distance": 1.2163,
        "calories": 30.64,
        # No _class_* keys — this row predates issue #46's download().
    }
    db.execute(
        "INSERT INTO raw_activities (provider, external_id, payload_json, fetched_at) "
        "VALUES (?, ?, ?, ?)",
        (PELOTON_PROVIDER, "w1", json.dumps(raw_payload), "2026-09-01T00:00:00+00:00"),
    )
    db.execute(
        """
        INSERT INTO normalized_activities
            (provider, external_id, start_time, duration_s, discipline, distance_m,
             avg_hr, max_hr, avg_power, max_power, calories,
             training_load, training_load_method, source_confidence,
             activity_title, instructor_name, class_type, planned_duration_s, provider_class_id)
        VALUES (?, 'w1', '2026-09-01T00:00:00+00:00', 299, 'cycling', 1216.3,
                NULL, NULL, 79.1, NULL, 30.64, NULL, 'unknown', 0.5,
                'Power Zone Max', 'Matt Wilpers', 'power_zone_max', 2700, 'ride-1')
        """,
        (PELOTON_PROVIDER,),
    )
    db.commit()

    peloton_connector = PelotonConnector(CredentialStore(conn=db), session=object())
    renormalize_provider(db, PELOTON_PROVIDER, peloton_connector)
    db.commit()

    row = dict(db.execute(
        "SELECT * FROM normalized_activities WHERE provider = ? AND external_id = 'w1'",
        (PELOTON_PROVIDER,),
    ).fetchone())
    assert row["activity_title"] == "Power Zone Max"
    assert row["instructor_name"] == "Matt Wilpers"
    assert row["class_type"] == "power_zone_max"
    assert row["planned_duration_s"] == 2700
    assert row["provider_class_id"] == "ride-1"
    # discipline DID get recomputed — proves this is a real re-
    # normalization pass, not a no-op.
    assert row["discipline"] == "cycling"
    # Issue #57: distance_m is PRESERVED, not wiped — this stored raw
    # payload predates issue #57's performance-fetch keys entirely (no
    # _performance_fetch_status/_distance_value/_distance_unit at all), so
    # normalize() returns distance_m=None ("not attempted this pass"), and
    # the COALESCE-based upsert (sync/engine.py) must preserve the
    # already-backfilled value rather than null it out — the exact
    # regression issue #57 exists to prevent.
    assert row["distance_m"] == 1216.3
