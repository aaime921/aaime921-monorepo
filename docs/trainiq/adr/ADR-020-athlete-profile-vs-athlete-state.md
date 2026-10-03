# ADR-020 — Athlete Profile vs. Athlete State

**Status:** Accepted (Phase 0B, Milestone B — Athlete Knowledge Model)
**Origin:** ADR-018's "persist only the irreducible" principle implies two genuinely different kinds of athlete data exist.

## Context

"Athlete State" had been used generically for both stable facts (sex, resting HR) and dynamic, derived conditions (current CTL/ATL). Conflating them makes the persistence model unclear.

## Decision

Athlete Profile: persistent facts, modified rarely (sex, date of birth, resting HR, max HR, FTP). Athlete State: dynamic data, always reconstructable from history.

## Consequences

- `AthleteProfile` (Epic 6/7, `trainiq/athlete/profile.py`) is exactly the Profile half of this ADR — a pure dataclass, deliberately kept free of any State-like derived fields.
- The State half (CTL/ATL/TSB caches) remains unbuilt as of Phase 0B's implementation phase — correctly deferred, not overlooked, since Epic 6/7 only needed the Profile half to unblock training-load computation.
