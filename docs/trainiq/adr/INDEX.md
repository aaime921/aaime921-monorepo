# TrainIQ — Architecture Decision Record Index

**Purpose:** this is the complete ADR register, published into the repository as a Product Owner-designated release blocker ahead of the v1.0 Release Candidate. Before this, ADR-006 through ADR-036 existed only in the design-phase conversation history — reproducible only by someone with access to that conversation. They are now committed as files, exactly as decided, so the design history is as durable as the implementation itself.

**Important provenance distinction:** ADR-006 through ADR-036 were decided during the design phase (Phase 0 and Phase 0B), before any implementation code existed, and are published here as a historical record, not as decisions made now. ADR-037 and ADR-038 were decided *during* implementation, in response to evidence the code itself surfaced — they were committed as files at the time they were made, not backfilled later. Each file's own header states which is true of it.

**Zero ADRs have been reversed.** Several were revised (most visibly ADR-038, across two review rounds) or refined without a new ADR when the change didn't add architectural capability (see BL-005's resolution and ADR-013's own consequences). No ADR has ever been found wrong and discarded.

---

## Phase 0 — Connector & Foundation Architecture

| ADR | Title | Origin |
|---|---|---|
| [006](ADR-006-incremental-polling.md) | Incremental Polling (Not Webhooks) | Milestone 1 (Strava) |
| [007](ADR-007-no-hardcoded-endpoints.md) | No Hardcoded Remote Endpoints | Milestone 1 (Strava) |
| [008](ADR-008-checkpointed-synchronization.md) | Checkpointed Synchronization | Milestone 1 (Strava) |
| [009](ADR-009-graceful-provider-degradation.md) | Graceful Provider Degradation | Milestone 2 (Peloton) |
| [010](ADR-010-provider-state-machine.md) | Provider State Machine | Milestone 2 (Peloton) |
| [011](ADR-011-multiple-acquisition-strategies.md) | Multiple Acquisition Strategies | Milestone 3 (Eufy) |
| [012](ADR-012-connector-capability-tiers.md) | Connector Capability Tiers | Milestone 3 (Eufy) |
| [013](ADR-013-connector-interface-v2.md) | Connector Interface v2 | Milestone 4 (Cross-cutting) |
| [014](ADR-014-scheduler-agnostic-sync-engine.md) | Synchronization Engine Is Scheduler-Agnostic | Milestone 4 (Cross-cutting) |

## Phase 0B — Coach Architecture

| ADR | Title | Origin |
|---|---|---|
| [015](ADR-015-canonical-training-load-strategy.md) | Canonical Training Load Strategy | Milestone A (Normalization) |
| [016](ADR-016-missing-load-policy.md) | Missing Load Policy | Milestone A (Normalization) |
| [017](ADR-017-confidence-propagation.md) | Confidence Propagation | Milestone A (Normalization) |
| [018](ADR-018-derived-metrics-never-source-of-truth.md) | Derived Metrics Are Never Source of Truth | Milestone B (Athlete Knowledge) |
| [019](ADR-019-missing-evidence-never-negative.md) | Missing Evidence Never Becomes Negative Evidence | Milestone B (Athlete Knowledge) |
| [020](ADR-020-athlete-profile-vs-athlete-state.md) | Athlete Profile vs. Athlete State | Milestone B (Athlete Knowledge) |
| [021](ADR-021-minimal-evidence-based-analytics-set.md) | Minimal Evidence-Based Analytics Set | Milestone C (Analytics) |
| [022](ADR-022-no-single-metric-determines-recommendation.md) | No Single Metric Determines a Recommendation | Milestone C (Analytics) |
| [023](ADR-023-no-synthetic-physiology.md) | No Synthetic Physiology | Milestone C (Analytics) |
| [024](ADR-024-evidence-density.md) | Evidence Density | Milestone C (Analytics) |
| [025](ADR-025-confidence-is-deterministic.md) | Confidence Is Deterministic | Milestone D (Coach Engine) |
| [026](ADR-026-llm-as-explainer.md) | LLM as Explainer, Not Decision Authority | Milestone D (Coach Engine) |
| [027](ADR-027-evidence-traceability.md) | Evidence Traceability | Milestone D (Coach Engine) |
| [028](ADR-028-no-model-generated-quantitative-claims.md) | No Model-Generated Quantitative Claims | Milestone D (Coach Engine) |
| [029](ADR-029-decision-provenance.md) | Decision Provenance | Milestone D (Coach Engine) |
| [030](ADR-030-validation-is-a-hard-gate.md) | Validation Is a Hard Gate | Milestone E (Recommendation Validation) |
| [031](ADR-031-no-clinical-inference.md) | No Clinical Inference | Milestone E (Recommendation Validation) |
| [032](ADR-032-conflict-completeness.md) | Conflict Completeness | Milestone E (Recommendation Validation) |
| [033](ADR-033-validation-failure-telemetry.md) | Validation Failure Telemetry | Milestone E (Recommendation Validation) |
| [034](ADR-034-evidence-before-intelligence.md) | Evidence Before Intelligence | Milestone E (closing Phase 0B) |

## Phase 0B → Roadmap Transition

| ADR | Title | Origin |
|---|---|---|
| [035](ADR-035-quality-gates-before-features.md) | Quality Gates Before Features | Roadmap review |
| [036](ADR-036-evidence-package-stable-contract.md) | Evidence Package Is a Stable Internal Contract | Roadmap review |

## Implementation Phase — created as files at the time of decision, not backfilled

| ADR | Title | Origin |
|---|---|---|
| [037](ADR-037-provider-directed-retry-policy.md) | Provider Directed Retry Policy | Epic 1 self-review (Strava) |
| [038](ADR-038-connector-lifecycle-policy.md) | Connector Lifecycle Policy | Epic 3 (Peloton), revised twice before implementation |
| [039](ADR-039-weigh-in-plausibility-flagging.md) | Weigh-In Plausibility Flagging | Issue #38 (Eufy implausible weigh-ins) |

---

## Related records, also part of the durable project history

- `../TrainIQ_Acceptance_Review.md` — Phase 0B development acceptance review (architecture, backlog, ADR consistency, test quality, migration integrity, production readiness).
- `../epic6_self_review.md`, `../epic6_discovery_report.md`, `../epic7_discovery_report.md` — implementation-phase discovery reports and self-reviews.
- `../../BACKLOG.md` — tracked open items, closed items, and documented (non-actionable) system behaviors.

**Committed alongside this archive:** [`../../CONSTITUTION.md`](../../CONSTITUTION.md) — the eight founding principles, each traced to specific ADRs above, per the Product Owner's explicit pre-RC request. The chain `Constitution → ADRs → Implementation → Tests` is now complete within the repository.

**Not yet committed, and flagged as a follow-up beyond this archive:** the full Phase 0/0B milestone reports and Tier A/B Implementation Roadmap documents exist only in the design-phase conversation history. This ADR archive and the Constitution capture the *decisions* and the *principles*; they do not reproduce the underlying *research* behind each one. Not currently designated a release blocker (Product Owner decision) — valuable background, but substantially larger documents than the archive above.
