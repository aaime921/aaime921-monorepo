# ADR-022 — No Single Metric Determines a Recommendation

**Status:** Accepted (Phase 0B, Milestone C — Analytics Engine)
**Origin:** ACWR research found genuine, peer-reviewed counter-evidence to its predictive validity for injury (e.g. a study finding no predictive relationship in elite football), despite its broad commercial popularity as a stand-alone "readiness" number.

## Context

Presenting any single popular metric as a definitive verdict overstates what the evidence actually supports, especially for metrics with mixed research support like ACWR.

## Decision

No metric, regardless of popularity, may alone determine a recommendation. The Coach must always combine multiple signals.

## Consequences

- Directly enforced structurally in Epic 6/9's design: ACWR is never exposed without at least one other core signal alongside it.
- Became the Coach Engine's central design constraint (Milestone D): Factor Identification must always combine signals, and the Recommendation Validator (Milestone E, ADR-030) checks this as one of its hard gates.
