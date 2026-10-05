# ADR-007 — No Hardcoded Remote Endpoints

**Status:** Accepted (Phase 0, Milestone 1 — Strava Engineering Feasibility)
**Origin:** Strava's announced API base-URL migration — `https://www.strava.com/api/v3` → `https://api-v3.strava.com`, new host available 2027-01-04, final cutover deadline 2027-06-01 (Strava developer changelog / community-hub "An update to our developer program"; corrected 2026-10, see #26/#29).

## Context

A confirmed future breaking change to Strava's base URL provided concrete evidence that remote endpoint values change over the life of a long-running project, not just hypothetically.

**Correction (2026-10, #29):** this ADR originally named `api.strava.com` as Strava's current, valid API hostname, migrating to `api-v3.strava.com`. That was wrong about the *current* host: `api.strava.com` has no DNS record at all and has never been a reachable Strava host (confirmed independently by the BO and the Architect on #26, via `dig`/`getent hosts` from unrestricted networks — not a sandbox-egress artifact). Strava's actual, current API base is `https://www.strava.com/api/v3` (confirmed reachable: `curl` against it returns 401, i.e. auth-required, not a DNS or connection failure; `stravalib`'s own `ApiV3.server` attribute independently targets the same host). The migration itself is real and Strava-documented — it simply migrates *from* `www.strava.com/api/v3`, not from the fictional `api.strava.com`; see the corrected `Origin` line above and its source.

## Decision

No remote endpoint (base URL, path, or host) may appear as a literal string scattered through application code. Every connector's endpoints are defined as configuration in one place.

## Consequences

- The Strava base-URL migration becomes a one-line change wherever TrainIQ's own code defines the endpoint. Where a third-party library (`stravalib`) owns the URL internally, TrainIQ cannot fully control the migration itself — tracked separately as BL-001.
- Eufy and Peloton's hand-rolled clients (Epics 2 and 3) fully satisfy this ADR, since TrainIQ owns their entire HTTP layer.
