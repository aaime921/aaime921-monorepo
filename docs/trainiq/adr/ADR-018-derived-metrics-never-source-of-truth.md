# ADR-018 — Derived Metrics Are Never Source of Truth

**Status:** Accepted (Phase 0B, Milestone B — Athlete Knowledge Model)
**Origin:** Research into CTL/ATL/TSB confirmed — including direct clarification from Andrew Coggan, TSS's original author — that these are always a fixed-formula EWMA over historical daily load, never an independently observed fact.

## Context

The question "what should TrainIQ persist about the athlete vs. derive?" has a principled answer once it's recognized that CTL/ATL/TSB are functions, not data.

## Decision

Any metric that is completely derivable from history (CTL, ATL, TSB, ACWR, and future readiness metrics) is never the primary source of truth. It may be cached for performance, but the cache must always be reconstructable from the underlying history, and never independently mutated.

## Consequences

- Directly shaped the SQLite design (raw + normalized tables kept separate, `normalized_activities` and any future derived-metrics cache being explicitly rebuildable) — reducing the risk of the classic "incremental derived state drifts from its source" bug class.
- Establishes the "persist only the irreducible" principle later applied concretely in Epic 7 (`AthleteProfile` persists only sex/DOB/resting-HR/max-HR/FTP — nothing derivable).
