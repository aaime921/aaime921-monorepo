# ADR-014 — Synchronization Engine Is Scheduler-Agnostic

**Status:** Accepted (Phase 0, Milestone 4 — Cross-cutting Architecture)
**Origin:** V1 triggers sync on app launch only, but the design should not preclude a future background scheduler (e.g. a macOS LaunchAgent).

## Context

Coupling the Synchronization Engine to a specific trigger mechanism would make adding a background scheduler later a redesign rather than an addition.

## Decision

The Synchronization Engine has no knowledge of what triggered it — `run_once()` is the only entry point a caller needs, whether that caller is app launch or a future scheduler.

## Consequences

- A future LaunchAgent-based background sync (Tier A roadmap, Feature 5.2) requires no changes to `SynchronizationEngine` itself.
- Verified structurally: `trainiq/sync/engine.py` contains no scheduling logic, only `run_once()` as its public entry point.
