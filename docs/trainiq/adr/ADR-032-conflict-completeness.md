# ADR-032 — Conflict Completeness

**Status:** Accepted (Phase 0B, Milestone E — Recommendation Validation)
**Origin:** The Chief Architect's explicit principle that the Coach need not resolve every tension between signals — it can surface a genuine conflict as a tension rather than forcing a false resolution.

## Context

When evidence points in different directions (e.g. Ramp Rate suggests safe progression while TSB suggests fatigue), silently picking one side and citing only the convenient half of the evidence would be a subtler version of the fabrication problem ADR-016 already forbids.

## Decision

When the Evidence Package contains genuinely conflicting relevant signals, both must be represented and traceable in the explanation. The Coach may reach a conclusion, but it may not omit the other side.

## Consequences

- Directly checkable by the Recommendation Validator: does the citation list include both sides of a detected conflict, not just the one supporting the final recommendation.
- Increases the Coach's credibility by design — a system that can say "these two signals are in tension" is more trustworthy than one that always appears certain.
