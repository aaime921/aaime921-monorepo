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

from trainiq.connectors.base import RecordKind
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


# --- Issue #45: raw_transform, used by scripts/renormalize_peloton_distance.py ---

class _FakeConnector:
    """Captures exactly what `raw` dict it was called with — enough to
    prove raw_transform ran (or didn't) before connector.normalize()."""

    record_kind = RecordKind.ACTIVITY

    def __init__(self):
        self.seen_raw: list[dict] = []

    def normalize(self, raw: dict) -> dict:
        self.seen_raw.append(raw)
        return {
            "provider": "fake",
            "external_id": str(raw["id"]),
            "start_time": "2026-01-01T00:00:00+00:00",
            "duration_s": 1800,
            "discipline_raw": "cycling",
            "avg_hr": None, "max_hr": None, "avg_power": None, "max_power": None,
            "distance_m": raw.get("distance"),
            "calories": None,
            "synced_at": "2026-01-01T00:00:00+00:00",
        }


def test_renormalize_applies_raw_transform_before_normalize(db):
    db.execute(
        "INSERT INTO raw_activities (provider, external_id, payload_json, fetched_at) VALUES (?, ?, ?, ?)",
        ("fake", "1", json.dumps({"id": 1, "distance": 13.1816}), "2026-10-08T00:00:00+00:00"),
    )
    db.commit()
    fake_connector = _FakeConnector()

    renormalize_provider(
        db, "fake", fake_connector,
        raw_transform=lambda raw: {**raw, "_distance_unit": "mi"},
    )

    assert fake_connector.seen_raw == [{"id": 1, "distance": 13.1816, "_distance_unit": "mi"}]
    row = db.execute(
        "SELECT distance_m FROM normalized_activities WHERE provider = ? AND external_id = ?",
        ("fake", "1"),
    ).fetchone()
    assert row["distance_m"] == 13.1816


def test_renormalize_omitted_raw_transform_is_byte_identical_to_today(db, connector):
    """Proves the strava_unofficial call site (issue #36) is unaffected:
    omitting raw_transform entirely must behave exactly as before this
    parameter existed."""
    _seed_raw_and_stale_normalized(db)

    without_param = renormalize_provider(db, PROVIDER, connector)
    db.commit()
    rows_without = {
        row["external_id"]: dict(row)
        for row in db.execute("SELECT * FROM normalized_activities WHERE provider = ?", (PROVIDER,)).fetchall()
    }

    with_none = renormalize_provider(db, PROVIDER, connector, raw_transform=None)
    db.commit()
    rows_with_none = {
        row["external_id"]: dict(row)
        for row in db.execute("SELECT * FROM normalized_activities WHERE provider = ?", (PROVIDER,)).fetchall()
    }

    assert without_param == with_none
    assert rows_without == rows_with_none


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
