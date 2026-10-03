# Requirements: Eufy scale connector graceful degradation

Issue: #6

## Summary

The BO wants the Eufy smart-scale connector to degrade gracefully instead of
failing outright when the Eufy API is temporarily unavailable. When the API
returns a 503, the connector should move into a distinct "Degraded" state
that keeps serving the user's own recently-cached scale readings (the last 7
days), records when the degradation happened, offers a way to manually
retry, and returns to normal ("Connected") operation on its own once the API
is healthy again. This issue is explicitly a pipeline smoke test (per the
issue's stated purpose: exercising BA → Architect → QA plus the Team Lead
Router), so the acceptance criteria below are taken directly from the
issue's own criteria, made concrete enough for QA to check pass/fail.

## Scope

- A connector-level connection state with at least two values: `Connected`
  and `Degraded`.
- Detecting that the Eufy API is unavailable specifically via an HTTP 503
  response, and transitioning the connector from `Connected` to `Degraded`
  when that happens.
- While `Degraded`, reads are served from locally cached scale data limited
  to the last 7 days; data older than 7 days is not surfaced while in this
  state.
- A manual retry action, available while `Degraded`, that re-attempts
  contact with the Eufy API.
- Returning to `Connected` automatically the next time the Eufy API responds
  normally (not only as a direct result of the manual retry — e.g. a
  background/periodic check succeeding should also clear `Degraded`).
- A log entry for each transition into `Degraded`, including the timestamp
  the degradation was detected.

### Out of scope

- Eufy API authentication, pairing, or initial connector setup/onboarding —
  not mentioned by the BO and assumed already handled or irrelevant to this
  behavior.
- Handling of error conditions other than a 503 response (e.g. timeouts,
  connection errors, other 5xx codes) — the issue names 503 specifically, so
  these criteria are scoped to that; broader transient-failure handling
  would need a separate decision (see "Open questions").
- The concrete mechanism/UI for the "manual retry" action (CLI flag, API
  call, literal UI button, etc.) — that's an implementation choice for the
  Architect/Developer, not a requirement.
- Cache storage/eviction mechanics beyond the 7-day visibility rule itself.

## Acceptance criteria

1. When the Eufy API returns an HTTP 503 response, the connector transitions
   from `Connected` to `Degraded`.
2. While in `Degraded` state, the connector can still return previously
   cached scale readings from the last 7 days; readings older than 7 days
   are not returned from the cache while `Degraded`.
3. While in `Degraded` state, a manual retry action is available that
   re-attempts a call to the Eufy API.
4. Every transition into `Degraded` produces a log entry that includes the
   timestamp the degradation was detected.
5. When the Eufy API subsequently responds successfully (a non-503
   response), the connector transitions back to `Connected` — whether that
   successful call was triggered by the manual retry or by a subsequent
   normal/background call to the API.
6. At least one automated test suite covers: entering `Degraded` on a 503,
   serving cached data within the 7-day window while `Degraded`, the manual
   retry action, the logged degradation timestamp, and the return to
   `Connected` on recovery.
7. All automated tests (existing and new) can be run via a single
   documented command and pass in this environment.

## Open questions

None blocking. Two notes for the Architect, neither of which changes the
criteria above:

- There is currently no Eufy scale connector (or any API-connector code) in
  `projects/agent-pipeline` — the project today is a hello-world greeting
  CLI (`src/hello.py`, "name" + `--shout`). This feature introduces the
  connector itself, not an extension of existing connector code, which is
  a larger change than prior issues on this project. Flagging this as scale
  context for the Architect's design, since it's a bigger lift than it
  might look from the acceptance criteria alone.
- The issue's own wording calls the manual-retry affordance a "button",
  which implies a UI this CLI-based project doesn't have. The requirement
  above is written against the *behavior* (a retry action is available and
  re-attempts the API call), leaving the concrete form (CLI flag, function,
  etc.) to the Architect/Developer, consistent with "describe what, not
  how."
