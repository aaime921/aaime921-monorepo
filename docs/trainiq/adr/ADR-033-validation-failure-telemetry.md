# ADR-033 — Validation Failure Telemetry

**Status:** Accepted (Phase 0B, Milestone E — Recommendation Validation)
**Origin:** Extension of ADR-030's fallback behavior — the Chief Architect's request that every validation failure be recorded as a structured event, not just silently absorbed into a generic fallback message.

## Context

A fallback message ("not enough reliable signal today") protects the user in the moment, but without a record of *why* validation failed, the system can never be tuned or debugged based on real usage.

## Decision

Every validation failure is logged as a structured event, recording the failure reason, which specific check failed, and the retry count.

## Consequences

- This is the mechanism by which literature-derived thresholds (e.g. escalation days, monotony bands) are expected to eventually be recalibrated against real usage data, rather than left as permanent, unexamined defaults.
- A precedent later followed structurally by ADR-038's persisted lifecycle state, which likewise treats "why did this happen" as data worth keeping, not just an ephemeral log line.
