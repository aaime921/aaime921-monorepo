# ADR-009 — Graceful Provider Degradation

**Status:** Accepted (Phase 0, Milestone 2 — Peloton Engineering Feasibility)
**Origin:** Documented evidence that Peloton's automated login broke for an extended period (Oct 2025–Jan 2026), and the Chief Architect's "Soft Degradation" product decision.

## Context

A provider becoming unavailable must not become the user's problem to notice and fix immediately. TrainIQ's original design implicitly assumed all providers work; Peloton's documented breakage proved that assumption false.

## Decision

An unavailable provider does not interrupt TrainIQ, does not block the Coach, and does not modify AthleteState beyond the data actually available. It generates a warning and enters a Degraded state. Only prolonged degradation requires user intervention — manual recovery is a recovery path, not the default user experience.

## Consequences

- TrainIQ continues functioning on partial data (e.g., Strava + Eufy) when one provider is down.
- Requires a formal state model for "how degraded" a connector is — see ADR-010.
- Later found (Epic 3 / ADR-038) to need a companion policy for *when* to retry a degraded connector, since graceful degradation alone does not specify a retry cadence.
