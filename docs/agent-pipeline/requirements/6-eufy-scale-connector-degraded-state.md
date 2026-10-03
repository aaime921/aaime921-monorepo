# Requirements: Graceful degradation for the Eufy scale connector

Issue: #6

## Summary

The BO wants the Eufy scale connector to handle temporary Eufy API
outages gracefully instead of failing outright. When the Eufy API returns
a `503` (temporarily unavailable), the connector should enter a
**Degraded** state that still lets callers read the most recent cached
readings (up to the last 7 days) rather than erroring, should let a user
manually retry rather than wait for the next scheduled poll, and should
record when degradation started. When the Eufy API starts responding
normally again, the connector should return to its normal **Connected**
state on its own. This issue is also explicitly a pipeline smoke test (per
the issue's stated purpose: "Test complete pipeline: BA → Architect → QA,
including Team Lead Router for routing decisions"), so this doc treats the
feature request itself at face value and keeps scope to exactly what's
described in the issue.

Note for the Architect: no "Eufy scale connector" or any connector
abstraction currently exists anywhere in this repository (confirmed by
searching the codebase) — this is new functionality, built from scratch
under `projects/agent-pipeline/`, not a modification of existing code.

## Scope

- A connector component that talks to the Eufy scale API and tracks its
  own connection state, with at least two named states: **Connected**
  (normal operation) and **Degraded** (API temporarily unavailable).
- Detection of a `503` response from the Eufy API as the trigger to
  transition from Connected to Degraded.
- While Degraded, reads continue to succeed by serving the last known-good
  cached readings, limited to data no older than 7 days. Reads for data
  older than 7 days, or when no cached data exists yet, are out of scope
  for "succeeding" — behavior for that edge case is an open question below.
- A manual retry action a user/operator can trigger while Degraded, which
  re-attempts contact with the Eufy API immediately rather than waiting
  for the connector's normal polling interval.
- A log entry recorded at the moment the connector transitions into
  Degraded, including the timestamp of that transition.
- Automatic recovery: once the Eufy API responds normally (non-503) again
  — whether via the normal polling interval or a manual retry — the
  connector transitions back to Connected on its own, without requiring
  any other manual step beyond the retry itself.
- At least one automated test per acceptance criterion below.

### Out of scope

- Any Eufy API error condition other than `503` (e.g. auth failures,
  malformed responses, network timeouts) — this issue is scoped
  specifically to the "temporarily unavailable" case the BO described.
- A UI/front-end for the "manual retry button" — see open questions below
  for what this means at the interface level this project actually has.
- Changing the cache's retention policy beyond what's needed to serve the
  last 7 days while Degraded (e.g. this doc doesn't require purging older
  cached data).
- Any connector other than the Eufy scale connector.
- Alerting/notification on degradation beyond the log entry itself (e.g.
  no requirement here for emails, pages, or dashboards).

## Acceptance criteria

1. When the Eufy API returns a `503` response, the connector transitions
   from Connected to Degraded.
2. While Degraded, reads return cached data from the last 7 days instead
   of failing.
3. While Degraded, a manual retry action is available that immediately
   re-attempts contact with the Eufy API (rather than waiting for the next
   scheduled poll).
4. The moment the connector transitions into Degraded, a log entry is
   recorded that includes the timestamp of that transition.
5. Once the Eufy API responds normally (a non-503 response) again, the
   connector transitions back to Connected automatically, with no manual
   step beyond the retry itself.
6. Automated tests cover: the Connected→Degraded transition on a `503`,
   serving cached data (within 7 days) while Degraded, the manual retry
   path, the degradation log entry, and the Degraded→Connected recovery
   path.
7. All automated tests (existing and new) can be run via a single
   documented command and pass in this environment.

## Open questions

Nothing here blocks handoff to the Architect — these are flagged as
design-level questions for the Architect to resolve, not items that need
the BO first, since the BO's intent is already clear from the issue's
acceptance criteria:

- **"Manual retry button"**: `projects/agent-pipeline/` currently only has
  a CLI entry point (`hello.py`), with no existing UI of any kind. The BO's
  wording ("button") suggests an interactive UI, but nothing in this
  project has one yet. The Architect should decide the concrete form this
  takes in this codebase (e.g. a CLI subcommand/flag that triggers a
  retry) rather than BA assuming a specific implementation.
- **No cached data within 7 days available**: the acceptance criteria
  don't say what a read should do while Degraded if there's no cached
  reading within the last 7 days yet (e.g. a brand-new connector that
  degrades before ever succeeding once). The Architect/Developer should
  pick a defined behavior (e.g. a clear "no data available" result) and
  QA should test it, but it doesn't change the acceptance criteria above
  and doesn't need BO input to proceed.
- **Polling interval**: the normal (non-degraded) polling interval isn't
  specified anywhere in the issue or existing project docs. It doesn't
  block this feature's requirements (the manual retry exists precisely so
  recovery doesn't depend on it), but the Architect/Developer will need to
  pick or confirm one.
