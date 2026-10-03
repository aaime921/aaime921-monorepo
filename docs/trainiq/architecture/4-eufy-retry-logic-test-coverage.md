# Architecture: Eufy connector retry-on-transient-error test coverage

Issue: #4
Requirements: [`docs/trainiq/requirements/4-retry-logic.md`](../requirements/4-retry-logic.md)

## Approach

The requirements doc's evidence-based finding is confirmed by reading the
code directly: `EufyConnector.download()`/`_login()`
(`trainiq/connectors/eufy.py`) already raise `TransientError` for HTTP 5xx
and 429 (honoring a provider `Retry-After` header as `retry_after_s` on 429,
per ADR-037), and `SynchronizationEngine._with_retries()`
(`trainiq/sync/engine.py`) already wraps every connector's `download()` call
in a generic exponential-backoff retry loop (`backoff_base_s=1.0`,
`max_retries=3`: delays 1s, 2s, 4s), re-raising `TransientError` on
exhaustion. `sync_connector()` catches that re-raised `TransientError`,
transitions the connector to `Warning`, and populates
`ConnectorSyncResult.error` — never letting it escape `run_once()` and never
blocking other connectors in the same batch (Graceful Degradation, ADR-009).

So this design adds **no new production code**. It adds one thing: Eufy-
specific end-to-end test coverage proving the already-generic
engine-level retry/backoff/exhaustion behavior genuinely holds when driven
through a real `EufyConnector` instance (not just the generic
`MockFlakyThenHealthyConnector`/`MockRateLimitedConnector` fakes already
exercising this mechanism in `test_sync_engine.py`), closing the gap the BA
identified: dedicated Eufy coverage didn't exist yet, only connector-level
unit tests (`test_login_rate_limited_raises_transient_error_with_retry_after`
etc., which only prove `EufyConnector` raises the right exception shape, not
that the engine actually retries/backs off/gives up around it end-to-end).

**Open question 1 (explicit 8s backoff cap) — decision: no change.** The
generic formula (`backoff_base_s * 2**(attempt-1)`, `max_retries=3`) never
reaches 8s today (1s, 2s, 4s), so the BO's stated criterion holds vacuously.
Adding a hard cap would touch `SynchronizationEngine._with_retries()`,
affecting Strava and Peloton too, for a scenario that cannot currently
occur under any connector's configured `max_retries`. Per the requirements
doc's own stated default ("keep current behavior, scoped to Eufy's test
coverage"), and since this is a test-coverage-pilot issue, not a mandate to
future-proof the shared engine, the safer, narrower choice is to leave the
formula uncapped and document this decision rather than widen the blast
radius of the change. If the BO later raises `max_retries` enough that 8s
would actually be exceeded, that's a new, separate issue against the shared
engine.

**Open question 2 (retry-count log level) — decision: no change.** The
existing line (`diagnostic_logger().warning(...)`, including attempt number
and `max_retries`) already satisfies the substance of AC4 (visibility into
retry behavior); WARNING is arguably the more correct level for an
anticipated-but-notable condition, and moving it to DEBUG is a logging-policy
change to the shared engine with no test-coverage benefit. Kept as-is.

Both decisions are non-blocking per the requirements doc and are recorded
here rather than guessed silently, satisfying the BA's open questions
without a `needs:human`/`needs:routing` detour.

## Affected components/files

- `tests/test_eufy_connector.py` — extend `FakeEufySession` to support
  scripting a *sequence* of GET responses (needed to simulate "fails N
  times, then succeeds" across repeated `download()` calls inside one
  retry loop); add the new end-to-end retry tests in a new section.
- No changes to `trainiq/connectors/eufy.py` or `trainiq/sync/engine.py`
  (see Approach — both open questions resolve to "no change").
- No changes to any other connector's tests (Strava/Peloton) — this issue
  is explicitly scoped to Eufy-specific coverage of an already-generic
  mechanism; the generic mechanism itself already has its own tests in
  `test_sync_engine.py` and is not being re-verified here.

## Interfaces/contracts

`FakeEufySession` (`tests/test_eufy_connector.py`) currently exposes only a
single scripted response per verb (`script_get_response(FakeResponse)` /
`script_post_response(FakeResponse)`), returned unconditionally on every
call — sufficient for existing tests (one login, one download), but not for
a test that needs `download()` to return different responses across
successive calls within one `_with_retries()` loop. Add an additive,
backward-compatible sequence option:

```python
class FakeEufySession:
    def __init__(self):
        self.post_calls: list[dict] = []
        self.get_calls: list[dict] = []
        self._post_response: FakeResponse | None = None
        self._get_response: FakeResponse | None = None
        self._get_response_queue: list[FakeResponse] = []

    def script_post_response(self, response: FakeResponse):
        self._post_response = response

    def script_get_response(self, response: FakeResponse):
        self._get_response = response

    def script_get_responses(self, responses: list[FakeResponse]):
        """Scripts a sequence: each .get() call pops the next response.
        Once exhausted, falls back to the single-response behavior (or
        raises if neither was ever scripted) — used for retry tests where
        download() is called more than once with different outcomes per
        attempt."""
        self._get_response_queue = list(responses)

    def get(self, url, params=None, headers=None):
        self.get_calls.append({"url": url, "params": params, "headers": headers})
        if self._get_response_queue:
            return self._get_response_queue.pop(0)
        return self._get_response
```

`post()` is unchanged — every new test only needs one scripted login
response (success), since auth failures/retries during login are already
covered by existing tests (`test_login_rate_limited_raises_transient_error_with_retry_after`,
`test_login_server_error_raises_transient_error_without_retry_after`) and
are explicitly out of scope here (the requirements doc's scope is
`download()`-path retries driven through the engine).

No changes to `EufyConnector`'s or `SynchronizationEngine`'s public
signatures.

## Task breakdown

1. Add `_get_response_queue` and `script_get_responses()` to
   `FakeEufySession` as shown above. Confirm every existing test in
   `tests/test_eufy_connector.py` still passes unmodified (the queue is
   empty unless a test opts in, so `.get()` falls through to the existing
   `_get_response` field exactly as before).
2. Add a new section to `tests/test_eufy_connector.py` (e.g. "--- Feature
   2.1b retry/backoff via SynchronizationEngine (issue #4) ---") with a
   shared pattern: a `credential_store` with email/password set, a single
   scripted login success, `script_get_responses([...])` for the download
   sequence, a real `EufyConnector(credential_store, device_id=..., session=fake)`,
   and `SynchronizationEngine(db, backoff_base_s=0.01 or 1.0,
   sleep_fn=sleeps.append)` (0.01 keeps the test fast while still letting
   assertions check the *ratio*/sequence; use `1.0` with `sleeps.append`
   if asserting exact 1.0/2.0 values matters more than speed — match the
   existing convention in `test_sync_engine.py`, which uses `0.01` for
   speed and asserts relative values).
3. **AC1/AC3/AC5 — 5xx retried with exponential backoff, then succeeds:**
   script two `FakeResponse(500)` followed by a 200 with a valid data
   body. Run `engine.run_once([connector])`. Assert:
   - `result.connector_results[0].state == ConnectorState.HEALTHY`
   - `result.connector_results[0].error is None`
   - `sleeps == [1.0, 2.0]` (or the scaled equivalent for the chosen
     `backoff_base_s`) — proves the *exact* doubling sequence, not just
     "it eventually succeeded."
   - `len(fake.get_calls) == 3` — proves the retry loop actually re-called
     `download()`, not that `EufyConnector` itself looped internally.
4. **AC2/AC3/AC5 — 429 (no `Retry-After`) retried with the same generic
   backoff, then succeeds:** same shape as step 3, but with
   `FakeResponse(429, {})` (no `Retry-After` header) for the failing
   attempts. Assert the same delay sequence — proves 429-without-a-hint
   falls back to the identical generic formula as 5xx, not a special case.
5. **AC2 — 429 `Retry-After` honored end-to-end through the engine:**
   script one `FakeResponse(429, {}, headers={"Retry-After": "7"})`
   followed by a 200. Assert `sleeps == [7.0]` (the provider-directed
   value, per ADR-037 — *not* the generic `1.0` the formula would have
   produced for attempt 1), and the sync still ends `HEALTHY`. This is the
   one case the requirements doc calls out as "already covered at the
   connector-unit level" but not proven end-to-end through the engine —
   this test closes exactly that gap.
6. **AC5/AC6 — retry budget exhausted, surfaces as `Warning` +
   `ConnectorSyncResult.error`, batch continues:** script 4 consecutive
   `FakeResponse(500)` responses (more than `max_retries=3` can absorb).
   Run `engine.run_once([eufy_connector, other_healthy_connector])` with a
   second, trivially-healthy mock connector (reuse
   `MockHealthyConnector` from `test_sync_engine.py` if importable, or an
   equally simple local fixture) in the same batch. Assert:
   - `eufy_result.state == ConnectorState.WARNING` (never `DEGRADED` —
     `TransientError` always routes to `Warning` regardless of lifecycle
     `decision`, per `sync_connector()`'s `except TransientError` branch)
   - `eufy_result.error` is set and mentions the transient failure
   - `len(fake.get_calls) == 4` (initial attempt + 3 retries, matching
     `max_retries=3`)
   - the second connector's result is still `HEALTHY` with its records
     upserted — proves the Eufy failure didn't crash `run_once()` or block
     the rest of the batch (ADR-009).
7. **AC7 — no regressions:** run the full `tests/test_eufy_connector.py`
   and `tests/test_sync_engine.py` suites and confirm everything passes
   unchanged, including the pre-existing connector-level 401/403
   non-retry tests (`test_login_rejected_returns_false`) and the generic
   engine-level retry tests in `test_sync_engine.py` — neither is modified
   by this issue.
8. Do not touch `trainiq/connectors/eufy.py` or `trainiq/sync/engine.py` —
   both open questions resolve to "no production change" (see Approach).

## Test strategy notes

- All new coverage is engine-driven integration tests (`EufyConnector` +
  `FakeEufySession` + real `SynchronizationEngine` against a real
  in-memory/temp-file `sqlite3` db via the existing `db`/`credential_store`
  fixtures) — no live Eufy account access is needed or possible in CI
  (testing-scope-boundary constraint, already established in the
  Architect role doc).
- Exact delay-sequence assertions (`sleeps == [1.0, 2.0]`, `sleeps ==
  [7.0]`) are deliberately stronger than "eventually succeeds" — they're
  what actually proves AC3 (the doubling formula) and AC2's `Retry-After`
  precedence, rather than merely proving retries happen at all.
- Asserting `len(fake.get_calls)` on each path makes the attempt count
  explicit and directly verifies AC5 (3 retries / 4 total tries) without
  relying on log output.
- Retry-count *logging* (AC4) is already satisfied by the existing
  `diagnostic_logger().warning(...)` call and is not the focus of new
  tests here (see Open question 2 decision) — if QA wants an explicit
  assertion, `caplog` can confirm the WARNING-level message contains the
  attempt/max_retries values, but this is optional polish, not a new
  acceptance criterion.
- The multi-connector batch test (step 6) is the one new test that goes
  slightly beyond "Eufy in isolation," because AC6's "other connectors
  unaffected" claim cannot be verified any other way than actually running
  more than one connector through `run_once()`.

## Risks/tradeoffs

- **`FakeEufySession` sequence-scripting is additive, not a rewrite** —
  chosen specifically so every one of the ~15 existing tests using
  `script_get_response()` (singular) keeps working untouched, rather than
  risking a regression across the whole file for the sake of this one
  issue's new tests.
- **No hard backoff cap, by design (Open question 1).** If a future change
  raises `max_retries` on any connector enough to actually reach 8s, the
  stated acceptance criterion would stop holding vacuously — that's a
  deliberate, documented consequence of scoping this issue to test
  coverage only, not a defect introduced here.
- **Log level left at WARNING (Open question 2)** — if the BO specifically
  wants DEBUG-level retry logging for noise reasons in production, that is
  a follow-up issue against the shared engine, not something this issue's
  test coverage forces a decision on.
