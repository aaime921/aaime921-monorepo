# ADR-029 — Decision Provenance

**Status:** Accepted (Phase 0B, Milestone D — Coach Engine Decision Architecture)
**Origin:** The Chief Architect's observation that TrainIQ should be able to compare recommendations produced by different Coach/model/ADR versions over time, not just log the final recommendation text.

## Context

Storing only "recommended workout: X" makes it impossible to later audit why that recommendation was made, or to compare behavior across system versions as the Coach evolves.

## Decision

Every recommendation must be fully reconstructable: the Evidence Package, selected signals, confidence, explanation, timestamp, and the specific ADR/Coach version in effect are all persisted together.

## Consequences

- Makes future A/B-style comparison of Coach versions possible without re-deriving historical context from logs alone.
- Directly gated by ADR-030 (Validation Is a Hard Gate) in the intended design: a Decision Provenance record should only ever represent a recommendation that passed validation, never a rejected or malformed one.
