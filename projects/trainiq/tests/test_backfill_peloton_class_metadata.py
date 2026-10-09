"""
Tests for scripts/backfill_peloton_class_metadata.py (issue #46, AC6;
updated for issue #58's two-step peloton_id -> ride_id resolution,
resolving BL-011).

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
    """Stands in for PelotonConnector — scripted fetch_class_session()/
    fetch_class_details() results, no network, no authenticate() needed.
    Each step has its own independent call log and scripted
    responses/failures, mirroring the two real network calls in the
    two-step lookup (issue #58)."""

    def __init__(self):
        self.session_calls: list[str] = []
        self.calls: list[str] = []  # fetch_class_details() calls, by ride_id — kept for existing tests
        self._session_responses: dict[str, dict | None] = {}
        self._responses: dict[str, object] = {}
        self._session_fail_times: dict[str, int] = {}
        self._call_count_before_success: dict[str, int] = {}

    def script_session_response(self, peloton_id: str, ride_id: str | None):
        """ride_id=None simulates a 404 at the session-resolution step."""
        self._session_responses[peloton_id] = {"ride_id": ride_id} if ride_id is not None else None

    def script_session_transient_then_success(self, peloton_id: str, ride_id: str, fail_times: int):
        self._session_fail_times[peloton_id] = fail_times
        self._session_responses[peloton_id] = {"ride_id": ride_id}

    def script_response(self, ride_id: str, details: dict | None):
        self._responses[ride_id] = details

    def script_transient_then_success(self, ride_id: str, details: dict, fail_times: int):
        self._call_count_before_success[ride_id] = fail_times
        self._responses[ride_id] = details

    def fetch_class_session(self, peloton_id: str):
        self.session_calls.append(peloton_id)
        remaining = self._session_fail_times.get(peloton_id, 0)
        if remaining > 0:
            self._session_fail_times[peloton_id] = remaining - 1
            raise TransientError("peloton: rate limited (fake)", retry_after_s=0.01)
        # Default: a session not explicitly scripted resolves 1:1 to a
        # same-named ride_id, so every existing test that only scripts
        # fetch_class_details() (and never calls script_session_response())
        # keeps working unchanged.
        if peloton_id not in self._session_responses:
            return {"ride_id": peloton_id}
        return self._session_responses[peloton_id]

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


def _ride_details(title: str, duration: int, class_type_name: str = "Power Zone", difficulty_estimate: float | None = None):
    """A GET /api/ride/{ride_id}/details success body — live-evidence
    shape from issue #58: `ride` is nested, `class_types` is a top-level
    sibling of `ride`, not nested under it."""
    return {
        "ride": {"title": title, "duration": duration, "difficulty_estimate": difficulty_estimate, "instructor": {"name": "Matt Wilpers"}},
        "class_types": [{"name": class_type_name}],
    }


def test_successful_run_processes_class_and_non_class_rows(db):
    """Fixtures shaped like the BO's 131 lookup_failed rows, per the
    requirements doc's AC5 — this row's raw payload has a session id
    (peloton_id) distinct from its resolved ride_id, proving the two-step
    lookup (not the old single-call one) is what the script now exercises."""
    _seed_row(db, "w1", {"id": "w1", "workout_type": "class", "peloton_id": "session-1"})
    _seed_row(db, "w2", {"id": "w2", "workout_type": "ride"})  # non-class

    connector = FakeConnector()
    connector.script_session_response("session-1", "ride-1")
    connector.script_response("ride-1", _ride_details("Power Zone Max", 2700, difficulty_estimate=8.40))

    counts = script.run_backfill(db, connector, sleep_fn=lambda _delay: None)

    assert counts == {"processed": 2, "not_a_class": 1, "success": 1, "failed": 0}
    row_w1 = dict(db.execute(
        "SELECT * FROM normalized_activities WHERE provider = ? AND external_id = 'w1'", (PROVIDER,)
    ).fetchone())
    assert row_w1["activity_title"] == "Power Zone Max"
    assert row_w1["instructor_name"] == "Matt Wilpers"
    assert row_w1["class_type"] == "Power Zone"
    assert row_w1["planned_duration_s"] == 2700
    assert row_w1["provider_class_id"] == "ride-1"  # the RESOLVED ride_id, not the session id
    assert row_w1["difficulty_estimate"] == 8.40
    assert _class_type_of(db, "w2") == CLASS_TYPE_NOT_A_CLASS


def test_session_resolution_404_is_recorded_as_lookup_failed_without_fetching_ride_details(db):
    """Issue #58, AC4/AC8: the literal old-bug scenario — a session id
    can't be resolved — must fail at step 1 and never attempt step 2."""
    _seed_row(db, "w1", {"id": "w1", "workout_type": "class", "peloton_id": "session-1"})
    connector = FakeConnector()
    connector.script_session_response("session-1", None)  # simulated 404 on GET /api/peloton/{peloton_id}

    counts = script.run_backfill(db, connector, sleep_fn=lambda _delay: None)

    assert counts["failed"] == 1
    assert _class_type_of(db, "w1") == CLASS_TYPE_LOOKUP_FAILED
    assert connector.calls == []  # fetch_class_details() never called


def test_ride_details_404_after_successful_session_resolution_is_recorded_as_lookup_failed(db):
    """Issue #58, AC4/AC8: the SECOND, distinct failure point — the
    session resolves fine, but the resolved ride_id's details fetch 404s."""
    _seed_row(db, "w1", {"id": "w1", "workout_type": "class", "peloton_id": "session-1"})
    connector = FakeConnector()
    connector.script_session_response("session-1", "ride-1")
    connector.script_response("ride-1", None)  # simulated 404 on GET /api/ride/{ride_id}/details

    counts = script.run_backfill(db, connector, sleep_fn=lambda _delay: None)

    assert counts["failed"] == 1
    assert _class_type_of(db, "w1") == CLASS_TYPE_LOOKUP_FAILED
    assert connector.session_calls == ["session-1"]
    assert connector.calls == ["ride-1"]  # step 2 was attempted, unlike the step-1 failure above


def test_simulated_rate_limiting_backs_off_and_still_succeeds(db):
    """AC6: a TransientError must be retried (via retry_with_backoff/
    ADR-037), not fail the whole run. Covers both steps independently,
    since each is wrapped in its own retry_with_backoff() call."""
    _seed_row(db, "w1", {"id": "w1", "workout_type": "class", "peloton_id": "session-1"})
    _seed_row(db, "w2", {"id": "w2", "workout_type": "class", "peloton_id": "session-2"})
    connector = FakeConnector()
    connector.script_session_transient_then_success("session-1", "ride-1", fail_times=1)
    connector.script_response("ride-1", _ride_details("Endurance", 1800, class_type_name="Endurance"))
    connector.script_session_response("session-2", "ride-2")
    connector.script_transient_then_success("ride-2", _ride_details("Climb", 1800, class_type_name="Climb"), fail_times=1)

    sleep_calls: list[float] = []
    counts = script.run_backfill(db, connector, sleep_fn=sleep_calls.append)

    assert counts["success"] == 2
    assert len(connector.session_calls) == 3  # session-1 failed once then succeeded, session-2 once
    assert len(connector.calls) == 3  # ride-1 succeeded immediately, ride-2 failed once then succeeded
    assert len(sleep_calls) == 2  # backed off exactly once per step


def test_resume_after_simulated_interruption_does_not_reprocess_completed_rows(db):
    """AC6: already-processed rows are not re-fetched or duplicated when
    a second run follows a partial/interrupted one."""
    _seed_row(db, "w1", {"id": "w1", "workout_type": "class", "peloton_id": "session-1"})
    _seed_row(db, "w2", {"id": "w2", "workout_type": "class", "peloton_id": "session-2"})
    connector = FakeConnector()
    connector.script_session_response("session-1", "ride-1")
    connector.script_response("ride-1", _ride_details("Power Zone", 2400, class_type_name="Power Zone"))
    connector.script_session_response("session-2", "ride-2")
    connector.script_response("ride-2", _ride_details("Climb", 1800, class_type_name="Climb"))

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
    _seed_row(db, "w1", {"id": "w1", "workout_type": "class", "peloton_id": "session-1"},
              class_type=CLASS_TYPE_LOOKUP_FAILED)
    connector = FakeConnector()
    connector.script_session_response("session-1", "ride-1")
    connector.script_response("ride-1", _ride_details("Power Zone Max", 2700, class_type_name="Power Zone Max"))

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


def test_cross_row_caching_two_rows_sharing_resolved_ride_id_cost_one_ride_details_call(db):
    """Issue #58, AC3/AC8: two different sessions (two different rows'
    peloton_ids) resolving to the SAME ride_id must cost two session calls
    but only one ride-details call, across the whole run — not just within
    one row's processing."""
    _seed_row(db, "w1", {"id": "w1", "workout_type": "class", "peloton_id": "session-1"})
    _seed_row(db, "w2", {"id": "w2", "workout_type": "class", "peloton_id": "session-2"})
    connector = FakeConnector()
    connector.script_session_response("session-1", "ride-1")
    connector.script_session_response("session-2", "ride-1")  # same class, different attendance
    connector.script_response("ride-1", _ride_details("Power Zone Max", 2700, class_type_name="Power Zone Max"))

    counts = script.run_backfill(db, connector, sleep_fn=lambda _d: None)

    assert counts["success"] == 2
    assert connector.session_calls == ["session-1", "session-2"]
    assert connector.calls == ["ride-1"]  # exactly one ride-details call for both rows
