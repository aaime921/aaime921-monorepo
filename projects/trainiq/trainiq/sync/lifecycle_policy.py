"""
trainiq.sync.lifecycle_policy — ADR-038: Connector Lifecycle Policy

Per the Chief Architect's explicit implementation discipline: this is a
pure, deterministic function with zero I/O and zero side effects. All
temporal decision-making lives here, in one place, rather than distributed
across scattered `if` statements inside SynchronizationEngine. Changing a
single threshold six months from now means changing one function, not
hunting through the orchestration code for every place time is checked.

One uniform policy (ADR-038 §4, revision 2) — no per-connector parameters,
no per-connector overrides. Every connector (Strava, Eufy, Peloton, and any
future one) is evaluated against exactly the same constants below.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import Enum
from typing import Optional

from trainiq.connectors.base import ConnectorState

# --- Policy constants (ADR-038 §4) — the ONLY place these numbers live ----
# Flagged, per the ADR itself, as calibratable engineering judgment
# (splitting the Chief Architect's "7 or 14 days" range from Milestone 2),
# not independently re-derived evidence.

DEGRADED_BACKOFF_SCHEDULE_DAYS: tuple[int, ...] = (1, 2, 4, 7)  # capped at the last value
DEGRADED_ESCALATION_THRESHOLD_DAYS: int = 10
# RecoveryRequired's default cadence is "every run" — i.e. always eligible,
# no waiting period. See ADR-038 §4's revision note for why this is a
# policy default, not a guarantee any connector can assume is free.


class LifecycleDecision(str, Enum):
    SKIP = "skip"
    RETRY = "retry"
    ESCALATE = "escalate"  # attempt now; if it fails, escalate to RecoveryRequired (ADR-038 §4)


@dataclass(frozen=True)
class LifecycleState:
    """Exactly the persisted fields ADR-038 §3 defines — passed in
    explicitly so this function has no database access of its own."""
    state: ConnectorState
    state_entered_at: Optional[datetime]
    last_attempt_at: Optional[datetime]
    attempt_count_in_state: int


def _backoff_cadence(attempts_made_in_state: int) -> timedelta:
    """The gap required AFTER `attempts_made_in_state` attempts have
    already occurred, before the next one is eligible — per ADR-038 §4's
    literal sequence: after 1 attempt, wait 1 day; after 2, wait 2 days;
    after 3, wait 4 days; after 4 or more, wait 7 days (capped).

    CORRECTNESS NOTE, left in place deliberately: an earlier version of
    this function indexed directly by attempts_made_in_state with no
    offset, which silently skipped the 1-day step entirely (a connector's
    first retry after entering Degraded would have waited 2 days, not 1).
    Caught by re-deriving the mapping from the ADR's own stated sequence
    rather than trusting the first implementation — the same discipline
    this whole project has tried to hold itself to elsewhere. `attempts_made_in_state=0`
    (no attempts yet) is handled by the caller short-circuiting on
    `last_attempt_at is None` before this function is ever consulted, so
    the floor-at-0 below only protects against being called with 0
    directly, not the primary path."""
    idx = min(max(attempts_made_in_state - 1, 0), len(DEGRADED_BACKOFF_SCHEDULE_DAYS) - 1)
    return timedelta(days=DEGRADED_BACKOFF_SCHEDULE_DAYS[idx])


def evaluate(lifecycle: LifecycleState, now: datetime) -> LifecycleDecision:
    """The single source of truth for "should the Sync Engine attempt this
    connector right now." Pure function: same inputs always produce the
    same output, no matter when or how many times it's called.
    """
    if lifecycle.state in (ConnectorState.HEALTHY, ConnectorState.WARNING):
        # Unaffected by ADR-038 — unchanged pre-existing behavior.
        return LifecycleDecision.RETRY

    if lifecycle.state == ConnectorState.RECOVERY_REQUIRED:
        # Default policy value: every run. Uniform across all connectors
        # (ADR-038 §4, revision 2) — never a per-connector parameter.
        return LifecycleDecision.RETRY

    if lifecycle.state == ConnectorState.DEGRADED:
        eligible_for_attempt = (
            lifecycle.last_attempt_at is None
            or now >= lifecycle.last_attempt_at + _backoff_cadence(lifecycle.attempt_count_in_state)
        )
        if not eligible_for_attempt:
            return LifecycleDecision.SKIP

        elapsed_in_state = (
            (now - lifecycle.state_entered_at) if lifecycle.state_entered_at else timedelta(0)
        )
        if elapsed_in_state >= timedelta(days=DEGRADED_ESCALATION_THRESHOLD_DAYS):
            # Conservative by design (ADR-038 §4): escalation is always
            # preceded by one real attempt, never triggered by elapsed
            # time alone with no corresponding attempt.
            return LifecycleDecision.ESCALATE
        return LifecycleDecision.RETRY

    # Defensive default for any future/unknown state — never attempt
    # something the policy doesn't explicitly recognize.
    return LifecycleDecision.SKIP
