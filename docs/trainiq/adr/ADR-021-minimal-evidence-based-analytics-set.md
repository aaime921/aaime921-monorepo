# ADR-021 — Minimal Evidence-Based Analytics Set

**Status:** Accepted (Phase 0B, Milestone C — Analytics Engine)
**Origin:** Research comparing commercially common fitness metrics against what TrainIQ's actual providers can honestly support.

## Context

Analytics products tend to accumulate metrics because competitors show them, not because the evidence or the available data supports them (e.g. VO2max estimation, HRV readiness — neither of which any TrainIQ provider supplies).

## Decision

TrainIQ V1 uses a restricted metric set that satisfies three criteria simultaneously: sufficient scientific evidence, genuinely available data, and direct impact on Coach decisions. New metrics must satisfy the same three criteria before entering the core set.

## Consequences

- The core set landed on exactly four metrics: CTL/ATL/TSB, ACWR, Training Monotony & Strain, Ramp Rate.
- VO2max, HRV readiness, sleep readiness, and weight-performance modeling were explicitly excluded, not merely deferred — see ADR-023.
