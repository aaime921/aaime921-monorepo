"""
Tests for Feature 0.6 — Synchronization Engine.

DoD being verified directly (Tier A roadmap, Feature 0.6):
"Killing the process mid-sync and restarting resumes from the last
checkpoint with no duplicate records and no lost progress."

Plus Feature 0.5's Graceful Degradation DoD, exercised here at the
orchestration level rather than the state-machine-unit level.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from trainiq.connectors.base import (
    AcquisitionStrategy,
    CapabilityTier,
    Connector,
    ConnectorState,
    RecordKind,
)
from trainiq.storage.schema import open_db
from trainiq.sync.engine import (
    AuthenticationError,
    SynchronizationEngine,
    TransientError,
)


class MockHealthyConnector(Connector):
    """Always succeeds. `records` simulates the provider's full history;
    each call to download() only returns records after `since`, mirroring
    the real Strava/Peloton/Eufy `after`-timestamp pattern (ADR-006)."""

    capability_tier = CapabilityTier.TIER_1_OFFICIAL

    def __init__(self, provider: str, records: list[dict]):
        super().__init__(provider)
        self._records = records

    def authenticate(self) -> bool:
        return True

    def download(self, since: str | None = None) -> list[dict]:
        if since is None:
            return list(self._records)
        return [r for r in self._records if r["start_time"] > since]

    def normalize(self, raw: dict) -> dict:
        # duration_s defaulted here (not in _records()) since these mocks
        # exist to test Sync Engine mechanics, not Normalization Engine
        # behavior -- a fixed placeholder duration satisfies
        # normalized_activities NOT NULL constraint (Epic 6, slice 5).
        return {"external_id": raw["external_id"], "start_time": raw["start_time"], "duration_s": raw.get("duration_s", 1800)}


class MockFailingAuthConnector(Connector):
    capability_tier = CapabilityTier.TIER_2_UNOFFICIAL

    def authenticate(self) -> bool:
        return False

    def download(self, since: str | None = None) -> list[dict]:
        raise AssertionError("download() should never be called after a failed authenticate()")

    def normalize(self, raw: dict) -> dict:
        return raw


class MockFlakyThenHealthyConnector(Connector):
    """Fails with a TransientError the first N calls, then succeeds —
    exercises the exponential-backoff retry path."""

    capability_tier = CapabilityTier.TIER_1_OFFICIAL

    def __init__(self, provider: str, records: list[dict], fail_times: int):
        super().__init__(provider)
        self._records = records
        self._fail_times = fail_times
        self._calls = 0

    def authenticate(self) -> bool:
        return True

    def download(self, since: str | None = None) -> list[dict]:
        self._calls += 1
        if self._calls <= self._fail_times:
            raise TransientError(f"simulated transient failure #{self._calls}")
        return list(self._records)

    def normalize(self, raw: dict) -> dict:
        # duration_s defaulted here (not in _records()) since these mocks
        # exist to test Sync Engine mechanics, not Normalization Engine
        # behavior -- a fixed placeholder duration satisfies
        # normalized_activities NOT NULL constraint (Epic 6, slice 5).
        return {"external_id": raw["external_id"], "start_time": raw["start_time"], "duration_s": raw.get("duration_s", 1800)}


@pytest.fixture
def db(tmp_path: Path):
    conn = open_db(tmp_path / "trainiq.db")
    yield conn
    conn.close()


class MockMultiStrategyConnector(MockHealthyConnector):
    """Simulates a Tier 3 connector (e.g. Eufy) with one specific active
    strategy — used to prove Cloud and BLE get independent checkpoints
    rather than silently sharing the provider-level default (Hardening
    Finding 4)."""

    capability_tier = CapabilityTier.TIER_3_MULTI_STRATEGY

    def __init__(self, provider: str, records: list[dict], strategy: AcquisitionStrategy):
        super().__init__(provider, records)
        self._strategy = strategy

    def list_acquisition_strategies(self) -> list[AcquisitionStrategy]:
        return [self._strategy]


class MockRateLimitedConnector(MockHealthyConnector):
    """Fails with a TransientError carrying a provider-directed retry_after_s
    hint (ADR-037) the first N calls, then succeeds — distinct from
    MockFlakyThenHealthyConnector, which never supplies a hint."""

    def __init__(self, provider: str, records: list[dict], fail_times: int, retry_after_s: float):
        super().__init__(provider, records)
        self._fail_times = fail_times
        self._retry_after_s = retry_after_s
        self._calls = 0

    def download(self, since: str | None = None) -> list[dict]:
        self._calls += 1
        if self._calls <= self._fail_times:
            raise TransientError(
                f"simulated provider-directed rate limit #{self._calls}",
                retry_after_s=self._retry_after_s,
            )
        return super().download(since=since)


class MockExoticShapeConnector(Connector):
    """Deliberately uses neither `start_time` nor `timestamp` — a made-up
    field name (`sequence_marker`) with its own resume-cursor override.
    Proves the Sync Engine works purely through the connector-owned
    `extract_resume_cursor()` indirection, with zero remaining knowledge of
    any specific field name (the exact verification the Chief Architect
    asked for after the ADR-013 refinement)."""

    capability_tier = CapabilityTier.TIER_1_OFFICIAL

    def __init__(self, provider: str, records: list[dict]):
        super().__init__(provider)
        self._records = records

    def authenticate(self) -> bool:
        return True

    def download(self, since: str | None = None) -> list[dict]:
        if since is None:
            return list(self._records)
        return [r for r in self._records if r["sequence_marker"] > since]

    def normalize(self, raw: dict) -> dict:
        # start_time/duration_s added for Normalization Engine's sake
        # (Epic 6, slice 5) -- sequence_marker remains the field this
        # connector actually tests extract_resume_cursor() against.
        return {
            "external_id": raw["external_id"],
            "sequence_marker": raw["sequence_marker"],
            "start_time": raw.get("start_time", "2026-01-01T00:00:00+00:00"),
            "duration_s": 1800,
        }

    def extract_resume_cursor(self, normalized: dict) -> str | None:
        return normalized.get("sequence_marker")


class MockControllableAuthConnector(Connector):
    """Auth outcome is toggled test-by-test via `.should_succeed` — used
    for ADR-038's Quality Gate tests, where the scenario requires precise
    control over exactly when a connector starts/stops succeeding."""

    capability_tier = CapabilityTier.TIER_1_OFFICIAL

    def __init__(self, provider: str):
        super().__init__(provider)
        self.should_succeed = False
        self.auth_call_count = 0

    def authenticate(self) -> bool:
        self.auth_call_count += 1
        return self.should_succeed

    def download(self, since: str | None = None) -> list[dict]:
        return []

    def normalize(self, raw: dict) -> dict:
        return raw


def _records(n: int, prefix: str = "act"):
    return [
        {"external_id": f"{prefix}-{i}", "start_time": f"2026-01-{i + 1:02d}T07:00:00+00:00"}
        for i in range(n)
    ]


def test_full_sync_upserts_all_records_and_sets_checkpoint(db):
    engine = SynchronizationEngine(db)
    connector = MockHealthyConnector("strava", _records(5))

    result = engine.run_once([connector])

    assert result.connector_results[0].records_upserted == 5
    assert result.connector_results[0].state == ConnectorState.HEALTHY
    rows = db.execute("SELECT COUNT(*) AS c FROM raw_activities").fetchone()
    assert rows["c"] == 5
    checkpoint = engine.get_checkpoint("strava")
    assert checkpoint == "2026-01-05T07:00:00+00:00"


def test_resume_after_mid_sync_kill_no_duplicates_no_lost_progress(db):
    """This is the literal Tier A DoD for Feature 0.6."""
    all_records = _records(10)

    # First "process": only sees the first 4 records (simulating a crash
    # partway through a provider's paginated history).
    engine_1 = SynchronizationEngine(db)
    connector_1 = MockHealthyConnector("strava", all_records[:4])
    engine_1.run_once([connector_1])
    assert db.execute("SELECT COUNT(*) AS c FROM raw_activities").fetchone()["c"] == 4

    # "Restart": a fresh engine instance (new process), same on-disk DB and
    # checkpoint, connector now has the FULL history available again.
    engine_2 = SynchronizationEngine(db)
    connector_2 = MockHealthyConnector("strava", all_records)
    result = engine_2.run_once([connector_2])

    # Only the 6 genuinely new records (after the checkpoint) get fetched...
    assert result.connector_results[0].records_upserted == 6
    # ...and the table has exactly 10 rows total: no duplicates, nothing lost.
    rows = db.execute("SELECT external_id FROM raw_activities ORDER BY external_id").fetchall()
    assert len(rows) == 10
    assert [r["external_id"] for r in rows] == [f"act-{i}" for i in range(10)]


def test_authentication_failure_transitions_to_degraded_not_retried(db):
    engine = SynchronizationEngine(db)
    connector = MockFailingAuthConnector("peloton")

    result = engine.run_once([connector])

    assert result.connector_results[0].state == ConnectorState.DEGRADED
    assert result.connector_results[0].records_upserted == 0
    state_row = db.execute(
        "SELECT state FROM connector_state WHERE provider = 'peloton'"
    ).fetchone()
    assert state_row["state"] == "Degraded"


def test_graceful_degradation_one_failing_connector_does_not_block_a_healthy_one(db):
    """Direct DoD for Feature 0.5: a mock connector that deliberately fails
    authentication does not block a second, healthy mock connector in the
    same run."""
    engine = SynchronizationEngine(db)
    failing = MockFailingAuthConnector("peloton")
    healthy = MockHealthyConnector("strava", _records(3))

    result = engine.run_once([failing, healthy])

    by_provider = {r.provider: r for r in result.connector_results}
    assert by_provider["peloton"].state == ConnectorState.DEGRADED
    assert by_provider["strava"].state == ConnectorState.HEALTHY
    assert by_provider["strava"].records_upserted == 3


class MockUnexpectedExceptionConnector(Connector):
    """Raises a bare RuntimeError from authenticate() — reproduces exactly
    the real defect found while wiring the composition root (RC1-HF-001):
    StravaConnector raises a bare RuntimeError, not AuthenticationError or
    TransientError, when STRAVA_CLIENT_ID/SECRET aren't set. This mock
    generalizes that reproduction rather than depending on real Strava
    code, since the fix is deliberately generic (any connector, any
    unexpected exception type), not Strava-specific."""

    capability_tier = CapabilityTier.TIER_1_OFFICIAL

    def authenticate(self) -> bool:
        raise RuntimeError("Simulated unexpected configuration error — not an auth or transient failure")

    def download(self, since: str | None = None) -> list[dict]:
        return []

    def normalize(self, raw: dict) -> dict:
        return raw


def test_rc1_hf_001_unexpected_exception_does_not_crash_the_batch(db):
    """Direct regression test for the defect found during composition-root
    wiring: an unexpected exception type from one connector must not
    prevent other connectors in the same run_once() call from executing —
    this is Graceful Degradation (ADR-009) extended to cover exception
    types the Sync Engine wasn't originally written to expect, not just
    the two it was."""
    engine = SynchronizationEngine(db)
    broken = MockUnexpectedExceptionConnector("broken_unexpected")
    healthy = MockHealthyConnector("healthy", _records(2))

    result = engine.run_once([broken, healthy])  # must NOT raise

    by_provider = {r.provider: r for r in result.connector_results}
    assert by_provider["healthy"].records_upserted == 2  # completely unaffected
    assert by_provider["broken_unexpected"].state == ConnectorState.DEGRADED
    assert "RuntimeError" in by_provider["broken_unexpected"].error

    state_row = db.execute(
        "SELECT state, detail FROM connector_state WHERE provider = 'broken_unexpected'"
    ).fetchone()
    assert state_row["state"] == "Degraded"
    assert "RuntimeError" in state_row["detail"]


def test_rc1_hf_004_diagnostic_log_captures_full_traceback_not_just_message(db, tmp_path):
    """RC1-HF-004: an unexpected exception's diagnostic log entry must
    include the FULL traceback (file, line, call stack), not just the
    exception type and message — added specifically so a future "is the
    fix actually running" question can be settled by reading the log
    directly, rather than debated. Uses a real logger writing to a real
    file, not a mock, so this proves what actually ends up on disk."""
    import sys
    from loguru import logger

    log_path = tmp_path / "diagnostic.log"
    handler_id = logger.add(log_path, format="{message}")
    try:
        engine = SynchronizationEngine(db)
        broken = MockUnexpectedExceptionConnector("broken_unexpected_tb")
        engine.run_once([broken])
    finally:
        logger.remove(handler_id)

    log_contents = log_path.read_text()
    assert "Traceback (most recent call last)" in log_contents
    assert "RuntimeError" in log_contents
    # The traceback must point INTO the actual source file where the
    # exception was raised — proving this isn't just a re-printed message.
    assert "eufy.py" in log_contents or "test_sync_engine.py" in log_contents


def test_rc1_hf_001_unexpected_exception_respects_recovery_required_legality(db):
    """The same illegal-transition protection already established for
    AuthenticationError (BL-007/ADR-038) must also apply to the new
    catch-all boundary — a connector already in RecoveryRequired must
    stay there on an unexpected exception, never attempt an illegal
    transition to Degraded."""
    db.execute(
        """
        INSERT INTO connector_state
            (provider, state, updated_at, state_entered_at, last_attempt_at, attempt_count_in_state)
        VALUES ('broken_unexpected', 'RecoveryRequired', '2026-01-01T00:00:00+00:00',
                '2026-01-01T00:00:00+00:00', '2026-01-01T00:00:00+00:00', 1)
        """
    )
    db.commit()
    engine = SynchronizationEngine(db)
    connector = MockUnexpectedExceptionConnector("broken_unexpected")

    engine.run_once([connector])  # must NOT raise InvalidStateTransition

    state = db.execute(
        "SELECT state FROM connector_state WHERE provider = 'broken_unexpected'"
    ).fetchone()["state"]
    assert state == "RecoveryRequired"


def test_degraded_connector_is_skipped_on_subsequent_runs(db):
    """Once Degraded, a connector should not be re-attempted every single
    run — it's surfaced as skipped until something (future Recovery flow)
    moves it back toward Healthy."""
    engine = SynchronizationEngine(db)
    connector = MockFailingAuthConnector("peloton")
    engine.run_once([connector])
    assert connector.get_state() == ConnectorState.DEGRADED

    result = engine.run_once([connector])
    assert result.connector_results[0].skipped_reason is not None
    assert "Degraded" in result.connector_results[0].skipped_reason


def test_transient_error_retries_with_backoff_then_succeeds(db):
    sleeps: list[float] = []
    engine = SynchronizationEngine(db, max_retries=3, backoff_base_s=0.01, sleep_fn=sleeps.append)
    connector = MockFlakyThenHealthyConnector("eufy", _records(2, prefix="w"), fail_times=2)

    result = engine.run_once([connector])

    assert result.connector_results[0].state == ConnectorState.HEALTHY
    assert result.connector_results[0].records_upserted == 2
    assert len(sleeps) == 2  # two retries before success
    assert sleeps == [0.01, 0.02]  # exponential backoff


def test_transient_error_exhausting_retries_transitions_to_warning_not_degraded(db):
    """A flaky-but-not-broken connector should land in Warning, distinct
    from an auth failure landing in Degraded — different failure classes,
    different signal to the rest of the system (ADR-010)."""
    engine = SynchronizationEngine(db, max_retries=2, backoff_base_s=0.01, sleep_fn=lambda s: None)
    connector = MockFlakyThenHealthyConnector("eufy", _records(2), fail_times=10)

    result = engine.run_once([connector])

    assert result.connector_results[0].state == ConnectorState.WARNING
    assert result.connector_results[0].records_upserted == 0


def test_multi_strategy_connector_checkpoints_are_isolated_per_strategy(db):
    """Direct regression test for Hardening Finding 4: Eufy-style Cloud and
    BLE strategies for the SAME provider must not share one checkpoint."""
    engine = SynchronizationEngine(db)

    cloud_connector = MockMultiStrategyConnector(
        "eufy", _records(4, prefix="cloud"), AcquisitionStrategy.CLOUD
    )
    engine.run_once([cloud_connector])

    ble_connector = MockMultiStrategyConnector(
        "eufy", _records(2, prefix="ble"), AcquisitionStrategy.BLE
    )
    engine.run_once([ble_connector])

    cloud_checkpoint = engine.get_checkpoint("eufy", strategy="cloud")
    ble_checkpoint = engine.get_checkpoint("eufy", strategy="ble")

    assert cloud_checkpoint is not None
    assert ble_checkpoint is not None
    assert cloud_checkpoint != ble_checkpoint  # would collide under the old "default"-only wiring

    # Running Cloud again must only fetch what's new for Cloud specifically —
    # BLE's checkpoint must not have leaked into Cloud's `since` value.
    cloud_connector_2 = MockMultiStrategyConnector(
        "eufy", _records(4, prefix="cloud"), AcquisitionStrategy.CLOUD
    )
    result = engine.run_once([cloud_connector_2])
    assert result.connector_results[0].records_upserted == 0  # nothing new since Cloud's own checkpoint


def test_single_strategy_connector_still_uses_default_checkpoint(db):
    """Backward-compatibility check: Tier 1/2 connectors with no declared
    strategy (Strava, Peloton) must behave exactly as before this fix."""
    engine = SynchronizationEngine(db)
    connector = MockHealthyConnector("strava", _records(3))
    engine.run_once([connector])

    assert engine.get_checkpoint("strava", strategy="default") is not None
    assert connector.list_acquisition_strategies() == []
    assert connector.active_strategy() is None


def test_provider_directed_retry_honors_retry_after_s_not_generic_backoff(db):
    """ADR-037's core behavior: when TransientError carries retry_after_s,
    the Sync Engine must use THAT value, not its own exponential formula."""
    sleeps: list[float] = []
    engine = SynchronizationEngine(db, max_retries=2, backoff_base_s=1.0, sleep_fn=sleeps.append)
    connector = MockRateLimitedConnector(
        "strava", _records(2), fail_times=1, retry_after_s=900.0
    )

    result = engine.run_once([connector])

    assert result.connector_results[0].state == ConnectorState.HEALTHY
    assert result.connector_results[0].records_upserted == 2
    assert sleeps == [900.0]  # NOT 1.0 (what the generic backoff formula would have used)


def test_provider_directed_retry_still_respects_max_retries(db):
    """A provider-directed wait time changes HOW LONG each retry waits, not
    HOW MANY retries are attempted — max_retries still bounds the total."""
    sleeps: list[float] = []
    engine = SynchronizationEngine(db, max_retries=2, backoff_base_s=1.0, sleep_fn=sleeps.append)
    connector = MockRateLimitedConnector(
        "strava", _records(2), fail_times=10, retry_after_s=900.0
    )

    result = engine.run_once([connector])

    assert result.connector_results[0].state == ConnectorState.WARNING
    assert len(sleeps) == 2  # bounded by max_retries, exactly as before ADR-037
    assert sleeps == [900.0, 900.0]


def test_transient_error_without_retry_after_s_still_uses_generic_backoff(db):
    """Backward compatibility: a TransientError with no hint (the pre-ADR-037
    default) must behave exactly as it did before this change."""
    sleeps: list[float] = []
    engine = SynchronizationEngine(db, max_retries=3, backoff_base_s=0.01, sleep_fn=sleeps.append)
    connector = MockFlakyThenHealthyConnector("eufy", _records(1), fail_times=2)

    engine.run_once([connector])

    assert sleeps == [0.01, 0.02]  # unchanged exponential formula


def test_sync_engine_has_no_knowledge_of_any_specific_cursor_field_name(db):
    """The definitive verification requested after the ADR-013 refinement:
    a connector using a field name that is neither `start_time` nor
    `timestamp` (nor anything else the Sync Engine has ever heard of) must
    still checkpoint and resume correctly, proving the engine works purely
    through extract_resume_cursor() with zero remaining field-name
    knowledge of its own."""
    engine = SynchronizationEngine(db)
    all_records = [
        {"external_id": f"x-{i}", "sequence_marker": f"seq-{i:04d}"} for i in range(6)
    ]

    connector_1 = MockExoticShapeConnector("exotic", all_records[:3])
    result_1 = engine.run_once([connector_1])
    assert result_1.connector_results[0].records_upserted == 3
    assert engine.get_checkpoint("exotic") == "seq-0002"

    # Resume: a fresh connector instance sees the full history again, but
    # only the genuinely-new records (after the exotic cursor) get fetched.
    connector_2 = MockExoticShapeConnector("exotic", all_records)
    result_2 = engine.run_once([connector_2])
    assert result_2.connector_results[0].records_upserted == 3
    rows = db.execute(
        "SELECT external_id FROM raw_activities WHERE provider = 'exotic' ORDER BY external_id"
    ).fetchall()
    assert len(rows) == 6


# --- ADR-038 Quality Gates (§6) — verified generically, at the Sync Engine
# level, independent of any specific connector (Strava/Peloton/Eufy all
# inherit this behavior identically per the single-uniform-policy decision).

def test_adr038_gate_2_does_not_escalate_one_day_short_of_threshold(db):
    """Never escalates early — tested at the Sync Engine integration
    level, not just the pure policy function, and specifically one backoff
    cycle short of the 10-day threshold."""
    clock = {"now": datetime(2026, 1, 1, tzinfo=timezone.utc)}
    engine = SynchronizationEngine(db, now_fn=lambda: clock["now"])
    connector = MockControllableAuthConnector("gate2")
    connector.should_succeed = False

    for day in (0, 1, 3, 7, 9):  # 9 days: one cycle short of the 10-day threshold
        clock["now"] = datetime(2026, 1, 1, tzinfo=timezone.utc) + timedelta(days=day)
        engine.run_once([connector])

    state = db.execute("SELECT state FROM connector_state WHERE provider = 'gate2'").fetchone()["state"]
    assert state == "Degraded"  # NOT RecoveryRequired yet


def test_adr038_gate_3_recovers_and_resets_counters_before_escalation(db):
    """Recovers correctly if a re-attempt succeeds before escalation —
    counters must reset, not continue accumulating toward escalation using
    stale state from the prior degradation."""
    clock = {"now": datetime(2026, 1, 1, tzinfo=timezone.utc)}
    engine = SynchronizationEngine(db, now_fn=lambda: clock["now"])
    connector = MockControllableAuthConnector("gate3")
    connector.should_succeed = False

    clock["now"] = datetime(2026, 1, 1, tzinfo=timezone.utc)
    engine.run_once([connector])  # enters Degraded
    clock["now"] = datetime(2026, 1, 2, tzinfo=timezone.utc)
    engine.run_once([connector])  # still Degraded, 2nd attempt

    # Recovery: the connector starts succeeding again, well before the
    # 10-day escalation threshold.
    connector.should_succeed = True
    clock["now"] = datetime(2026, 1, 4, tzinfo=timezone.utc)
    engine.run_once([connector])

    row = db.execute(
        "SELECT state, attempt_count_in_state FROM connector_state WHERE provider = 'gate3'"
    ).fetchone()
    assert row["state"] == "Healthy"
    assert row["attempt_count_in_state"] == 1  # reset, not accumulated to 3

    # Prove the reset actually took effect functionally, not just in the
    # stored number: if it degrades again, backoff must restart at the
    # 1-day cadence, not continue as if 3 prior attempts had happened.
    connector.should_succeed = False
    clock["now"] = datetime(2026, 1, 5, tzinfo=timezone.utc)
    engine.run_once([connector])  # re-enters Degraded, attempt 1 of the new degradation

    clock["now"] = datetime(2026, 1, 5, 12, 0, 0, tzinfo=timezone.utc)  # only 12h later
    result = engine.run_once([connector])
    assert result.connector_results[0].skipped_reason is not None  # too soon — 1-day cadence, not yet due

    clock["now"] = datetime(2026, 1, 6, tzinfo=timezone.utc)  # exactly 1 day later
    result = engine.run_once([connector])
    assert result.connector_results[0].skipped_reason is None  # due right on schedule, proving the reset


def test_adr038_gate_4_lifecycle_persists_across_a_simulated_application_restart(db):
    """Persists correctly across an application restart — mirrors exactly
    how test_resume_after_mid_sync_kill_no_duplicates_no_lost_progress
    already proves resume-cursor persistence: a fresh SynchronizationEngine
    AND a fresh Connector instance (simulating a real app restart, where
    nothing in-memory survives) against the same on-disk database must
    compute lifecycle eligibility correctly from persisted state alone."""
    clock = {"now": datetime(2026, 1, 1, tzinfo=timezone.utc)}

    engine_1 = SynchronizationEngine(db, now_fn=lambda: clock["now"])
    connector_1 = MockControllableAuthConnector("gate4")
    connector_1.should_succeed = False
    engine_1.run_once([connector_1])  # enters Degraded on day 0

    # "Restart": brand new engine AND brand new connector instance — the
    # old connector_1's in-memory ProviderStateMachine (which defaulted to
    # Healthy at construction) is discarded entirely, exactly as it would
    # be if the process actually exited and relaunched.
    clock["now"] = datetime(2026, 1, 1, 6, 0, 0, tzinfo=timezone.utc)  # 6h later, same day
    engine_2 = SynchronizationEngine(db, now_fn=lambda: clock["now"])
    connector_2 = MockControllableAuthConnector("gate4")
    connector_2.should_succeed = False
    assert connector_2.get_state() == ConnectorState.HEALTHY  # fresh instance defaults to Healthy in memory

    result = engine_2.run_once([connector_2])
    # The persisted Degraded state — and the fact only 6h have passed, not
    # yet the 1-day backoff — must be correctly picked up from the
    # database, not lost to the fresh in-memory default.
    assert connector_2.get_state() == ConnectorState.DEGRADED
    assert result.connector_results[0].skipped_reason is not None
    assert connector_2.auth_call_count == 0  # correctly skipped, not re-attempted early

    # One full day after the ORIGINAL entry (not the restart), a third,
    # independent instance correctly finds it eligible again.
    clock["now"] = datetime(2026, 1, 2, tzinfo=timezone.utc)
    engine_3 = SynchronizationEngine(db, now_fn=lambda: clock["now"])
    connector_3 = MockControllableAuthConnector("gate4")
    connector_3.should_succeed = True
    engine_3.run_once([connector_3])
    assert connector_3.auth_call_count == 1
    final_state = db.execute("SELECT state FROM connector_state WHERE provider = 'gate4'").fetchone()["state"]
    assert final_state == "Healthy"


def test_adr038_restart_transparency_connector_state_evolves_identically(tmp_path):
    """Restart Transparency: a restart must not change the state machine's
    behavior at all — not just the final state name, but every persisted
    field's exact evolution. This is a stronger property than persistence
    (already covered by Gate 4): Gate 4 proves state survives a restart.
    This proves a restart is INVISIBLE to the outcome — the same event
    sequence produces byte-for-byte identical connector_state, whether run
    in one continuous process or split across a simulated restart partway
    through.

    Two independent databases run the exact same scripted event sequence
    (fail, fail, fail, fail-at-backoff-boundary, then recover) — Run A in
    one continuous engine/connector instance, Run B with a fresh
    engine+connector substituted partway through, mirroring a real app
    restart. Every column of connector_state must match exactly at the end."""
    from trainiq.storage.schema import open_db

    # Identical scripted sequence for both runs: (day_offset, should_succeed).
    # Each day_offset is chosen to land exactly on the eligible boundary the
    # backoff schedule actually produces (1, 2, 4-day gaps) — verified
    # against lifecycle_policy's own schedule, not guessed.
    events = [
        (0, False),   # enters Degraded, next eligible = day 1 (schedule[0]=1)
        (1, False),   # 2nd attempt, exactly at the 1-day boundary; next eligible = day 3 (schedule[1]=2)
        (3, False),   # 3rd attempt, exactly at the 2-day boundary; next eligible = day 7 (schedule[2]=4)
        (7, True),    # 4th attempt, exactly at the 4-day boundary — recovers
    ]
    anchor = datetime(2026, 1, 1, tzinfo=timezone.utc)

    def run_sequence(db_conn, restart_after_index: int | None):
        """restart_after_index=None means no restart at all — one
        continuous engine+connector instance for the whole sequence.
        Otherwise, a fresh engine+connector is substituted immediately
        after that event index, simulating an app restart."""
        engine = SynchronizationEngine(db_conn, now_fn=lambda: clock["now"])
        connector = MockControllableAuthConnector("restart_transparency")
        for i, (day_offset, should_succeed) in enumerate(events):
            clock["now"] = anchor + timedelta(days=day_offset)
            connector.should_succeed = should_succeed
            engine.run_once([connector])
            if restart_after_index is not None and i == restart_after_index:
                # Simulate a full app restart: brand new engine AND brand
                # new connector instance, discarding all in-memory state.
                engine = SynchronizationEngine(db_conn, now_fn=lambda: clock["now"])
                connector = MockControllableAuthConnector("restart_transparency")

    clock = {"now": anchor}
    db_a = open_db(tmp_path / "run_a.db")
    run_sequence(db_a, restart_after_index=None)  # no restart

    clock = {"now": anchor}
    db_b = open_db(tmp_path / "run_b.db")
    run_sequence(db_b, restart_after_index=2)  # restart after the 3rd event (index 2)

    row_a = dict(db_a.execute(
        "SELECT state, state_entered_at, last_attempt_at, attempt_count_in_state, next_eligible_retry_at "
        "FROM connector_state WHERE provider = 'restart_transparency'"
    ).fetchone())
    row_b = dict(db_b.execute(
        "SELECT state, state_entered_at, last_attempt_at, attempt_count_in_state, next_eligible_retry_at "
        "FROM connector_state WHERE provider = 'restart_transparency'"
    ).fetchone())

    assert row_a == row_b, f"Restart changed the outcome:\n  no-restart: {row_a}\n  restarted:  {row_b}"
    # Sanity: confirm this actually exercised a non-trivial final state,
    # not two runs that both trivially did nothing.
    assert row_a["state"] == "Healthy"
    assert row_a["attempt_count_in_state"] == 1  # reset on recovery

    db_a.close()
    db_b.close()


def test_adr038_recovery_required_repeated_failure_does_not_raise_illegal_transition(db):
    """Regression test for a latent bug this implementation surfaced and
    fixed: RecoveryRequired's only legal outgoing transition is to Healthy
    (ADR-010's _ALLOWED_TRANSITIONS). Before ADR-038, RecoveryRequired
    connectors were never re-attempted at all, so a second consecutive
    failure while already RecoveryRequired was a code path that had never
    actually executed. Now that RecoveryRequired connectors ARE
    periodically re-attempted (every run, by default policy), a naive
    "always transition to Degraded on AuthenticationError" would attempt
    the illegal RecoveryRequired -> Degraded transition and crash. This
    test proves it doesn't — set up directly via persisted state (the
    realistic scenario: a previous process/run already escalated this
    connector, and now a fresh restore-from-DB brings it back as
    RecoveryRequired) rather than an in-memory-only shortcut, since
    restore_state() correctly treats the database as the source of truth
    and would otherwise overwrite an in-memory-only test setup."""
    db.execute(
        """
        INSERT INTO connector_state
            (provider, state, updated_at, state_entered_at, last_attempt_at, attempt_count_in_state)
        VALUES ('gate_recovery', 'RecoveryRequired', '2026-01-01T00:00:00+00:00',
                '2026-01-01T00:00:00+00:00', '2026-01-01T00:00:00+00:00', 1)
        """
    )
    db.commit()

    engine = SynchronizationEngine(db)
    connector = MockControllableAuthConnector("gate_recovery")
    connector.should_succeed = False  # still no manual token supplied

    result = engine.run_once([connector])  # must NOT raise InvalidStateTransition

    assert connector.auth_call_count == 1  # RecoveryRequired's default cadence: every run
    state = db.execute(
        "SELECT state FROM connector_state WHERE provider = 'gate_recovery'"
    ).fetchone()["state"]
    assert state == "RecoveryRequired"  # stayed put, no crash, no illegal transition


# --- Epic 6, slice 5: routing purely by record_kind, never by shape-sniffing ---

class MockAdversarialWeighInConnector(Connector):
    """Declares record_kind=WEIGH_IN, but its normalize() output ALSO
    happens to contain activity-shaped keys (start_time, duration_s) —
    deliberately adversarial. Proves routing is decided by the DECLARED
    record_kind alone, never by inspecting which keys are present, the
    same anti-shape-sniffing property BL-005 already established for
    extract_resume_cursor()."""

    capability_tier = CapabilityTier.TIER_3_MULTI_STRATEGY
    record_kind = RecordKind.WEIGH_IN

    def __init__(self, provider: str):
        super().__init__(provider)

    def authenticate(self) -> bool:
        return True

    def download(self, since: str | None = None) -> list[dict]:
        return [{"id": "adv-1", "time": "2026-01-05T06:30:00+00:00", "weight": 80.0}]

    def normalize(self, raw: dict) -> dict:
        return {
            "external_id": raw["id"],
            "timestamp": raw["time"],
            "weight_kg": raw["weight"],
            # Adversarial: these look like activity fields, but record_kind
            # says WEIGH_IN, so they must be ignored for routing purposes.
            "start_time": "2026-01-05T06:30:00+00:00",
            "duration_s": 1800,
        }


def test_routing_uses_record_kind_not_shape_sniffing(db):
    engine = SynchronizationEngine(db)
    connector = MockAdversarialWeighInConnector("adversarial")

    engine.run_once([connector])

    weigh_in_row = db.execute(
        "SELECT * FROM weigh_ins WHERE provider = 'adversarial'"
    ).fetchone()
    assert weigh_in_row is not None
    assert weigh_in_row["weight_kg"] == 80.0

    activity_row = db.execute(
        "SELECT * FROM normalized_activities WHERE provider = 'adversarial'"
    ).fetchone()
    assert activity_row is None  # must NOT have been routed here despite start_time/duration_s being present


# --- Epic 6 self-review finding: a malformed record must not crash the batch ---

class MockMalformedRecordConnector(Connector):
    """Its normalize() output is missing required ACTIVITY fields
    (start_time, duration_s) — reproduces the exact self-review finding:
    before the fix, this raised an uncaught KeyError from
    build_canonical_record() that propagated through run_once()'s list
    comprehension and crashed every OTHER connector in the same batch too,
    not just this one."""

    capability_tier = CapabilityTier.TIER_1_OFFICIAL

    def authenticate(self) -> bool:
        return True

    def download(self, since: str | None = None) -> list[dict]:
        return [{"whatever": "missing required fields"}]

    def normalize(self, raw: dict) -> dict:
        return {"external_id": "1"}  # missing start_time, duration_s


def test_malformed_record_does_not_crash_the_run_or_other_connectors(db):
    """Direct regression test for the self-review finding. Must NOT raise,
    and the healthy connector in the same batch must be completely
    unaffected — this is Graceful Degradation (ADR-009) applied at the
    per-record level, not just the per-connector level it was already
    proven at."""
    broken = MockMalformedRecordConnector("broken")
    healthy = MockHealthyConnector("healthy", _records(2))

    result = engine_run_once_without_raising(db, [broken, healthy])

    by_provider = {r.provider: r for r in result.connector_results}
    assert by_provider["broken"].state == ConnectorState.HEALTHY  # the connector itself is fine
    assert by_provider["healthy"].records_upserted == 2  # completely unaffected by broken's bad record

    # Raw payload preserved for later reprocessing, per Milestone 4 §5 —
    # only the canonical record is skipped, never the raw evidence.
    raw_row = db.execute("SELECT external_id FROM raw_activities WHERE provider = 'broken'").fetchone()
    assert raw_row is not None
    canonical_row = db.execute("SELECT * FROM normalized_activities WHERE provider = 'broken'").fetchone()
    assert canonical_row is None  # correctly skipped, not fabricated


def engine_run_once_without_raising(db, connectors):
    engine = SynchronizationEngine(db)
    return engine.run_once(connectors)  # will raise the test itself if this regresses


# --- Sync summary UX improvement: real (not estimated) insert/update counts ---

def test_first_sync_reports_all_records_as_inserted_none_updated(db):
    """A genuinely new record must be counted as inserted, never updated —
    the common case, verified explicitly rather than assumed."""
    engine = SynchronizationEngine(db)
    connector = MockHealthyConnector("strava", _records(3))

    result = engine.run_once([connector])

    r = result.connector_results[0]
    assert r.records_inserted == 3
    assert r.records_updated == 0
    assert r.records_upserted == 3  # aggregate field unchanged in meaning


class MockAlwaysReturnsAllConnector(Connector):
    """Ignores `since` entirely — used specifically to test the
    insert/update distinction, since a normal incremental connector
    (MockHealthyConnector) correctly filters out already-synced records
    via the resume cursor, which would hide the update path this test
    needs to exercise."""

    capability_tier = CapabilityTier.TIER_1_OFFICIAL

    def __init__(self, provider: str, records: list[dict]):
        super().__init__(provider)
        self._records = records

    def authenticate(self) -> bool:
        return True

    def download(self, since: str | None = None) -> list[dict]:
        return list(self._records)  # always all records, regardless of since

    def normalize(self, raw: dict) -> dict:
        return {"external_id": raw["external_id"], "start_time": raw["start_time"], "duration_s": 1800}


def test_second_sync_of_same_records_reports_updated_not_inserted(db):
    """Re-syncing the exact same external_ids a second time must report
    them as updated, not inserted — this is the real, non-estimated
    distinction this improvement exists to provide. Uses the REAL
    INSERT OR IGNORE + conditional UPDATE mechanism, not a mock."""
    engine = SynchronizationEngine(db)
    records = _records(3)
    connector_1 = MockAlwaysReturnsAllConnector("strava", records)
    engine.run_once([connector_1])

    connector_2 = MockAlwaysReturnsAllConnector("strava", records)  # same external_ids
    result = engine.run_once([connector_2])

    r = result.connector_results[0]
    assert r.records_inserted == 0
    assert r.records_updated == 3
    assert r.records_upserted == 3


def test_mixed_batch_correctly_splits_inserted_and_updated(db):
    """Half new, half already-existing records in the SAME sync run must
    be split correctly — not all counted as one or the other."""
    engine = SynchronizationEngine(db)
    first_batch = _records(2)
    connector_1 = MockAlwaysReturnsAllConnector("strava", first_batch)
    engine.run_once([connector_1])

    # Second run: the original 2 records again, plus 2 genuinely new ones.
    second_batch = first_batch + _records(2, prefix="act_new")
    connector_2 = MockAlwaysReturnsAllConnector("strava", second_batch)
    result = engine.run_once([connector_2])

    r = result.connector_results[0]
    assert r.records_inserted == 2
    assert r.records_updated == 2
    assert r.records_upserted == 4


def test_eufy_weigh_in_upsert_also_reports_real_insert_vs_update(db, monkeypatch):
    """The RCA's own subject matter, verified directly: a WEIGH_IN-kind
    connector's insert/update counts must be tracked identically to an
    ACTIVITY-kind one — the routing (BL-005/Epic 6) doesn't bypass this."""
    import keyring
    from keyring.backends.fail import Keyring as FailKeyring
    import trainiq.connectors.eufy as eufy_module
    from trainiq.credentials.store import CredentialStore

    class _InMemoryKeyring(FailKeyring):
        priority = 1

        def __init__(self):
            self._store: dict[tuple[str, str], str] = {}

        def set_password(self, service, username, password):
            self._store[(service, username)] = password

        def get_password(self, service, username):
            return self._store.get((service, username))

        def delete_password(self, service, username):
            pass

    monkeypatch.setattr(keyring, "get_keyring", lambda: _InMemoryKeyring())
    original_keyring = keyring.get_keyring()
    keyring.set_keyring(_InMemoryKeyring())

    class _FakeSession:
        def post(self, url, json=None, headers=None):
            class R:
                status_code = 200
                def json(self):
                    return {"res_code": 1, "access_token": "tok", "refresh_token": "r"}
            return R()

        def get(self, url, headers=None, params=None):
            class R:
                status_code = 200
                def json(self):
                    return {"res_code": 1, "data": [
                        {"id": "w1", "device_id": "d1", "create_time": "2026-01-05T06:30:00+00:00",
                         "scale_data": {"weight": 75.0}},
                    ]}
            return R()

    try:
        credential_store = CredentialStore(conn=db)
        credential_store.set(eufy_module.PROVIDER, eufy_module.CRED_EMAIL, "a@b.com")
        credential_store.set(eufy_module.PROVIDER, eufy_module.CRED_PASSWORD, "pw")
        connector = eufy_module.EufyConnector(credential_store, device_id="dev-1", session=_FakeSession())
        engine = SynchronizationEngine(db)

        result_1 = engine.run_once([connector])
        assert result_1.connector_results[0].records_inserted == 1
        assert result_1.connector_results[0].records_updated == 0

        connector_2 = eufy_module.EufyConnector(credential_store, device_id="dev-1", session=_FakeSession())
        result_2 = engine.run_once([connector_2])
        assert result_2.connector_results[0].records_inserted == 0
        assert result_2.connector_results[0].records_updated == 1
    finally:
        keyring.set_keyring(original_keyring)


def test_records_skipped_counts_missing_external_id_separately_from_malformed(db):
    """A record with no external_id at all (skipped) is a different
    failure mode than a record that fails canonical normalization
    (malformed) — the summary must not conflate the two. Both the
    normalized output AND the raw record must lack external_id, since
    sync_connector() falls back to raw.get("external_id") if the
    normalized value is missing."""
    class _NoExternalIdConnector(Connector):
        capability_tier = CapabilityTier.TIER_1_OFFICIAL

        def authenticate(self) -> bool:
            return True

        def download(self, since: str | None = None) -> list[dict]:
            return [{"start_time": "2026-01-01T07:00:00+00:00"}, {"start_time": "2026-01-02T07:00:00+00:00"}]

        def normalize(self, raw: dict) -> dict:
            return {"start_time": raw["start_time"], "duration_s": 1800}

    engine = SynchronizationEngine(db)
    connector = _NoExternalIdConnector("strava")

    result = engine.run_once([connector])

    r = result.connector_results[0]
    assert r.records_skipped == 2
    assert r.records_inserted == 0
    assert r.records_malformed == 0


# --- BL-006: generic, provider-agnostic incremental-sync-unavailable flag ---

def test_default_connector_supports_incremental_sync(db):
    """Every connector defaults to True — the assumption every connector
    has operated under since ADR-006. Verified via a plain mock, no
    provider-specific behavior involved."""
    connector = MockHealthyConnector("strava", _records(1))
    assert connector.supports_incremental_sync is True


def test_connector_declaring_no_incremental_support_logs_explanatory_note(db, caplog):
    """A connector that sets supports_incremental_sync = False must
    produce a clear, non-alarming log explanation — generic wording, not
    hardcoded to any specific provider name in the Sync Engine itself."""
    import io
    from loguru import logger

    class _NoIncrementalConnector(MockHealthyConnector):
        supports_incremental_sync = False

    log_stream = io.StringIO()
    handler_id = logger.add(log_stream, format="{message}")
    try:
        engine = SynchronizationEngine(db)
        connector = _NoIncrementalConnector("some_provider", _records(2))
        engine.run_once([connector])
    finally:
        logger.remove(handler_id)

    log_contents = log_stream.getvalue()
    assert "incremental filtering unavailable" in log_contents
    assert "some_provider" in log_contents


def test_connector_supporting_incremental_sync_does_not_log_the_note(db):
    """The note must only appear for connectors that actually declare
    the limitation — not printed unconditionally for every sync."""
    import io
    from loguru import logger

    log_stream = io.StringIO()
    handler_id = logger.add(log_stream, format="{message}")
    try:
        engine = SynchronizationEngine(db)
        connector = MockHealthyConnector("strava", _records(2))
        engine.run_once([connector])
    finally:
        logger.remove(handler_id)

    assert "incremental filtering unavailable" not in log_stream.getvalue()


def test_eufy_connector_declares_no_incremental_sync_support():
    """Direct confirmation that EufyConnector actually sets the flag,
    per BL-006's live-verified finding — not just that the generic
    mechanism works in the abstract."""
    from trainiq.connectors.eufy import EufyConnector
    assert EufyConnector.supports_incremental_sync is False


def test_sync_engine_source_contains_no_hardcoded_provider_names_for_this_feature():
    """Structural guarantee, not just a description: the Sync Engine's
    BL-006 handling must never compare against a literal 'eufy' string in
    actual logic. Checked via AST string-literal inspection, not raw
    source text — an earlier version of this test flagged this file's own
    explanatory comment ("BL-006 (Eufy, currently the only connector this
    applies to)..."), the same false-positive pattern this project has
    hit before with blunt text-based containment checks. Comments aren't
    AST nodes at all, so this version naturally excludes them and checks
    only what could actually affect behavior: string literals."""
    import ast
    import inspect
    from trainiq.sync.engine import SynchronizationEngine

    source = inspect.getsource(SynchronizationEngine.sync_connector)
    tree = ast.parse(textwrap_dedent_for_method(source))

    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            assert "eufy" not in node.value.lower(), (
                f"Found a hardcoded provider-name string literal: {node.value!r}"
            )


def textwrap_dedent_for_method(source: str) -> str:
    """ast.parse() requires the method body to not be indented relative to
    module level — a plain method's source (as inspect.getsource returns
    it) is indented under its class, so this strips the common leading
    whitespace before parsing."""
    import textwrap
    return textwrap.dedent(source)


# --- ADR-039 / Issue #38: weigh-in plausibility flagging, wired through the Sync Engine ---

class MockWeighInConnector(Connector):
    """A WEIGH_IN-kind connector returning whatever fixed list of raw
    records it's constructed with, ignoring `since` entirely — mirrors
    MockAlwaysReturnsAllConnector's role for ACTIVITY-kind tests, needed
    here because Eufy's real supports_incremental_sync=False means every
    sync reprocesses full history (BL-006), the exact scenario this
    feature's order-independence guarantee (Task breakdown item 5) must
    hold under."""

    capability_tier = CapabilityTier.TIER_3_MULTI_STRATEGY
    record_kind = RecordKind.WEIGH_IN
    supports_incremental_sync = False

    def __init__(self, provider: str, records: list[dict]):
        super().__init__(provider)
        self._records = records

    def authenticate(self) -> bool:
        return True

    def download(self, since: str | None = None) -> list[dict]:
        return list(self._records)

    def normalize(self, raw: dict) -> dict:
        return {
            "external_id": raw["external_id"],
            "timestamp": raw["timestamp"],
            "weight_kg": raw.get("weight_kg"),
            "body_fat_pct": raw.get("body_fat_pct"),
        }

    def extract_resume_cursor(self, normalized: dict) -> str | None:
        value = normalized.get("timestamp")
        return str(value) if value is not None else None


def _baseline_weigh_ins(n: int, weight_kg: float = 85.0, prefix: str = "w") -> list[dict]:
    return [
        {
            "external_id": f"{prefix}{i}",
            "timestamp": f"2026-01-{i + 1:02d}T08:00:00+00:00",
            "weight_kg": weight_kg,
            "body_fat_pct": 18.0,
        }
        for i in range(n)
    ]


_OUTLIER_READING = {
    "external_id": "outlier1",
    "timestamp": "2026-01-10T08:00:00+00:00",
    "weight_kg": 20.0,
    "body_fat_pct": 5.0,
}


def test_weigh_in_sync_counts_flagged_implausible_records(db):
    """Direct confirmation of the new ConnectorSyncResult counter —
    reproducing the issue's own evidence shape (a low-weight, low-body-fat
    outlier against an established baseline)."""
    records = _baseline_weigh_ins(5) + [dict(_OUTLIER_READING)]
    connector = MockWeighInConnector("testscale", records)
    engine = SynchronizationEngine(db)

    result = engine.run_once([connector])

    r = result.connector_results[0]
    assert r.records_flagged_implausible == 1
    assert r.records_inserted == 6

    flagged_row = db.execute(
        "SELECT is_flagged_implausible, plausibility_reason FROM weigh_ins WHERE external_id = 'outlier1'"
    ).fetchone()
    assert flagged_row["is_flagged_implausible"] == 1
    assert flagged_row["plausibility_reason"] is not None

    normal_row = db.execute(
        "SELECT is_flagged_implausible FROM weigh_ins WHERE external_id = 'w0'"
    ).fetchone()
    assert normal_row["is_flagged_implausible"] == 0


def test_weigh_in_sync_summary_reports_flagged_count(db):
    """The run summary must report the flagged count, following the
    existing inserted/updated/malformed/skipped pattern (AC5)."""
    import io
    from loguru import logger

    records = _baseline_weigh_ins(5) + [dict(_OUTLIER_READING)]
    connector = MockWeighInConnector("testscale", records)

    log_stream = io.StringIO()
    handler_id = logger.add(log_stream, format="{message}")
    try:
        engine = SynchronizationEngine(db)
        engine.run_once([connector])
    finally:
        logger.remove(handler_id)

    assert "flagged 1 implausible" in log_stream.getvalue()


def test_activity_sync_summary_always_reports_flagged_zero(db):
    """ACTIVITY-kind connectors never flag anything, but the clause is
    unconditional (unlike the BL-006 note) — "flagged 0 implausible" is
    still printed."""
    import io
    from loguru import logger

    log_stream = io.StringIO()
    handler_id = logger.add(log_stream, format="{message}")
    try:
        engine = SynchronizationEngine(db)
        connector = MockHealthyConnector("strava", _records(2))
        engine.run_once([connector])
    finally:
        logger.remove(handler_id)

    assert "flagged 0 implausible" in log_stream.getvalue()


def test_connector_summary_written_to_summary_log_exactly_once(db, tmp_path):
    """Issue #44 AC3/AC5: each connector's summary line must be written to
    summary.log exactly once per run — previously both the Sync Engine and
    trainiq/app.py's _log_sync_summary() logged their own copy, producing a
    duplicate. This exercises the real file sink (not an in-memory loguru
    capture) so it actually catches a regression where the line is logged
    twice to the same file."""
    from trainiq import logging_setup

    log_dir = tmp_path / "logs"
    logging_setup.configure(log_dir)
    try:
        connector = MockHealthyConnector("strava", _records(2))
        engine = SynchronizationEngine(db)
        engine.run_once([connector])
    finally:
        from loguru import logger
        logger.remove()

    summary_text = (log_dir / "summary.log").read_text()
    assert summary_text.count("strava: downloaded") == 1


def test_connector_sync_result_carries_summary_line_matching_logged_text(db):
    """The exact string ConnectorSyncResult.summary_line carries must match
    what was actually logged via summary_logger() — trainiq/app.py relies on
    this to echo the same text to the console without re-logging it."""
    import io
    from loguru import logger

    connector = MockHealthyConnector("strava", _records(2))

    log_stream = io.StringIO()
    handler_id = logger.add(log_stream, format="{message}")
    try:
        engine = SynchronizationEngine(db)
        result = engine.run_once([connector])
    finally:
        logger.remove(handler_id)

    r = result.connector_results[0]
    assert r.summary_line is not None
    assert r.summary_line in log_stream.getvalue()
    assert "flagged 0 implausible" in r.summary_line


def test_resync_never_clears_bo_confirmed_valid_on_already_confirmed_row(db):
    """ADR-039's auditability guarantee: once the BO has confirmed a
    flagged reading as valid (via scripts/confirm_weigh_in.py), a later
    resync re-evaluating the same row must re-derive the same
    is_flagged_implausible/plausibility_reason verdict but must NEVER
    touch bo_confirmed_valid/bo_confirmed_at — those columns are
    BO-owned, and _upsert_weigh_in()'s UPDATE statement deliberately
    excludes them."""
    records = _baseline_weigh_ins(5) + [dict(_OUTLIER_READING)]
    connector_1 = MockWeighInConnector("testscale", records)
    engine = SynchronizationEngine(db)
    engine.run_once([connector_1])

    confirmed_at = "2026-02-01T00:00:00+00:00"
    db.execute(
        "UPDATE weigh_ins SET bo_confirmed_valid = 1, bo_confirmed_at = ? WHERE external_id = 'outlier1'",
        (confirmed_at,),
    )
    db.commit()

    # Resync the exact same data — Eufy-style full-history reprocessing
    # (supports_incremental_sync = False) means this is the realistic case.
    connector_2 = MockWeighInConnector("testscale", records)
    engine.run_once([connector_2])

    row = db.execute(
        "SELECT is_flagged_implausible, bo_confirmed_valid, bo_confirmed_at "
        "FROM weigh_ins WHERE external_id = 'outlier1'"
    ).fetchone()
    assert row["is_flagged_implausible"] == 1  # the rule's own verdict is re-derived, unchanged
    assert row["bo_confirmed_valid"] == 1  # BO's confirmation survived the resync
    assert row["bo_confirmed_at"] == confirmed_at


def test_recent_weights_before_excludes_flagged_unconfirmed_readings(db):
    """_recent_weights_before() must not let a still-flagged, unconfirmed
    outlier poison the rolling baseline for subsequent readings."""
    records = _baseline_weigh_ins(5) + [dict(_OUTLIER_READING)]
    connector = MockWeighInConnector("testscale", records)
    engine = SynchronizationEngine(db)
    engine.run_once([connector])

    recent = engine._recent_weights_before("2026-01-11T00:00:00+00:00", 5)

    assert 20.0 not in recent  # the flagged outlier must not appear in the window
    assert all(w == 85.0 for w in recent)
