# ADR-012 — Connector Capability Tiers

**Status:** Accepted (Phase 0, Milestone 3 — Eufy Engineering Feasibility)
**Origin:** After three provider studies, a pattern emerged: Strava taught official-API integration, Peloton taught unofficial-API resilience, Eufy taught multi-strategy acquisition — three qualitatively different categories of connector.

## Context

Without a shared vocabulary for "how risky/well-supported is this connector's data source," every future provider discussion would re-derive the same distinctions from scratch.

## Decision

Connectors are classified into descriptive tiers: Tier 1 (Official API), Tier 2 (Unofficial API), Tier 3 (Multiple Acquisition Strategies). This is metadata, not a structural constraint — a connector can change tier over time without a system redesign (see R-ARCH-03, Capability Drift).

## Consequences

- `CapabilityTier` is a class attribute on every `Connector` subclass (`trainiq/connectors/base.py`).
- Strava = Tier 1, Peloton = Tier 2, Eufy = Tier 3 — confirmed unchanged through implementation.
- Provides a shared classification for any future provider (Garmin, Withings, Oura, Apple Health) before it is built.
