# ADR-037 — Provider Directed Retry Policy

**Status:** Accepted
**Raised during:** Epic 1 self-review (Finding S1), promoted from backlog (BL-004) to ADR per Chief Architect direction.
**Decision owner:** Chief Architect, on Claude's mini-review recommendation.

## Context

Epic 0 designed `TransientError` as a generic signal: "this failed for a reason that might succeed on retry." The Synchronization Engine responds with a fixed exponential backoff (`backoff_base_s * 2^attempt`).

Epic 1 surfaced real evidence this is insufficiently expressive. `stravalib.exc.RateLimitExceeded` carries a `.timeout` attribute — a provider-supplied, authoritative wait time (up to ~900s for Strava's 15-minute rate-limit window), verified against the installed library source, not inferred. The generic backoff (~1s/2s/4s by default) has no way to honor this even if the connector wanted to pass it through, because `TransientError` has no field for it.

This is not a Strava-specific problem. Any provider capable of saying "retry in N seconds" (rate limits with a `Retry-After`-style header being the common case across REST APIs generally) will hit the same gap once its connector is built — most immediately Eufy and Peloton, both already flagged (Milestones 2 and 3) as having worse authentication/rate-limit friction than Strava.

## Decision

`TransientError` gains an optional `retry_after_s: float | None` field. A connector that receives an authoritative wait-time hint from its provider populates this field when raising `TransientError`. The Synchronization Engine's retry logic prefers this value over its own exponential-backoff formula when present; falls back to the existing formula when absent (i.e. for genuine transient errors with no provider-supplied timing — network blips, timeouts).

`max_retries` still bounds the total retry count either way — a provider-directed wait time does not grant unlimited retries, it only changes *how long* each retry waits, not *how many* are attempted.

## Consequences

- **Positive:** connectors can now express "I know exactly how long to wait" distinctly from "something went wrong, try again soon." Every future connector inherits this capability automatically — no per-connector retry logic needs to be reinvented.
- **Positive:** this is an additive change to `TransientError`'s constructor (a new optional field) — no existing call site (the eight `TransientError` usages in `test_sync_engine.py`, or `strava.py`'s own two raise sites) breaks.
- **Neutral:** connectors are not required to populate `retry_after_s` — a connector for a provider with no such signal continues to work exactly as before, unchanged.
- **Risk carried forward, not eliminated:** a buggy or adversarial provider response could in principle specify an excessively long wait. Not mitigated here — flagged as a future hardening candidate only if it's ever observed in practice, per the Constitution's "evidence, not speculation" standard. No cap is imposed today because no evidence yet suggests one is needed.

## Alternatives considered

- **A dedicated `RetryPolicyHint` type, separate from `TransientError`:** rejected as unnecessary indirection — the wait-time hint is a property of a transient failure, not a distinct kind of event requiring its own exception hierarchy.
- **Leave it as backlog, handle per-connector:** rejected per Chief Architect direction — this changes a shared Connector↔Sync Engine contract, which the project's governance model treats as ADR material, not an implementation detail any single epic can decide unilaterally.
