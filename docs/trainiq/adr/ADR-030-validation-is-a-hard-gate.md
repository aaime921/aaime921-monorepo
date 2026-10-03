# ADR-030 — Validation Is a Hard Gate

**Status:** Accepted (Phase 0B, Milestone E — Recommendation Validation)
**Origin:** The Chief Architect's explicit instruction that recommendation verification must be programmatic, not merely prompt-encouraged — "the prompt helps, the validation guarantees."

## Context

A validator that only warns, rather than blocks, tends to be ignored over time and becomes a formality rather than a real safeguard.

## Decision

An unvalidated recommendation is never shown to the user, never persisted, and never used — with no exceptions, including for testing. A validation failure is a rejection, not a warning.

## Consequences

- Directly informed the general engineering practice adopted from Epic 0 onward for backward-incompatible or risky behavior: reject and log, rather than warn and proceed — the same instinct that shaped how Epic 6's malformed-record bug was ultimately fixed (skip and log, never silently pass through).
- The Recommendation Validator's own future correctness (once built) is explicitly the hardest-to-verify property in the whole system, since it requires adversarial testing, not just normal-case testing — a distinction the Tier B roadmap's Quality Gate 5 was built specifically to capture.
