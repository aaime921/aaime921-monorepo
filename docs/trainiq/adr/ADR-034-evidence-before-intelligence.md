# ADR-034 — Evidence Before Intelligence

**Status:** Accepted (Phase 0B, Milestone E — Recommendation Validation, closing Phase 0B)
**Origin:** Synthesis of the entire Phase 0B design phase — the Chief Architect's summary that "the intelligence nasce dall'architettura, non dal modello linguistico."

## Context

By the end of Phase 0B, every individual ADR (016, 019, 022, 025, 026, 027, 030, 031) independently arrived at some version of the same principle from a different angle. This ADR names that convergence explicitly as the project's own identity, rather than leaving it implicit across many separate decisions.

## Decision

Every decision the Coach makes must trace back to a chain of verifiable evidence, built before the language model is ever invoked. The model intervenes only after that chain already exists — it never constructs the chain itself.

## Consequences

- Later adopted as one of the eight founding principles of the TrainIQ Engineering Constitution, in the form "TrainIQ prefers uncertainty over false certainty."
- Functions as the single sentence that could regenerate most of the other Phase 0B ADRs if they were somehow lost — the clearest sign this is a genuine architectural identity, not an arbitrary rule.
