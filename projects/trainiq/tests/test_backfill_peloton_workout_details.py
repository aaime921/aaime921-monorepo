"""
Tests for scripts/backfill_peloton_workout_details.py — class metadata
(issue #46, AC6) and distance (issue #57, AC4), resolved independently
per row by one shared, resumable mechanism.

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

from trainiq.connectors.peloton import (
    CLASS_TYPE_LOOKUP_FAILED,
    CLASS_TYPE_NOT_A_CLASS,
    PERFORMANCE_FETCH_STATUS_FAILED,
    PERFORMANCE_FETCH_STATUS_OK,
    PROVIDER,
)
from trainiq.storage.schema import open_db
from trainiq.sync.engine import TransientError

SCRIPT_PATH = Path(__file__).resolve().parent.parent / "scripts" / "backfill_peloton_workout_details.py"


def _load_script_module():
    spec = importlib.util.spec_from_file_location("backfill_peloton_workout_details", SCRIPT_PATH)
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
    """Stands in for PelotonConnector — scripted fetch_class_details()/
    fetch_workout_performance() results, no network, no authenticate()
    needed."""

    def __init__(self):
        self.calls: list[str] = []
        self.performance_calls: list[str] = []
        self._responses: dict[str, object] = {}
        self._performance_responses: dict[str, object] = {}
        self._call_count_before_success: dict[str, int] = {}

    def script_response(self, ride_id: str, details: dict | None):
        self._responses[ride_id] = details

    def script_transient_then_success(self, ride_id: str, details: dict, fail_times: int):
        self._call_count_before_success[ride_id] = fail_times
        self._responses[ride_id] = details

    def script_performance(self, workout_id: str, performance: dict | None):
        self._performance_responses[workout_id] = performance

    def fetch_class_details(self, ride_id: str):
        self.calls.append(ride_id)
        remaining = self._call_count_before_success.get(ride_id, 0)
        if remaining > 0:
            self._call_count_before_success[ride_id] = remaining - 1
            raise TransientError("peloton: rate limited (fake)", retry_after_s=0.01)
        return self._responses.get(ride_id)

    def fetch_workout_performance(self, workout_id: str):
        self.performance_calls.append(workout_id)
        return self._performance_responses.get(workout_id)


def _seed_row(
    conn, external_id: str, raw_payload: dict,
    class_type: str | None = None, performance_fetch_status: str | None = None,
):
    conn.execute(
        "INSERT INTO raw_activities (provider, external_id, payload_json, fetched_at) "
        "VALUES (?, ?, ?, ?)",
        (PROVIDER, external_id, json.dumps(raw_payload), "2026-09-01T00:00:00+00:00"),
    )
    conn.execute(
        """
        INSERT INTO normalized_activities
            (provider, external_id, start_time, duration_s, discipline, source_confidence,
             class_type, performance_fetch_status)
        VALUES (?, ?, '2026-09-01T00:00:00+00:00', 300, 'cycling', 0.9, ?, ?)
        """,
        (PROVIDER, external_id, class_type, performance_fetch_status),
    )
    conn.commit()


def _row(conn, external_id: str) -> dict:
    return dict(conn.execute(
        "SELECT * FROM normalized_activities WHERE provider = ? AND external_id = ?",
        (PROVIDER, external_id),
    ).fetchone())


_BASE_COUNTS = {
    "processed": 0,
    "class_not_a_class": 0, "class_success": 0, "class_failed": 0, "class_skipped": 0,
    "distance_success": 0, "distance_failed": 0, "distance_skipped": 0,
}


def _counts(**overrides) -> dict:
    counts = dict(_BASE_COUNTS)
    counts.update(overrides)
    return counts


# --- Issue #46: class metadata branch (unchanged behavior) -----------------

def test_successful_run_processes_class_and_non_class_rows(db):
    _seed_row(db, "w1", {"id": "w1", "workout_type": "class", "peloton_id": "ride-1"},
              performance_fetch_status=PERFORMANCE_FETCH_STATUS_OK)
    _seed_row(db, "w2", {"id": "w2", "workout_type": "ride"},  # non-class
              performance_fetch_status=PERFORMANCE_FETCH_STATUS_OK)

    connector = FakeConnector()
    connector.script_response("ride-1", {"title": "Power Zone Max", "duration": 2700, "ride_type_id": "power_zone_max"})

    counts = script.run_backfill(db, connector, sleep_fn=lambda _delay: None)

    assert counts == _counts(processed=2, class_not_a_class=1, class_success=1,
                              distance_skipped=2)
    row_w1 = _row(db, "w1")
    assert row_w1["activity_title"] == "Power Zone Max"
    assert row_w1["planned_duration_s"] == 2700
    assert row_w1["provider_class_id"] == "ride-1"
    assert _row(db, "w2")["class_type"] == CLASS_TYPE_NOT_A_CLASS


def test_class_lookup_returning_none_is_recorded_as_lookup_failed(db):
    _seed_row(db, "w1", {"id": "w1", "workout_type": "class", "peloton_id": "ride-1"},
              performance_fetch_status=PERFORMANCE_FETCH_STATUS_OK)
    connector = FakeConnector()
    connector.script_response("ride-1", None)  # simulated 404

    counts = script.run_backfill(db, connector, sleep_fn=lambda _delay: None)

    assert counts["class_failed"] == 1
    assert _row(db, "w1")["class_type"] == CLASS_TYPE_LOOKUP_FAILED


def test_simulated_rate_limiting_backs_off_and_still_succeeds(db):
    """AC6: a TransientError must be retried (via retry_with_backoff/
    ADR-037), not fail the whole run."""
    _seed_row(db, "w1", {"id": "w1", "workout_type": "class", "peloton_id": "ride-1"},
              performance_fetch_status=PERFORMANCE_FETCH_STATUS_OK)
    connector = FakeConnector()
    connector.script_transient_then_success("ride-1", {"title": "Endurance", "duration": 1800, "ride_type_id": "endurance"}, fail_times=1)

    sleep_calls: list[float] = []
    counts = script.run_backfill(db, connector, sleep_fn=sleep_calls.append)

    assert counts["class_success"] == 1
    assert len(connector.calls) == 2  # one failure, one success
    assert len(sleep_calls) == 1  # backed off exactly once


def test_retry_failed_flag_reattempts_previously_failed_class_lookup(db):
    _seed_row(db, "w1", {"id": "w1", "workout_type": "class", "peloton_id": "ride-1"},
              class_type=CLASS_TYPE_LOOKUP_FAILED, performance_fetch_status=PERFORMANCE_FETCH_STATUS_OK)
    connector = FakeConnector()
    connector.script_response("ride-1", {"title": "Power Zone Max", "duration": 2700, "ride_type_id": "power_zone_max"})

    plain_run_counts = script.run_backfill(db, connector, sleep_fn=lambda _d: None)
    assert plain_run_counts["processed"] == 0  # skipped by a plain run
    assert connector.calls == []

    retry_counts = script.run_backfill(db, connector, retry_failed=True, sleep_fn=lambda _d: None)
    assert retry_counts["processed"] == 1
    assert retry_counts["class_success"] == 1
    assert _row(db, "w1")["activity_title"] == "Power Zone Max"


# --- Issue #57: distance branch ---------------------------------------------

def test_distance_fetch_success_converts_and_records_ok(db):
    _seed_row(db, "w1", {"id": "w1", "distance": 13.1816},
              class_type=CLASS_TYPE_NOT_A_CLASS)
    connector = FakeConnector()
    connector.script_performance("w1", {"distance_value": 13.1816, "distance_unit_raw": "mi"})

    counts = script.run_backfill(db, connector, sleep_fn=lambda _d: None)

    assert counts == _counts(processed=1, class_skipped=1, distance_success=1)
    row = _row(db, "w1")
    assert row["distance_m"] == pytest.approx(13.1816 * 1609.344)
    assert row["performance_fetch_status"] == PERFORMANCE_FETCH_STATUS_OK


def test_distance_fetch_returning_none_is_recorded_as_performance_fetch_status_failed(db):
    _seed_row(db, "w1", {"id": "w1", "distance": 13.1816}, class_type=CLASS_TYPE_NOT_A_CLASS)
    connector = FakeConnector()
    # script_performance not called for "w1" -> fetch_workout_performance returns None

    counts = script.run_backfill(db, connector, sleep_fn=lambda _d: None)

    assert counts["distance_failed"] == 1
    row = _row(db, "w1")
    assert row["distance_m"] is None
    assert row["performance_fetch_status"] == PERFORMANCE_FETCH_STATUS_FAILED


def test_distance_fetch_ok_but_unresolved_unit_with_real_distance_counts_as_failed(db):
    """A workout with a real workout-list `distance` whose performance
    summary doesn't resolve (missing/unrecognized unit) is a real failure
    — distance_m stays NULL, performance_fetch_status is still recorded
    "ok" (the fetch itself succeeded), matching normalize()'s own
    fail-safe exactly."""
    _seed_row(db, "w1", {"id": "w1", "distance": 13.1816}, class_type=CLASS_TYPE_NOT_A_CLASS)
    connector = FakeConnector()
    connector.script_performance("w1", {"distance_value": 13.1816, "distance_unit_raw": "furlongs"})

    counts = script.run_backfill(db, connector, sleep_fn=lambda _d: None)

    assert counts["distance_failed"] == 1
    row = _row(db, "w1")
    assert row["distance_m"] is None
    assert row["performance_fetch_status"] == PERFORMANCE_FETCH_STATUS_OK


def test_distance_fetch_ok_with_no_distance_at_all_counts_as_success_not_failed(db):
    """A meditation/strength workout with no raw `distance` at all is not
    a failure, even though there's nothing to store — same as
    normalize()'s silent, no-warning branch."""
    _seed_row(db, "w1", {"id": "w1"}, class_type=CLASS_TYPE_NOT_A_CLASS)  # no "distance" key
    connector = FakeConnector()
    connector.script_performance("w1", {"distance_value": None, "distance_unit_raw": None})

    counts = script.run_backfill(db, connector, sleep_fn=lambda _d: None)

    assert counts["distance_success"] == 1
    row = _row(db, "w1")
    assert row["distance_m"] is None
    assert row["performance_fetch_status"] == PERFORMANCE_FETCH_STATUS_OK


def test_retry_failed_flag_reattempts_previously_failed_distance_fetch(db):
    _seed_row(db, "w1", {"id": "w1", "distance": 13.1816},
              class_type=CLASS_TYPE_NOT_A_CLASS, performance_fetch_status=PERFORMANCE_FETCH_STATUS_FAILED)
    connector = FakeConnector()
    connector.script_performance("w1", {"distance_value": 13.1816, "distance_unit_raw": "mi"})

    plain_run_counts = script.run_backfill(db, connector, sleep_fn=lambda _d: None)
    assert plain_run_counts["processed"] == 0  # skipped by a plain run
    assert connector.performance_calls == []

    retry_counts = script.run_backfill(db, connector, retry_failed=True, sleep_fn=lambda _d: None)
    assert retry_counts["processed"] == 1
    assert retry_counts["distance_success"] == 1
    row = _row(db, "w1")
    assert row["distance_m"] == pytest.approx(13.1816 * 1609.344)


# --- Issue #57: both branches resolved independently in one pass -----------

def test_row_needing_both_concerns_resolves_both_independently(db):
    _seed_row(db, "w1", {"id": "w1", "distance": 13.1816, "workout_type": "class", "peloton_id": "ride-1"})
    connector = FakeConnector()
    connector.script_response("ride-1", {"title": "Power Zone Max", "duration": 2700, "ride_type_id": "power_zone_max"})
    connector.script_performance("w1", {"distance_value": 13.1816, "distance_unit_raw": "mi"})

    counts = script.run_backfill(db, connector, sleep_fn=lambda _d: None)

    assert counts == _counts(processed=1, class_success=1, distance_success=1)
    row = _row(db, "w1")
    assert row["activity_title"] == "Power Zone Max"
    assert row["distance_m"] == pytest.approx(13.1816 * 1609.344)
    assert row["performance_fetch_status"] == PERFORMANCE_FETCH_STATUS_OK


def test_row_missing_only_performance_fetch_status_does_not_touch_class_metadata(db):
    _seed_row(db, "w1", {"id": "w1", "distance": 13.1816, "workout_type": "class", "peloton_id": "ride-1"},
              class_type="power_zone_max")  # already backfilled, performance_fetch_status still NULL
    connector = FakeConnector()
    connector.script_performance("w1", {"distance_value": 13.1816, "distance_unit_raw": "mi"})

    counts = script.run_backfill(db, connector, sleep_fn=lambda _d: None)

    assert counts == _counts(processed=1, class_skipped=1, distance_success=1)
    assert connector.calls == []  # class branch never touched, no ride-detail call
    row = _row(db, "w1")
    assert row["class_type"] == "power_zone_max"  # untouched
    assert row["distance_m"] == pytest.approx(13.1816 * 1609.344)


def test_row_missing_only_class_metadata_does_not_touch_distance(db):
    _seed_row(db, "w1", {"id": "w1", "distance": 13.1816, "workout_type": "class", "peloton_id": "ride-1"},
              performance_fetch_status=PERFORMANCE_FETCH_STATUS_OK)  # distance already resolved, class_type still NULL
    db.execute("UPDATE normalized_activities SET distance_m = 21213.7 WHERE external_id = 'w1'")
    db.commit()
    connector = FakeConnector()
    connector.script_response("ride-1", {"title": "Power Zone Max", "duration": 2700, "ride_type_id": "power_zone_max"})

    counts = script.run_backfill(db, connector, sleep_fn=lambda _d: None)

    assert counts == _counts(processed=1, class_success=1, distance_skipped=1)
    assert connector.performance_calls == []  # distance branch never touched
    row = _row(db, "w1")
    assert row["activity_title"] == "Power Zone Max"
    assert row["distance_m"] == pytest.approx(21213.7)  # untouched


# --- AC6: resumability ------------------------------------------------------

def test_resume_after_simulated_interruption_does_not_reprocess_completed_rows(db):
    """AC6: already-processed rows are not re-fetched or duplicated when
    a second run follows a partial/interrupted one."""
    _seed_row(db, "w1", {"id": "w1", "workout_type": "class", "peloton_id": "ride-1"},
              performance_fetch_status=PERFORMANCE_FETCH_STATUS_OK)
    _seed_row(db, "w2", {"id": "w2", "workout_type": "class", "peloton_id": "ride-2"},
              performance_fetch_status=PERFORMANCE_FETCH_STATUS_OK)
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

    assert _row(db, "w1")["activity_title"] == "Power Zone"  # unchanged, not duplicated/altered
