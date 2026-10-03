# ADR-026 — LLM as Explainer, Not Decision Authority

**Status:** Accepted (Phase 0B, Milestone D — Coach Engine Decision Architecture)
**Origin:** Direct consequence of ADR-025 and the broader Milestone D finding that hallucination-mitigation research consistently favors narrow, grounded model tasks over open-ended reasoning.

## Context

An LLM given raw data and open latitude to "decide" a recommendation inherits every known LLM reliability problem (hallucination, miscalibration) directly into the product's core value proposition.

## Decision

The LLM synthesizes, explains, and contextualizes. It is never the primary source of decision logic. The deterministic pipeline (Evidence Collection → Quality Evaluation → Factor Identification) constructs the decision; the LLM renders it as natural language.

## Consequences

- Established the "Evidence-Based Coaching System with LLM-assisted Communication" framing the Chief Architect adopted as TrainIQ's own definition of itself.
- Makes the architecture stable across LLM vendor/version changes — swapping the underlying model changes only the narrator, never the decision logic.
