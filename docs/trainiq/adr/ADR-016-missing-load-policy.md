# ADR-016 — Missing Load Policy

**Status:** Accepted (Phase 0B, Milestone A — Normalization Engine)
**Origin:** Product decision following the sRPE (subjective effort) open question — TrainIQ's "passive automation by default" principle rules out asking the user to rate every workout.

## Context

Some activities (e.g. an unpaired Peloton strength class) will have neither power nor heart-rate data, and therefore no way to compute a training load through any known method.

## Decision

When training load cannot be reliably estimated: no artificial estimate is produced, no value is invented, the load is marked Unknown with a specific reason, and downstream confidence is reduced accordingly — never silently treated as zero load.

## Consequences

- This is the single most-cited principle in the entire codebase — it generalizes, unmodified, from missing training load to missing discipline mapping (Epic 6), missing confidence inputs, and the entire training-load engine's behavior with no `AthleteProfile` (Epic 7).
- Directly implemented by `TrainingLoadResult(load=None, method=UNKNOWN, reason=...)` in `trainiq/normalization/load.py`.
