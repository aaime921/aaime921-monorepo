# ADR-006 — Incremental Polling (Not Webhooks)

**Status:** Accepted (Phase 0, Milestone 1 — Strava Engineering Feasibility)
**Origin:** Strava connector feasibility research.

## Context

Strava supports webhook-based push notifications for activity updates, requiring a publicly reachable HTTPS callback endpoint. TrainIQ is a single-user macOS desktop application with no server component and no guarantee of being continuously running.

## Decision

TrainIQ uses `after`-timestamp incremental polling (`GET /athlete/activities?after=...`) for synchronization. Webhooks are explicitly out of scope for V1.

## Consequences

- No public server, certificate, or always-on infrastructure is required — consistent with the "double-click → sync → recommendation" product model.
- Sync freshness is bounded by how often the app is launched (or, later, a background scheduler), not real-time — judged acceptable for a personal training tool.
- The same polling pattern generalizes to any future REST-based provider without requiring provider-specific push infrastructure.
