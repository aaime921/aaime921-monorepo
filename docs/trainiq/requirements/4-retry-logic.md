# Requirements: Retry Logic for Eufy Scale Connector on Transient Errors

**Issue:** #4
**Test Issue for BA Pilot — Full Pipeline BA→QA Flow**

## Summary

The BO wants confidence that the Eufy connector handles transient upstream
failures (HTTP 5xx, HTTP 429) gracefully, retrying with exponential backoff
before giving up, rather than failing a whole sync run on a single blip.

**Ground truth from the existing codebase (evidence-based principle):**
most of this already exists, not as Eufy-specific code, but as a generic
mechanism every connector already goes through:

- `EufyConnector._login()` and `EufyConnector.download()`
  (`trainiq/connectors/eufy.py`) already classify HTTP 429 and HTTP 5xx
  responses as retryable, raising `TransientError` (never a bare
  exception) — and already honor a provider `Retry-After` header on 429
  via `retry_after_s` (ADR-037), exactly like the Strava connector. This is
  covered by existing tests: `test_login_rate_limited_raises_transient_error_with_retry_after`,
  `test_login_server_error_raises_transient_error_without_retry_after`,
  `test_download_rate_limited_raises_transient_error_with_retry_after`.
- `SynchronizationEngine._with_retries()` (`trainiq/sync/engine.py`)
  already wraps every connector's `download()` call in a generic retry
  loop: exponential backoff (`backoff_base_s=1.0`, so delays double each
  attempt: 1s, 2s, 4s, ...) up to `max_retries=3` attempts, deferring to a
  provider's `retry_after_s` hint when present. This is NOT Eufy-specific
  — it already applies automatically to every registered connector.
- When retries are exhausted, `_with_retries()` re-raises `TransientError`;
  `SynchronizationEngine.sync_connector()` catches it, transitions the
  connector to `Warning` state, and reports the failure via
  `ConnectorSyncResult.error` (the run as a whole continues per Graceful
  Degradation, ADR-009) — there is no bare `TransientError` that escapes to
  a caller of `run_once()`.

So the real gap against the BO's acceptance criteria is narrower than "add
retry logic": it's (a) dedicated test coverage proving this already-generic
behavior actually holds for the Eufy connector specifically, and (b) two
small discrepancies between the BO's stated criteria and current behavior
(no explicit cap at 8s; retry-count logging is at WARNING, not DEBUG) —
see Open Questions.

## Scope

- Automated test coverage (via `SynchronizationEngine`, using the existing
  fake-session pattern already used in `test_eufy_connector.py`) proving,
  specifically for `EufyConnector`:
  - A download that raises `TransientError` (5xx or 429, no `Retry-After`)
    some number of times and then succeeds is retried and the sync
    ultimately succeeds.
  - A download that raises `TransientError` on every attempt exhausts the
    retry budget, ends the run with the connector in `Warning` state, and
    `ConnectorSyncResult.error` populated — the run does not crash and
    other connectors in the same `run_once()` batch are unaffected.
  - The actual delays requested from the injectable `sleep_fn` follow the
    expected exponential sequence, and a provider `Retry-After` value
    (already covered at the connector-unit level) is honored end-to-end
    through the engine too.
- Confirming (via the above tests, not new production code unless the
  Architect determines otherwise) that the existing 5xx/429 classification
  in `EufyConnector` and the existing generic backoff/retry-exhaustion
  behavior in `SynchronizationEngine` together satisfy the BO's stated
  acceptance criteria.
- Possibly in scope, pending Architect decision (see Open Questions): an
  explicit cap on the backoff delay at 8 seconds per attempt, and/or
  moving the per-attempt retry log line to DEBUG level. Both of these, if
  implemented, land in the shared `SynchronizationEngine._with_retries()`,
  not in `EufyConnector` — they would affect every connector (Strava,
  Peloton too), not just Eufy.

### Out of scope

- Live-network verification against a real Eufy account — the CI pipeline
  has no access to real BO accounts (live-verification constraint); this
  must be testable via unit tests / fixture data only.
- Changing the number of retry attempts from the existing default of 3.
- Any change to how authentication failures (401/403) are handled — these
  remain non-retried, per Milestone 4 §3 and ADR-009 (they transition the
  connector's state instead of looping).
- Pagination, normalization, or checkpoint/cursor handling — unrelated to
  this request (see issue #23 for pagination).

## Acceptance criteria

1. **5xx classified as retryable:** `EufyConnector.download()` raises
   `TransientError` for HTTP 5xx responses. *(Already true — verify with a
   test, do not reimplement.)*
2. **429 classified as retryable:** `EufyConnector.download()` raises
   `TransientError` for HTTP 429 responses, honoring a `Retry-After`
   header as `retry_after_s` when present. *(Already true — verify with a
   test, do not reimplement.)*
3. **Exponential backoff, capped:** Absent a provider `Retry-After` hint,
   retries back off exponentially starting at 1s and doubling (1s, 2s,
   4s, ...), capped at a maximum of 8s per attempt. *(The doubling
   behavior already exists in `SynchronizationEngine._with_retries()`; the
   explicit 8s cap does not currently exist as a hard limit — flagged in
   Open Questions.)*
4. **Retry count logged:** Each retry attempt logs the current attempt
   number and the maximum allowed. *(Already true in substance — logged
   via `diagnostic_logger().warning(...)`, including attempt/max_retries;
   whether this should move to DEBUG level is flagged in Open Questions.)*
5. **Retry budget:** Up to 3 retries are attempted (4 total tries) before
   giving up. *(Already the default: `SynchronizationEngine(max_retries=3)`.)*
6. **Exhaustion surfaces as TransientError:** After the retry budget is
   exhausted, the failure is represented as a `TransientError` internally
   (`_with_retries` re-raises it) and the connector's sync run ends with
   the connector in `Warning` state and `ConnectorSyncResult.error` set —
   it must never crash `run_once()` or prevent other connectors in the
   same batch from running. *(Already true — verify with a test.)*
7. **No regression to existing behavior:** All existing `EufyConnector`
   and `SynchronizationEngine` tests continue to pass; authentication
   failures (401/403) remain non-retried.

## Open questions

Neither of these blocks the Architect from proceeding — both have a
reasonable default (keep current behavior, scoped to Eufy's test
coverage) — but they should be explicitly decided rather than assumed,
since both touch the shared `SynchronizationEngine`, used by every
connector, not just Eufy:

1. The BO's acceptance criteria describe an explicit 8s cap on backoff
   delay. The existing generic formula (`backoff_base_s * 2**(attempt-1)`
   with `max_retries=3`) never actually reaches 8s today (sequence is 1s,
   2s, 4s), so the stated criterion happens to hold vacuously. Does the BO
   want a hard cap enforced in `SynchronizationEngine._with_retries()`
   (affecting Strava and Peloton too) for future-proofing, or is the
   current, uncapped formula acceptable as-is since it never manifests
   under current defaults?
2. The BO's acceptance criteria say retry count should be "logged in debug
   output." The existing log line is at WARNING level (arguably correct,
   since it's an anticipated-but-notable condition, not routine debug
   noise) and already includes the attempt count. Should this move to
   DEBUG level specifically, or does WARNING satisfy the intent (visibility
   into retry behavior) well enough?

## Cross-issue dependencies

None — this does not depend on unmerged work from another issue.
