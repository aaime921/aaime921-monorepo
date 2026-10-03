# ADR-008 — Checkpointed Synchronization

**Status:** Accepted (Phase 0, Milestone 1 — Strava Engineering Feasibility)
**Origin:** Strava's rate limits mean a full historical backfill for a multi-year account may require more than one day.

## Context

If a historical backfill cannot complete in a single run, TrainIQ must be able to resume safely across app restarts and process crashes, without re-fetching everything or losing progress.

## Decision

Synchronization is checkpointed: the last successfully processed point (a resume cursor) is persisted after every batch, and a resumed sync continues from there rather than restarting.

## Consequences

- Multi-day historical backfills are safe to interrupt at any point.
- Storage writes must be idempotent (upserts keyed on natural identifiers), since a resumed sync may re-request records already processed.
- Implemented by the Synchronization Engine (Epic 0.6) and directly verified by `test_resume_after_mid_sync_kill_no_duplicates_no_lost_progress`.
