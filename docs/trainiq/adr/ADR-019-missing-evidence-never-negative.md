# ADR-019 — Missing Evidence Never Becomes Negative Evidence

**Status:** Accepted (Phase 0B, Milestone B — Athlete Knowledge Model)
**Origin:** Direct extension of ADR-016 to its logical consequence for time-series/derived metrics: a day with unknown training load is not the same as a rest day.

## Context

A naive CTL/ATL implementation that zero-fills days with Unknown load would systematically understate fitness and fatigue — every data gap would silently look like a rest day.

## Decision

Unknown, Missing, and Zero are three distinct states in the internal model. They must never be automatically converted into one another — an EWMA calculation must exclude Unknown-load days from its window, not treat them as zero.

## Consequences

- Verified concretely in Epic 6: `test_degraded_connector_...` and the Monotony/ACWR design in Milestone C both explicitly exclude Unknown-load days from statistical windows rather than zero-filling them.
- One of the clearest examples in the project of a single ADR generalizing correctly across multiple, later-built components without modification.
