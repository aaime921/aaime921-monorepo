# ADR-031 — No Clinical Inference

**Status:** Accepted (Phase 0B, Milestone E — Recommendation Validation)
**Origin:** Overtraining-syndrome research reviewed during Milestone E found that most clinically recognized warning signs (mood change, illness, injury, hormonal markers) require data sources TrainIQ does not and cannot have.

## Context

TrainIQ can compute a narrow set of proxies (deeply negative TSB, elevated ramp rate, high monotony in combination) that the literature treats as a caution signal — but that is not the same as detecting overtraining, illness, or injury, which are clinical judgments.

## Decision

TrainIQ never formulates diagnoses, medical assessments, or clinical inferences. It communicates only what is directly supported by the data it actually has, defaulting to conservative language ("signals suggest reduced intensity") rather than diagnostic claims, however extreme the underlying computed values.

## Consequences

- Sets a hard boundary the Recommendation Validator (ADR-030) is expected to enforce by scanning for diagnostic language, not just trusting the prompt.
- Establishes that TrainIQ's honest self-description is "a narrow, evidence-based caution system," never "a health-monitoring or diagnostic tool" — relevant to both product framing and legal/safety posture.
