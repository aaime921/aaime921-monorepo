# ADR-013 — Connector Interface v2

**Status:** Accepted (Phase 0, Milestone 4 — Cross-cutting Architecture)
**Origin:** The original four-method interface (`authenticate`, `download`, `normalize`, `health_check`) proved sufficient for Strava but had no way to express concepts Peloton and Eufy's studies surfaced: state (ADR-010), multi-strategy (ADR-011), or manual recovery.

## Context

Extending the interface enriches what a Connector exposes; it does not alter the frozen Connectors → Normalization → Storage → … pipeline.

## Decision

The Connector interface gains: `get_state()`, `list_acquisition_strategies()`, `active_strategy()`, `request_manual_recovery()`. This is treated as an enrichment of the Connectors layer, not a pipeline change, and does not require an Architecture Change Request.

## Consequences

- Established the precedent later reused, without a new ADR, for `extract_resume_cursor()` (BL-005) and `record_kind` (Epic 6) — both judged to be the same category of interface refinement as this ADR, not new architectural capabilities.
- Every real connector (Strava, Eufy, Peloton) implements this interface fully.
