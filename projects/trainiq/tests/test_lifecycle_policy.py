"""
Tests for ADR-038's lifecycle_policy.evaluate() — a pure function, tested
directly with no database, no connector, no Sync Engine involved. This is
deliberate: correctness of the temporal logic should be provable in
isolation before it's ever wired into orchestration.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from trainiq.connectors.base import ConnectorState
from trainiq.sync.lifecycle_policy import (
    DEGRADED_BACKOFF_SCHEDULE_DAYS,
    DEGRADED_ESCALATION_THRESHOLD_DAYS,
    LifecycleDecision,
    LifecycleState,
    evaluate,
)

NOW = datetime(2026, 6, 1, 12, 0, 0, tzinfo=timezone.utc)


def _state(state, entered_days_ago=None, last_attempt_days_ago=None, attempt_count=0):
    return LifecycleState(
        state=state,
        state_entered_at=(NOW - timedelta(days=entered_days_ago)) if entered_days_ago is not None else None,
        last_attempt_at=(NOW - timedelta(days=last_attempt_days_ago)) if last_attempt_days_ago is not None else None,
        attempt_count_in_state=attempt_count,
    )


# --- Healthy / Warning: unaffected by ADR-038 -------------------------------

def test_healthy_always_retries():
    assert evaluate(_state(ConnectorState.HEALTHY), NOW) == LifecycleDecision.RETRY


def test_warning_always_retries():
    assert evaluate(_state(ConnectorState.WARNING), NOW) == LifecycleDecision.RETRY


# --- RecoveryRequired: default "every run" ----------------------------------

def test_recovery_required_always_retries_even_immediately_after_last_attempt():
    lifecycle = _state(ConnectorState.RECOVERY_REQUIRED, entered_days_ago=0, last_attempt_days_ago=0, attempt_count=5)
    assert evaluate(lifecycle, NOW) == LifecycleDecision.RETRY


# --- Degraded: backoff cadence (Quality Gate 2 — never escalates/retries early) --

def test_degraded_never_attempted_yet_is_immediately_eligible():
    lifecycle = _state(ConnectorState.DEGRADED, entered_days_ago=0, last_attempt_days_ago=None, attempt_count=0)
    assert evaluate(lifecycle, NOW) == LifecycleDecision.RETRY


def test_degraded_skips_before_first_backoff_interval_elapses():
    # attempt_count_in_state=0 -> 1-day cadence; only 12 hours have passed.
    lifecycle = _state(ConnectorState.DEGRADED, entered_days_ago=0.5, last_attempt_days_ago=0.5, attempt_count=0)
    assert evaluate(lifecycle, NOW) == LifecycleDecision.SKIP


def test_degraded_retries_exactly_at_the_first_backoff_boundary():
    assert DEGRADED_BACKOFF_SCHEDULE_DAYS[0] == 1
    lifecycle = _state(ConnectorState.DEGRADED, entered_days_ago=1, last_attempt_days_ago=1, attempt_count=0)
    assert evaluate(lifecycle, NOW) == LifecycleDecision.RETRY


def test_degraded_backoff_cadence_increases_with_attempt_count():
    """After 1 attempt, the gap is 1 day (schedule[0]) — 1.5 days since the
    last attempt is already past that, so a second attempt is due. After 2
    attempts, the gap grows to 2 days — the same 1.5-day elapsed time is
    NOT yet enough for a third. This is the specific property "increases
    with attempt count" is meant to demonstrate, and it's tested at the
    exact boundary rather than comfortably past it, per the same
    off-by-one-hunting discipline used elsewhere in this test file."""
    after_one_attempt = _state(ConnectorState.DEGRADED, entered_days_ago=2, last_attempt_days_ago=1.5, attempt_count=1)
    assert evaluate(after_one_attempt, NOW) == LifecycleDecision.RETRY

    after_two_attempts = _state(ConnectorState.DEGRADED, entered_days_ago=2, last_attempt_days_ago=1.5, attempt_count=2)
    assert evaluate(after_two_attempts, NOW) == LifecycleDecision.SKIP


def test_degraded_backoff_caps_at_the_longest_scheduled_value():
    # attempt_count_in_state way beyond the schedule length must still cap
    # at the last value (7 days), not grow unbounded or index-error.
    lifecycle = _state(ConnectorState.DEGRADED, entered_days_ago=9, last_attempt_days_ago=6.9, attempt_count=99)
    assert evaluate(lifecycle, NOW) == LifecycleDecision.SKIP
    lifecycle_due = _state(ConnectorState.DEGRADED, entered_days_ago=9, last_attempt_days_ago=7.0, attempt_count=99)
    assert evaluate(lifecycle_due, NOW) == LifecycleDecision.RETRY


# --- Degraded: escalation (Quality Gates 1 and 2 together) -----------------

def test_degraded_escalates_at_exactly_the_threshold_when_also_due_for_retry():
    assert DEGRADED_ESCALATION_THRESHOLD_DAYS == 10
    lifecycle = _state(ConnectorState.DEGRADED, entered_days_ago=10, last_attempt_days_ago=7, attempt_count=3)
    assert evaluate(lifecycle, NOW) == LifecycleDecision.ESCALATE


def test_degraded_does_not_escalate_one_day_short_of_threshold():
    """Quality Gate 2 (never escalates early), tested at the exact boundary,
    not comfortably before it — this is where off-by-one bugs hide."""
    lifecycle = _state(ConnectorState.DEGRADED, entered_days_ago=9, last_attempt_days_ago=7, attempt_count=3)
    assert evaluate(lifecycle, NOW) == LifecycleDecision.RETRY  # due for a retry, but NOT an escalation


def test_degraded_past_threshold_but_not_yet_due_for_retry_skips_not_escalates():
    """Escalation must never override the backoff cadence — even past the
    10-day mark, if we're not yet due for a retry, we skip. Escalation is
    only ever decided AT the moment an attempt is actually due (ADR-038 §4:
    'never triggered by elapsed time alone with no corresponding attempt')."""
    lifecycle = _state(ConnectorState.DEGRADED, entered_days_ago=15, last_attempt_days_ago=0.1, attempt_count=3)
    assert evaluate(lifecycle, NOW) == LifecycleDecision.SKIP


def test_degraded_with_no_state_entered_at_does_not_escalate_spuriously():
    """Defensive: missing state_entered_at (shouldn't happen in practice,
    but must not be misread as 'elapsed forever') must not cause a bogus
    immediate escalation."""
    lifecycle = _state(ConnectorState.DEGRADED, entered_days_ago=None, last_attempt_days_ago=None, attempt_count=0)
    assert evaluate(lifecycle, NOW) == LifecycleDecision.RETRY


# --- Unknown/defensive ------------------------------------------------------

def test_unrecognized_state_defaults_to_skip():
    class _FakeState:
        pass
    lifecycle = LifecycleState(state=_FakeState(), state_entered_at=None, last_attempt_at=None, attempt_count_in_state=0)
    assert evaluate(lifecycle, NOW) == LifecycleDecision.SKIP


# --- Determinism (the core discipline requested) ----------------------------

def test_evaluate_is_pure_same_inputs_same_output_every_time():
    lifecycle = _state(ConnectorState.DEGRADED, entered_days_ago=3, last_attempt_days_ago=2, attempt_count=1)
    results = {evaluate(lifecycle, NOW) for _ in range(50)}
    assert len(results) == 1
