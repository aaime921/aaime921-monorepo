# ADR-024 — Evidence Density

**Status:** Accepted (Phase 0B, Milestone C — Analytics Engine)
**Origin:** Two athletes can have an identical CTL value computed from very different amounts of underlying evidence (90 days of complete data vs. 90 days with many gaps) — the number alone doesn't convey that difference.

## Context

Confidence Propagation (ADR-017) needs a concrete mechanism for windowed/derived metrics specifically, since a single value can't self-report how much evidence supports it.

## Decision

Every time-windowed metric must track how many days were observed, missing, or of unknown load within its window. This doesn't change the metric's value — it changes how much the rest of the system should trust it.

## Consequences

- Realized in Epic 6 as `source_confidence` (per-record) and, by design, the intended basis for any future windowed-metric confidence in the Analytics Engine (not yet built as of Phase 0B's implementation phase).
- Directly informs Milestone D's Step 2 (Evidence Quality Evaluation) in the Coach Engine's decision process.
