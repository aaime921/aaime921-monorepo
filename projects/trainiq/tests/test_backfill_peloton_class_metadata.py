"""
Tests for scripts/backfill_peloton_class_metadata.py (issue #46, AC6).

Fixture-based only, per this project's established live-verification
boundary (CI has no access to the BO's real Peloton account) — builds its
own small SQLite DB via open_db(), seeds raw_activities/normalized_activities
directly, and drives the script's run_backfill() against a fake connector.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

from trainiq.connectors.peloton import CLASS_TYPE_LOOKUP_FAILED, CLASS_TYPE_NOT_A_CLASS, PROVIDER
from trainiq.storage.schema import open_db
from trainiq.sync.engine import TransientError

SCRIPT_PATH = Path(__file__).resolve().parent.parent / "scripts" / "backfill_peloton_class_metadata.py"


def _load_script_module():
    spec = importlib.util.spec_from_file_location("backfill_peloton_class_metadata", SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


script = _load_script_module()


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


class FakeConnector:
    """Stands in for PelotonConnector — scripted fetch_class_details()
    results, no network, no authenticate() needed."""

    def __init__(self):
        self.calls: list[str] = []
        self._responses: dict[str, object] = {}
        self._exceptions: dict[str, Exception] = {}
        self._call_count_before_success: dict[str, int] = {}

    def script_response(self, ride_id: str, details: dict | None):
        self._responses[ride_id] = details

    def script_transient_then_success(self, ride_id: str, details: dict, fail_times: int):
        self._call_count_before_success[ride_id] = fail_times
        self._responses[ride_id] = details

    def fetch_class_details(self, ride_id: str):
        self.calls.append(ride_id)
        remaining = self._call_count_before_success.get(ride_id, 0)
        if remaining > 0:
            self._call_count_before_success[ride_id] = remaining - 1
            raise TransientError("peloton: rate limited (fake)", retry_after_s=0.01)
        return self._responses.get(ride_id)


def _seed_row(conn, external_id: str, raw_payload: dict, class_type: str | None = None):
    conn.execute(
        "INSERT INTO raw_activities (provider, external_id, payload_json, fetched_at) "
        "VALUES (?, ?, ?, ?)",
        (PROVIDER, external_id, json.dumps(raw_payload), "2026-09-01T00:00:00+00:00"),
    )
    conn.execute(
        """
        INSERT INTO normalized_activities
            (provider, external_id, start_time, duration_s, discipline, source_confidence, class_type)
        VALUES (?, ?, '2026-09-01T00:00:00+00:00', 300, 'cycling', 0.9, ?)
        """,
        (PROVIDER, external_id, class_type),
    )
    conn.commit()


def _class_type_of(conn, external_id: str) -> str | None:
    row = conn.execute(
        "SELECT class_type FROM normalized_activities WHERE provider = ? AND external_id = ?",
        (PROVIDER, external_id),
    ).fetchone()
    return row["class_type"] if row else None


def test_successful_run_processes_class_and_non_class_rows(db):
    _seed_row(db, "w1", {"id": "w1", "workout_type": "class", "peloton_id": "ride-1"})
    _seed_row(db, "w2", {"id": "w2", "workout_type": "ride"})  # non-class

    connector = FakeConnector()
    connector.script_response("ride-1", {"title": "Power Zone Max", "duration": 2700, "ride_type_id": "power_zone_max"})

    counts = script.run_backfill(db, connector, sleep_fn=lambda _delay: None)

    assert counts == {"processed": 2, "not_a_class": 1, "success": 1, "failed": 0}
    row_w1 = dict(db.execute(
        "SELECT * FROM normalized_activities WHERE provider = ? AND external_id = 'w1'", (PROVIDER,)
    ).fetchone())
    assert row_w1["activity_title"] == "Power Zone Max"
    assert row_w1["planned_duration_s"] == 2700
    assert row_w1["provider_class_id"] == "ride-1"
    assert _class_type_of(db, "w2") == CLASS_TYPE_NOT_A_CLASS


def test_lookup_returning_none_is_recorded_as_lookup_failed(db):
    _seed_row(db, "w1", {"id": "w1", "workout_type": "class", "peloton_id": "ride-1"})
    connector = FakeConnector()
    connector.script_response("ride-1", None)  # simulated 404

    counts = script.run_backfill(db, connector, sleep_fn=lambda _delay: None)

    assert counts["failed"] == 1
    assert _class_type_of(db, "w1") == CLASS_TYPE_LOOKUP_FAILED


def test_simulated_rate_limiting_backs_off_and_still_succeeds(db):
    """AC6: a TransientError must be retried (via retry_with_backoff/
    ADR-037), not fail the whole run."""
    _seed_row(db, "w1", {"id": "w1", "workout_type": "class", "peloton_id": "ride-1"})
    connector = FakeConnector()
    connector.script_transient_then_success("ride-1", {"title": "Endurance", "duration": 1800, "ride_type_id": "endurance"}, fail_times=1)

    sleep_calls: list[float] = []
    counts = script.run_backfill(db, connector, sleep_fn=sleep_calls.append)

    assert counts["success"] == 1
    assert len(connector.calls) == 2  # one failure, one success
    assert len(sleep_calls) == 1  # backed off exactly once


def test_resume_after_simulated_interruption_does_not_reprocess_completed_rows(db):
    """AC6: already-processed rows are not re-fetched or duplicated when
    a second run follows a partial/interrupted one."""
    _seed_row(db, "w1", {"id": "w1", "workout_type": "class", "peloton_id": "ride-1"})
    _seed_row(db, "w2", {"id": "w2", "workout_type": "class", "peloton_id": "ride-2"})
    connector = FakeConnector()
    connector.script_response("ride-1", {"title": "Power Zone", "duration": 2400, "ride_type_id": "power_zone"})
    connector.script_response("ride-2", {"title": "Climb", "duration": 1800, "ride_type_id": "climb"})

    # Simulate an interrupted run: process only the first candidate.
    first_batch_counts = script.run_backfill(db, connector, limit=1, sleep_fn=lambda _d: None)
    assert first_batch_counts["processed"] == 1
    assert connector.calls == ["ride-1"]

    # Resume: the second invocation must pick up exactly the remaining row.
    second_batch_counts = script.run_backfill(db, connector, sleep_fn=lambda _d: None)
    assert second_batch_counts["processed"] == 1
    assert connector.calls == ["ride-1", "ride-2"]  # ride-1 never re-fetched

    row_w1 = dict(db.execute(
        "SELECT * FROM normalized_activities WHERE provider = ? AND external_id = 'w1'", (PROVIDER,)
    ).fetchone())
    assert row_w1["activity_title"] == "Power Zone"  # unchanged, not duplicated/altered


def test_retry_failed_flag_reattempts_previously_failed_rows(db):
    _seed_row(db, "w1", {"id": "w1", "workout_type": "class", "peloton_id": "ride-1"},
              class_type=CLASS_TYPE_LOOKUP_FAILED)
    connector = FakeConnector()
    connector.script_response("ride-1", {"title": "Power Zone Max", "duration": 2700, "ride_type_id": "power_zone_max"})

    plain_run_counts = script.run_backfill(db, connector, sleep_fn=lambda _d: None)
    assert plain_run_counts["processed"] == 0  # skipped by a plain run
    assert connector.calls == []

    retry_counts = script.run_backfill(db, connector, retry_failed=True, sleep_fn=lambda _d: None)
    assert retry_counts["processed"] == 1
    assert retry_counts["success"] == 1
    row_w1 = dict(db.execute(
        "SELECT * FROM normalized_activities WHERE provider = ? AND external_id = 'w1'", (PROVIDER,)
    ).fetchone())
    assert row_w1["activity_title"] == "Power Zone Max"
