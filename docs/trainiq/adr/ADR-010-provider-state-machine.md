# ADR-010 — Provider State Machine

**Status:** Accepted (Phase 0, Milestone 2 — Peloton Engineering Feasibility)
**Origin:** Companion decision to ADR-009 — graceful degradation needs a concrete state model, not just a principle.

## Context

"Degraded" is not a single condition — a connector could be transiently failing, persistently failing, or waiting on a manual fix. A single boolean (healthy/unhealthy) cannot represent this.

## Decision

Every connector has an explicit state: Healthy → Warning → Degraded → Recovery Required → Healthy. Transitions are outcome-driven (a real success or failure), never time-driven alone. Illegal transitions (e.g. Healthy directly to Recovery Required) are rejected.

## Consequences

- Implemented as `ProviderStateMachine` / `ConnectorState` (`trainiq/connectors/base.py`).
- Directly enables Peloton's manual-recovery flow (Epic 3) and later the automatic retry/escalation policy (ADR-038).
- The illegal-transition guarantee was found to have a real, previously-unexercised bug once `RecoveryRequired` connectors began being retried (Epic 3 / ADR-038 implementation) — fixed and covered by a dedicated regression test.
