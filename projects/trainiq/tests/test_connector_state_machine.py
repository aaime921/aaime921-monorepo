"""
Tests for Feature 0.5 — Provider State Machine (ConnectorState / ADR-010).

Previously exercised only implicitly, via the *legal* paths that
`test_sync_engine.py` happens to walk. Epic 0 Hardening Finding 5: no test
directly proved illegal transitions are actually rejected. This file closes
that gap.
"""

from __future__ import annotations

import pytest

from trainiq.connectors.base import (
    ConnectorState,
    InvalidStateTransition,
    ProviderStateMachine,
)


def test_healthy_to_warning_is_legal():
    sm = ProviderStateMachine("strava")
    sm.transition(ConnectorState.WARNING)
    assert sm.state == ConnectorState.WARNING


def test_healthy_to_degraded_is_legal():
    sm = ProviderStateMachine("strava")
    sm.transition(ConnectorState.DEGRADED)
    assert sm.state == ConnectorState.DEGRADED


def test_healthy_directly_to_recovery_required_is_illegal():
    """The state machine is Healthy -> Warning -> Degraded -> RecoveryRequired
    -> Healthy (ADR-010) — skipping straight from Healthy to RecoveryRequired
    must be rejected, not silently allowed."""
    sm = ProviderStateMachine("peloton")
    with pytest.raises(InvalidStateTransition):
        sm.transition(ConnectorState.RECOVERY_REQUIRED)
    assert sm.state == ConnectorState.HEALTHY  # rejected transition leaves state unchanged


def test_warning_directly_to_recovery_required_is_illegal():
    """Must go through Degraded first — Warning cannot skip straight to
    RecoveryRequired either."""
    sm = ProviderStateMachine("peloton")
    sm.transition(ConnectorState.WARNING)
    with pytest.raises(InvalidStateTransition):
        sm.transition(ConnectorState.RECOVERY_REQUIRED)
    assert sm.state == ConnectorState.WARNING


def test_recovery_required_can_only_go_to_healthy():
    sm = ProviderStateMachine("peloton", initial=ConnectorState.RECOVERY_REQUIRED)
    with pytest.raises(InvalidStateTransition):
        sm.transition(ConnectorState.WARNING)
    with pytest.raises(InvalidStateTransition):
        sm.transition(ConnectorState.DEGRADED)
    sm.transition(ConnectorState.HEALTHY)
    assert sm.state == ConnectorState.HEALTHY


def test_full_legal_cycle():
    sm = ProviderStateMachine("peloton")
    sm.transition(ConnectorState.WARNING)
    sm.transition(ConnectorState.DEGRADED)
    sm.transition(ConnectorState.RECOVERY_REQUIRED)
    sm.transition(ConnectorState.HEALTHY)
    assert sm.state == ConnectorState.HEALTHY
    assert len(sm.history) == 4


def test_same_state_transition_is_a_noop_not_an_error():
    sm = ProviderStateMachine("strava")
    sm.transition(ConnectorState.HEALTHY)  # already Healthy
    assert sm.state == ConnectorState.HEALTHY
    assert len(sm.history) == 0  # no-op, not recorded as a change


def test_illegal_transition_does_not_corrupt_history():
    """A rejected transition must not leave a partial/incorrect trace in
    history — the exception must be raised before any mutation happens."""
    sm = ProviderStateMachine("peloton")
    sm.transition(ConnectorState.WARNING)
    history_len_before = len(sm.history)
    with pytest.raises(InvalidStateTransition):
        sm.transition(ConnectorState.RECOVERY_REQUIRED)
    assert len(sm.history) == history_len_before


def test_is_available_true_only_for_healthy_and_warning():
    sm = ProviderStateMachine("strava")
    assert sm.is_available() is True
    sm.transition(ConnectorState.WARNING)
    assert sm.is_available() is True
    sm.transition(ConnectorState.DEGRADED)
    assert sm.is_available() is False
    sm.transition(ConnectorState.RECOVERY_REQUIRED)
    assert sm.is_available() is False
