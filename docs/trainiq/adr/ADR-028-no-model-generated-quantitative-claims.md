# ADR-028 — No Model-Generated Quantitative Claims

**Status:** Accepted (Phase 0B, Milestone D — Coach Engine Decision Architecture)
**Origin:** Generalizes the reasoning behind ADR-025 (confidence specifically) to every number an LLM might be tempted to produce — e.g. "80% recovered," "95% ready."

## Context

The risk ADR-025 identified for confidence scores applies identically to any other invented percentage, score, or threshold a language model might generate to sound authoritative.

## Decision

Any numeric value shown to the user must originate from the deterministic engine. The LLM may never invent percentages, scores, thresholds, or probabilities.

## Consequences

- Broadens ADR-025's guarantee from "confidence specifically" to "every number, without exception" — closing an obvious loophole (a model could otherwise satisfy ADR-025 while still inventing other-sounding-authoritative numbers elsewhere in its explanation).
- Intended to be enforced programmatically by the Recommendation Validator (ADR-030), not merely by prompt instruction.
