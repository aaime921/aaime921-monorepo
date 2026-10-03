# ADR-036 — Evidence Package Is a Stable Internal Contract

**Status:** Accepted (Phase 0B → Implementation Roadmap transition)
**Origin:** The Chief Architect's observation, on reviewing the completed Tier B roadmap, that nearly the entire system converges on one object — the Evidence Package (Analytics Engine's output, the Coach's input, the Validator's reference) — making it the project's true center of gravity, more so than the Coach itself.

## Context

An object that many independent subsystems depend on needs the same discipline as an external API; treating it as an incidental implementation detail of one component (the Coach) risks careless, uncoordinated changes to its shape.

## Decision

The Evidence Package is the stable internal contract between the Analytics Engine, Coach Engine, and Recommendation Validator. Any incompatible change to its shape is a breaking change, requiring the same care as ADR-013's Connector interface changes.

## Consequences

- Not yet implemented as of this ADR archive's publication (Epic 9, Coach Engine, has not been built) — recorded here so its schema is versioned and independently tested from the moment it is first implemented, per the Tier B roadmap's explicit note on this ADR.
- Reinforces a pattern already visible elsewhere in the codebase (the Connector interface, the canonical `normalized_activities`/`weigh_ins` schemas): the project consistently treats shared internal data shapes with API-level seriousness, not as incidental plumbing.
