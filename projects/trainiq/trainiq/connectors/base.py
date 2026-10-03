"""
trainiq.connectors.base — Feature 0.5

Connector interface v2 (ADR-013) and the Provider State Machine (ADR-010),
implemented as a shared component per the Chief Architect's direction —
this is not per-connector logic, every connector shares one implementation.

State machine: Healthy -> Warning -> Degraded -> RecoveryRequired -> Healthy
Graceful Degradation (ADR-009): a Degraded/RecoveryRequired connector is
skipped, logged, and surfaced as a warning — it never halts the overall
sync cycle. This module provides the primitive; Feature 0.6 (Sync Engine)
is responsible for actually orchestrating multiple connectors around it.
"""

from __future__ import annotations

import abc
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional


class ConnectorState(str, Enum):
    HEALTHY = "Healthy"
    WARNING = "Warning"
    DEGRADED = "Degraded"
    RECOVERY_REQUIRED = "RecoveryRequired"


# Legal transitions, per ADR-010. Anything not listed here is rejected —
# the state machine is deliberately strict, not a free-form status field.
_ALLOWED_TRANSITIONS: dict[ConnectorState, set[ConnectorState]] = {
    ConnectorState.HEALTHY: {ConnectorState.WARNING, ConnectorState.DEGRADED},
    ConnectorState.WARNING: {ConnectorState.HEALTHY, ConnectorState.DEGRADED},
    ConnectorState.DEGRADED: {ConnectorState.RECOVERY_REQUIRED, ConnectorState.HEALTHY},
    ConnectorState.RECOVERY_REQUIRED: {ConnectorState.HEALTHY},
}


class InvalidStateTransition(Exception):
    pass


@dataclass
class StateChange:
    provider: str
    from_state: ConnectorState
    to_state: ConnectorState
    at: str
    detail: Optional[str] = None


class ProviderStateMachine:
    """One instance per connector. Owns the current state and enforces
    legal transitions only."""

    def __init__(self, provider: str, initial: ConnectorState = ConnectorState.HEALTHY):
        self.provider = provider
        self._state = initial
        self.history: list[StateChange] = []

    @property
    def state(self) -> ConnectorState:
        return self._state

    def transition(self, to_state: ConnectorState, detail: str | None = None) -> StateChange:
        if to_state == self._state:
            # No-op transitions are allowed (e.g. re-confirming Healthy) and
            # are not an error — they're just not recorded as a change.
            return StateChange(self.provider, self._state, to_state, _iso_now(), detail)
        allowed = _ALLOWED_TRANSITIONS.get(self._state, set())
        if to_state not in allowed:
            raise InvalidStateTransition(
                f"{self.provider}: cannot transition {self._state} -> {to_state}"
            )
        change = StateChange(self.provider, self._state, to_state, _iso_now(), detail)
        self._state = to_state
        self.history.append(change)
        return change

    def is_available(self) -> bool:
        """Whether the Synchronization Engine should attempt to use this
        connector at all right now."""
        return self._state in (ConnectorState.HEALTHY, ConnectorState.WARNING)

    def restore(self, state: ConnectorState) -> None:
        """Loads persisted state on startup — NOT a real transition, so it
        deliberately bypasses _ALLOWED_TRANSITIONS and is never recorded in
        `history`. Discovered as a genuine gap while implementing ADR-038:
        every fresh Connector instance previously defaulted to Healthy
        regardless of what was persisted in connector_state, meaning
        Graceful Degradation only ever worked within a single continuous
        process, never across an app restart. The Synchronization Engine
        calls this once, at the very start of sync_connector(), before
        evaluating the lifecycle policy."""
        self._state = state


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class AcquisitionStrategy(str, Enum):
    """ADR-011 — a connector may support more than one strategy (Eufy's
    Cloud+BLE duality). Most connectors expose exactly one."""
    CLOUD = "cloud"
    BLE = "ble"
    OAUTH = "oauth"
    UNOFFICIAL_SESSION = "unofficial_session"


class CapabilityTier(str, Enum):
    """ADR-012 — descriptive metadata, not a structural constraint. A
    connector can change tier over time (R-ARCH-03) without a system
    redesign."""
    TIER_1_OFFICIAL = "tier_1_official"
    TIER_2_UNOFFICIAL = "tier_2_unofficial"
    TIER_3_MULTI_STRATEGY = "tier_3_multi_strategy"


class RecordKind(str, Enum):
    """Epic 6 discovery finding, Q2: what canonical shape does this
    connector's normalize() output take? Explicit and connector-declared,
    not inferred by the Normalization Engine sniffing which keys happen to
    be present — the same lesson BL-005 already taught this project one
    layer down (the Sync Engine must not guess a connector's field names;
    by the same logic, the Normalization Engine must not guess a
    connector's record shape). One pipeline, two canonical record kinds —
    not two pipelines — per the Chief Architect's explicit framing."""
    ACTIVITY = "activity"     # RawActivity-shaped (Strava, Peloton)
    WEIGH_IN = "weigh_in"     # WeighIn-shaped (Eufy)


class Connector(abc.ABC):
    """Connector interface v2 (ADR-013).

    Base four methods (Milestone 4 / original Part 8 spec):
        authenticate(), download(), normalize(), health_check()
    Extended per ADR-013:
        get_state(), list_acquisition_strategies(), active_strategy(),
        request_manual_recovery()

    This is an enrichment of the Connectors layer, not a pipeline change —
    see Tier B roadmap, Section 6.
    """

    provider: str
    capability_tier: CapabilityTier
    # Epic 6 discovery, Q2 (interface refinement, not an ADR): defaults to
    # ACTIVITY so every existing connector and every existing mock/test
    # connector continues to work with zero changes. Only a connector whose
    # normalize() output is WeighIn-shaped needs to override this — today,
    # only EufyConnector. Not yet consumed anywhere (SynchronizationEngine
    # doesn't read this yet) — this slice only establishes the contract.
    record_kind: RecordKind = RecordKind.ACTIVITY

    # BL-006: whether this connector's provider is known to honor
    # incremental filtering (via extract_resume_cursor()'s value passed
    # back as `since`). Defaults to True — the assumption every connector
    # has operated under since ADR-006. A connector whose provider has
    # been directly, live-verified NOT to filter (confirmed by a real
    # network experiment, not inferred) should override this to False, so
    # the Sync Engine can log an accurate, non-alarming explanation
    # instead of leaving a full-history download looking like a bug to
    # anyone reading logs later. The Sync Engine itself contains no
    # provider-specific knowledge — it only reads this generic flag.
    supports_incremental_sync: bool = True

    def __init__(self, provider: str):
        self.provider = provider
        self._state_machine = ProviderStateMachine(provider)

    # --- original four methods -------------------------------------------------
    @abc.abstractmethod
    def authenticate(self) -> bool:
        """Returns True on success. Must never raise for expected auth
        failures — those should be reflected via a state transition instead,
        so the Sync Engine can react uniformly (ADR-009)."""
        ...

    @abc.abstractmethod
    def download(self, since: str | None = None) -> list[dict[str, Any]]:
        """Returns raw provider payloads. `since` is an opaque cursor/timestamp
        from sync_checkpoints (ADR-008)."""
        ...

    @abc.abstractmethod
    def normalize(self, raw: dict[str, Any]) -> dict[str, Any]:
        """Per-connector field mapping into the canonical shape Epic 6 (the
        Normalization Engine) defines. This method does NOT compute training
        load or apply the discipline taxonomy — that's Epic 6's job; this is
        just extracting the connector-specific raw fields."""
        ...

    def health_check(self) -> bool:
        return self._state_machine.is_available()

    # --- ADR-013 extensions ------------------------------------------------
    def get_state(self) -> ConnectorState:
        return self._state_machine.state

    def list_acquisition_strategies(self) -> list[AcquisitionStrategy]:
        """Override in Tier 3 connectors (e.g. Eufy) to report more than one."""
        return []

    def active_strategy(self) -> AcquisitionStrategy | None:
        strategies = self.list_acquisition_strategies()
        return strategies[0] if strategies else None

    def request_manual_recovery(self) -> str:
        """Default: no manual recovery path exists. Tier 2 connectors with a
        known manual fallback (e.g. Peloton's bearer-token recovery) should
        override this with real instructions/state."""
        return "No manual recovery procedure is defined for this connector."

    # --- ADR-013 refinement (Epic 2 / Eufy): resume cursor ownership -------
    def extract_resume_cursor(self, normalized: dict[str, Any]) -> str | None:
        """Returns the value the Synchronization Engine should treat as the
        resume point for this normalized record, or None if this record
        doesn't advance the resume position.

        This is connector-owned, not Sync-Engine-owned, per the same
        reasoning already established for `normalize()` itself: provider-
        specific field knowledge belongs inside the connector, never in
        shared infrastructure. The Sync Engine calls this method and never
        inspects `normalized`'s keys directly.

        CONTRACT — the returned value must be safely orderable via a plain
        string `>` comparison in the Sync Engine's "track the latest"
        logic. ISO 8601 timestamps satisfy this by construction. A
        zero-padded sequence number does too. An unpadded one ("9" vs "10")
        does NOT — "9" > "10" lexicographically, which is wrong. An opaque
        continuation token from a provider's own pagination API generally
        does NOT satisfy this either, unless the provider specifically
        guarantees lexicographic monotonicity. A connector overriding this
        method is responsible for either returning a naturally-comparable
        value, or (a future extension, not needed by any connector today)
        supplying its own comparison function alongside the cursor.

        Default implementation covers `RawActivity`-shaped output
        (Strava, Peloton): the natural resume point is `start_time`, an
        ISO 8601 string, which satisfies the ordering contract above.
        Connectors with a differently-shaped `normalize()` output (e.g.
        Eufy's `WeighIn` shape, which uses `timestamp` rather than
        `start_time`) MUST override this method — the default will
        silently return None for such records, which the Sync Engine
        correctly treats as "this record doesn't advance the checkpoint,"
        not as an error. This is why the gap this method fixes was a
        silent inefficiency (checkpoint never advances) rather than a
        crash — worth knowing when reasoning about how a future
        connector's mistake here would actually surface.
        """
        return normalized.get("start_time")

    # --- shared state-machine plumbing --------------------------------------
    def transition_state(self, to_state: ConnectorState, detail: str | None = None) -> StateChange:
        return self._state_machine.transition(to_state, detail)

    def restore_state(self, state: ConnectorState) -> None:
        """See ProviderStateMachine.restore() — loads persisted state on
        startup, not a real transition."""
        self._state_machine.restore(state)
