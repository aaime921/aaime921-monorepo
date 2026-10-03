# ADR-027 — Evidence Traceability

**Status:** Accepted (Phase 0B, Milestone D — Coach Engine Decision Architecture)
**Origin:** Extends the Chief Architect's "grounding" proposal into a stronger, explicitly named requirement after Milestone D's review.

## Context

Grounding an LLM's output in retrieved context reduces hallucination, but "grounded" alone doesn't guarantee every specific claim can be checked — only that context was available to the model.

## Decision

Every significant statement in a recommendation must be traceable to one or more specific elements of the structured Evidence Package. Explanations cite evidence by reference, not by paraphrase alone.

## Consequences

- Sets up the concrete mechanism later implemented as a hard validation gate in Milestone E (ADR-030): traceability becomes something a validator checks programmatically, not something merely encouraged by a prompt.
- Directly named as the property `test_...` style regression tests are expected to verify once the Coach Engine is built (Epic 9, not yet implemented as of this ADR archive's publication).
