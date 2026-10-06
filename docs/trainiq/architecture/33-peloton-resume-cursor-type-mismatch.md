# Architecture: Fix Peloton Resume Cursor int/str TypeError

**Issue:** #33
**Requirements:** `docs/trainiq/requirements/33-peloton-resume-cursor-type-mismatch.md`
**Regression of:** `aaime921/trainiq#12` (fix never merged into this monorepo)

## Approach

### Root cause (verified against current `main`, not assumed)

`PelotonConnector.normalize()` (`trainiq/connectors/peloton.py`) sets
`"start_time": start_time`, where `start_time = raw["start_time"]` is
Peloton's raw epoch-**int**. Unlike `StravaConnector.normalize()`, which
maps `"start_time": raw["start_date"]` — already an ISO 8601 **string** —
Peloton's connector never converts this field.

`PelotonConnector.extract_resume_cursor()` (overridden, but only "for
explicitness," per its own comment) returns `normalized.get("start_time")`
verbatim — so for Peloton specifically, this returns an `int`, violating
the `Connector.extract_resume_cursor()` base-class CONTRACT in
`connectors/base.py`, which requires a value "safely orderable via a plain
string `>` comparison."

`SynchronizationEngine._set_checkpoint()` persists this value into
`sync_checkpoints.last_cursor`, a column declared `TEXT` in
`storage/schema.py`. SQLite's TEXT column affinity converts a bound
Python `int` to its text representation *on storage* (not on read) — so
the first sync's in-memory `resume_cursor` (an `int`) round-trips through
SQLite and comes back out of `get_checkpoint()` as a `str` on the *next*
sync. Meanwhile `extract_resume_cursor()` keeps producing a fresh `int`
for every new record, every run. The second sync's comparison —
`candidate_cursor > resume_cursor` in `SynchronizationEngine.sync_connector()`
(`sync/engine.py`) — is therefore `int > str`, which raises exactly the
`TypeError` in the BO's log. This is also directly confirmed by the
existing (passing) test `test_end_to_end_automated_login_sync` in
`tests/test_peloton_connector.py`, which already asserts
`engine.get_checkpoint("peloton") == "1790100644"` — a `str` — immediately
after a sync whose connector only ever produced `int` cursors in memory.

### Fix

Coerce the cursor to `str` at the one place it is *produced* for Peloton —
`PelotonConnector.extract_resume_cursor()` — so every cursor value the
Sync Engine ever sees for this provider (in-memory `candidate_cursor`,
`resume_cursor` carried across records in the same run, the persisted
`last_cursor`, and the value reloaded on the next run) is a `str`, end to
end. This directly satisfies requirements AC1 (one consistent type across
extraction/persistence/reload) and AC2 (works against already-persisted
`str` checkpoints, e.g. the BO's current `'1790883770'`, with no DB
migration — that persisted format is already `str(int)`, which is exactly
what the fixed `extract_resume_cursor()` now also produces).

**Deliberately NOT changed:** `normalize()`'s `"start_time"` field stays a
raw `int`. The textbook-cleaner fix would also convert `start_time` to an
ISO 8601 string in `normalize()` (matching Strava, and matching
`normalized_activities.start_time`'s `TEXT NOT NULL` schema declaration,
which Peloton currently violates in spirit by writing a Python `int`
there). But requirement AC6 is explicit: **all existing Peloton unit
tests must keep passing**, and several already assert `normalize()`'s
`start_time` as a raw `int` against real captured fixture data
(`test_normalize_maps_cycling_class_with_power`,
`test_normalize_logs_unrecognized_discipline_without_dropping_the_record`,
and both end-to-end tests in `test_peloton_connector.py`). Changing
`normalize()`'s output shape would break those tests and would be
reinterpreting the Normalization Engine's existing `RawActivity` contract
for this connector — out of scope for a cursor-type bug fix. See
"Risks/tradeoffs" below for the resulting known inconsistency and why it's
being knowingly deferred, not silently ignored.

The temporary `DEBUG CURSORS` diagnostic line in `sync/engine.py` (~line
503) is lowered from `.error(...)` to `.debug(...)` — same call, same
arguments, only the log level changes (AC4). It stays in place (not
deleted) since it is generically useful for diagnosing the same class of
issue on any future connector, and ADR-038/the Sync Engine's own
conventions already distinguish diagnostic-vs-summary logging by level,
not by presence/absence of a line.

## Affected components/files

- `projects/trainiq/trainiq/connectors/peloton.py` — `extract_resume_cursor()`
  only. No change to `authenticate()`, `download()`, or `normalize()`.
- `projects/trainiq/trainiq/sync/engine.py` — one log-level change
  (`.error` → `.debug`) on the existing `DEBUG CURSORS` line inside
  `sync_connector()`. No change to any comparison, persistence, or
  control-flow logic — the Sync Engine itself remains provider-agnostic
  and is not the place where the type mismatch is fixed, since the bug is
  Peloton-specific cursor production, not a generic engine defect (Strava
  and Eufy already produce/consume `str` cursors correctly).
- `projects/trainiq/tests/test_peloton_connector.py` — new tests only
  (see "Test strategy notes"). No existing test is modified.

Nothing in `trainiq/storage/schema.py`, `trainiq/normalization/engine.py`,
or any other connector changes. No new migration is needed — AC2
(handling existing `str`-persisted checkpoints) is satisfied by the fact
that the fixed code now produces the same string format
(`str(<epoch-seconds>)`) the database already holds.

## Interfaces/contracts

```python
# trainiq/connectors/peloton.py — PelotonConnector

def extract_resume_cursor(self, normalized: dict[str, Any]) -> str | None:
    """Coerces Peloton's raw epoch-int `start_time` to `str` so it matches
    the type `sync_checkpoints.last_cursor` already round-trips as
    (SQLite TEXT affinity) — the Sync Engine's `candidate_cursor >
    resume_cursor` comparison must never mix `int` and `str` (issue #33).
    `normalize()`'s own `start_time` field is intentionally left as a raw
    int elsewhere; only the cursor value returned from here is coerced.
    """
    start_time = normalized.get("start_time")
    return str(start_time) if start_time is not None else None
```

No signature changes anywhere — the base class already declares
`extract_resume_cursor(...) -> str | None`; Peloton's override simply now
honors that return type correctly for every value it returns, not just
`None`.

```python
# trainiq/sync/engine.py — SynchronizationEngine.sync_connector(), ~line 503
# Before:
diagnostic_logger().error(
    "DEBUG CURSORS: candidate={!r} ({}) resume={!r} ({})",
    candidate_cursor, type(candidate_cursor).__name__,
    resume_cursor, type(resume_cursor).__name__,
)
# After (same call, lower level only):
diagnostic_logger().debug(
    "DEBUG CURSORS: candidate={!r} ({}) resume={!r} ({})",
    candidate_cursor, type(candidate_cursor).__name__,
    resume_cursor, type(resume_cursor).__name__,
)
```

## Task breakdown

1. In `trainiq/connectors/peloton.py`, change `PelotonConnector.extract_resume_cursor()`
   to coerce its return value to `str` (see Interfaces/contracts above).
   Do not touch `normalize()`.
2. In `trainiq/sync/engine.py`, change the `DEBUG CURSORS` log call's level
   from `.error` to `.debug`. No other edits to `engine.py`.
3. Add a direct unit test for `extract_resume_cursor()`: given a
   `normalized` dict with `"start_time": 1790014244` (int), it returns
   `"1790014244"` (str); given `"start_time": None`, it returns `None`.
4. Add an end-to-end regression test modeled on the existing
   `test_end_to_end_automated_login_sync`, reproducing the exact reload
   path from the BO's log:
   - Seed `sync_checkpoints` for `provider='peloton'` with
     `last_cursor = '1790883770'` (str — reproducing an already-persisted,
     pre-fix-shaped row, per AC2) and `strategy='default'`.
   - Run one sync (`engine.run_once([connector])`) whose downloaded batch
     includes a workout with `start_time = 1791134056` (the log's own
     candidate value, per AC3).
   - Assert the run completes without raising, `connector_results[0].state
     == ConnectorState.HEALTHY`, and `engine.get_checkpoint("peloton") ==
     "1791134056"`.
5. Add a second end-to-end test covering two *consecutive* syncs in the
   same test (no pre-seeded row this time — start from a fresh DB) to
   cover the full extraction → persist → reload cycle AC3 asks for
   explicitly: first sync downloads a workout with `start_time =
   1790883770`, asserts the resulting checkpoint is `"1790883770"`; second
   sync (same `db`, same or a fresh `PelotonConnector` instance against
   that `db`) downloads a workout with `start_time = 1791134056`, asserts
   it completes with `ConnectorState.HEALTHY` (not `DEGRADED`) and the
   checkpoint advances to `"1791134056"`.
6. Run the full existing Peloton and Sync Engine test suites
   (`tests/test_peloton_connector.py`, `tests/test_sync_engine.py`) and
   confirm zero regressions — in particular the `normalize()`-only tests
   that assert `result["start_time"]` as a raw int must be untouched and
   still pass.

## Test strategy notes

- Unit-level: `extract_resume_cursor()` in isolation (step 3) is the
  cheapest possible regression guard for the actual root cause — no DB,
  no connector plumbing.
- Integration-level: steps 4–5 exercise the real `SynchronizationEngine`
  + real SQLite schema, which is where the original bug only manifested
  (a unit test of `extract_resume_cursor()` alone would not have caught
  the affinity-driven persist/reload round trip). Use the issue's own
  logged values (`1791134056`, `'1790883770'`) as fixture data per AC3 so
  the test is traceably tied to the reported defect, not a synthetic one.
- No live-account testing is in scope (per requirements' explicit
  exclusion and `tests/test_peloton_connector.py`'s existing
  `FakePelotonSession` convention) — all new tests use the same fake
  session / in-memory SQLite pattern already established in that file.
- Do not add a test asserting on the `DEBUG CURSORS` log's level — no
  existing test does, and doing so would couple a test to log wording
  rather than behavior.

## Risks/tradeoffs

- **Known, accepted limitation inherited from the base class's own
  documented contract, not introduced by this fix:** a plain `str(int)`
  epoch-seconds cursor is only safely orderable via lexicographic `>`
  comparison while every value has the same digit count. Unix epoch
  seconds are 10 digits from 2001-09-09 through 2286-11-20, so this holds
  for the practical lifetime of this project. `base.py`'s own
  `extract_resume_cursor()` docstring already flags this exact class of
  risk ("An unpadded one... does NOT [satisfy the ordering contract]").
  Not fixed here (e.g. by zero-padding to a fixed width) because it would
  change the persisted cursor *format*, which would then fail to match
  the BO's already-persisted `'1790883770'`-shaped rows — directly
  contradicting AC2. If this ever needs hardening, it is a separate,
  deliberately-scoped change, not a silent addition to this fix.
- **Known, pre-existing, out-of-scope inconsistency, now explicit rather
  than accidental:** `normalized_activities.start_time` is declared `TEXT
  NOT NULL` in `storage/schema.py`, but `PelotonConnector.normalize()`
  continues to write a Python `int` into that field (via
  `build_canonical_record()` → `_upsert_normalized_activity()`), relying
  on the same SQLite TEXT-affinity conversion this whole bug was about.
  This doesn't crash (there's no `int`/`str` comparison against this
  particular column anywhere) and today's fix doesn't touch it, per AC6's
  constraint that existing `normalize()`-level tests must keep asserting
  an `int`. Worth a future, separately-scoped cleanup issue (converting
  `normalize()`'s `start_time` to ISO 8601 for Peloton and updating the
  handful of tests that currently assert an int) — flagging this
  explicitly rather than letting a future reader assume it was already
  handled.
- **Scope check against other connectors:** Strava's `normalize()` already
  returns an ISO 8601 string for `start_time` (`raw["start_date"]`), and
  its `extract_resume_cursor()` uses the base class default unmodified —
  already consistent, not affected by or relevant to this fix. Eufy
  overrides `extract_resume_cursor()` for its own `WeighIn`-shaped
  (`timestamp`-keyed) records — a separate code path, not touched here.
  Per the requirements doc's explicit scope boundary, no other connector
  is touched by this change even though the *pattern* (an unconverted raw
  numeric field feeding a cursor) could theoretically recur in a future
  connector; that would be a new issue, not an expansion of this one.

## Recovery note for the BO (requirement AC5)

The BO's local `connector_state` row for `provider='peloton'` is currently
`state='Degraded'` (per the diagnostic log, entered on or before
2026-10-04). **No manual DB fix or reset is required.** ADR-038's
lifecycle policy (`sync/lifecycle_policy.py`) already retries a `Degraded`
connector automatically on its existing backoff schedule (1, 2, 4, then
every 7 days, timed from `last_attempt_at`) — this fix does not change
that policy. The crash this issue describes happens *after* a successful
`authenticate()` call, during per-record cursor tracking, so once this fix
ships, the very next *eligible* automated attempt will download and
process records without raising, and `sync_connector()` will call
`connector.transition_state(ConnectorState.HEALTHY, ...)` — a legal
`Degraded → Healthy` transition — self-healing the connector with zero BO
action needed.

**One timing caveat:** ADR-038 escalates a connector that has sat
continuously `Degraded` for `DEGRADED_ESCALATION_THRESHOLD_DAYS` (10 days,
tracked from `connector_state.state_entered_at` for `provider='peloton'`)
to `RecoveryRequired` — which *would* then require the BO to manually
extract and submit a Peloton bearer token (Feature 3.2,
`submit_manual_recovery()`) rather than self-healing. The BO should get
this fix running (so at least one eligible sync attempt occurs) before 10
days have elapsed since the connector first entered `Degraded`, to avoid
that extra manual step. If the BO wants to force an immediate retry rather
than waiting for the next eligible day on the backoff schedule, the only
way to do that today is to directly reset `last_attempt_at` and
`attempt_count_in_state` to `0`/`NULL` for the `peloton` row in
`connector_state` — this is an existing, general ADR-038 property, not
something this fix adds or needs to add tooling for.
