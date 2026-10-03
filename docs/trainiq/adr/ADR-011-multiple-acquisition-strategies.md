# ADR-011 — Multiple Acquisition Strategies

**Status:** Accepted (Phase 0, Milestone 3 — Eufy Engineering Feasibility)
**Origin:** Eufy Life is the first provider found to support two independent data-acquisition paths — a cloud REST API and local Bluetooth (BLE) — rather than exactly one.

## Context

The original Connector model implicitly assumed one provider = one acquisition method. Eufy's Cloud+BLE duality showed this doesn't generalize.

## Decision

A connector may expose more than one acquisition strategy (e.g. Cloud, primary; BLE, fallback). The rest of the system sees one connector; strategy selection and checkpointing are strategy-aware internally.

## Consequences

- Checkpoints are keyed per `(provider, strategy)`, not per provider alone — this wiring was found to be missing during Epic 0 hardening (Finding 4) and fixed before Eufy's real implementation (Epic 2).
- BLE remains a documented, designed-for future fallback for Eufy — not built in V1, since it cannot backfill history and requires physical proximity.
- `AcquisitionStrategy` and `list_acquisition_strategies()`/`active_strategy()` are part of the Connector interface (Feature 0.5 / ADR-013).
