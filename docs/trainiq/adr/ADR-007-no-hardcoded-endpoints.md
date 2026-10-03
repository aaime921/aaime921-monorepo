# ADR-007 — No Hardcoded Remote Endpoints

**Status:** Accepted (Phase 0, Milestone 1 — Strava Engineering Feasibility)
**Origin:** Strava's documented January 2027 API base-URL migration (`api.strava.com` → `api-v3.strava.com`).

## Context

A confirmed future breaking change to Strava's base URL provided concrete evidence that remote endpoint values change over the life of a long-running project, not just hypothetically.

## Decision

No remote endpoint (base URL, path, or host) may appear as a literal string scattered through application code. Every connector's endpoints are defined as configuration in one place.

## Consequences

- The Strava base-URL migration becomes a one-line change wherever TrainIQ's own code defines the endpoint. Where a third-party library (`stravalib`) owns the URL internally, TrainIQ cannot fully control the migration itself — tracked separately as BL-001.
- Eufy and Peloton's hand-rolled clients (Epics 2 and 3) fully satisfy this ADR, since TrainIQ owns their entire HTTP layer.
