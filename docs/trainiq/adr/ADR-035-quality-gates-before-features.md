# ADR-035 — Quality Gates Before Features

**Status:** Accepted (Phase 0B → Implementation Roadmap transition)
**Origin:** The Chief Architect's review of the completed Tier A/B Implementation Roadmap — proposed as the natural extension of the discipline already applied throughout design.

## Context

Without an explicit gate, "done" can silently come to mean "produces output" rather than "produces output that meets the layer's actual quality bar" — a drift that tends to happen gradually, not as a single visible decision.

## Decision

A feature is not complete when it produces an output. It is complete when that output passes the quality criteria defined for its layer. This applies uniformly, from a connector's first sync to the Coach's final explanation.

## Consequences

- Directly produced the Implementation Roadmap's six Quality Gates (Connector, Normalization/Knowledge, Analytics, Coach, Validator, End-to-End), with Gate 5 (Validator) explicitly called out as needing adversarial testing, not just normal-case testing.
- Realized concretely during implementation as the practice of self-review before declaring any epic complete (Epic 0, Epic 1, Epic 6, Epic 7) — a direct behavioral consequence of this ADR, not a separate habit.
