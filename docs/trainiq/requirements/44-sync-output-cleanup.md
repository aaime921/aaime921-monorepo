# Requirements: Sync Output Cleanup (noisy re-normalize logging, missing flagged count, duplicate summary lines)

**Issue:** #44
**Related:** #25 (console summary), #36 (re-normalization script), #38 (flagged count), #42 (over-flagging bug this noise hid)

## Summary

During the BO's first full run after #36/#37/#38 (2026-10-08), three output problems surfaced. None affects stored data, but together they make the console output unreliable as a source of truth and hid a real bug (#42) that was visible in `summary.log` but not on screen. The BO wants: (1) the per-record "training_load unknown" message to stop flooding the console when re-normalizing, (2) the terminal summary line to show the same `flagged N implausible` count that `summary.log` already shows, and (3) each connector's summary written to `summary.log` exactly once per run instead of twice.

**Evidence found while scoping (cited for the Architect, not prescribing the fix):**
- `scripts/renormalize_strava_unofficial.py` never calls `trainiq.logging_setup.configure()`. Without it, loguru's default handler (added automatically, stderr, no filter) is never removed, so every `diagnostic_logger().info(...)` call — including the one in `trainiq/normalization/load.py`'s `_unknown()`, invoked once per record via `compute_training_load()` inside `build_canonical_record()` — prints straight to the terminal, once per row re-normalized.
- `trainiq/sync/engine.py`'s `SynchronizationEngine.sync_connector()` (around line 617) logs the per-connector summary line directly via `summary_logger().info(...)`, including `flagged {flagged_implausible_count} implausible`. This line reaches `summary.log` but is never captured into anything `trainiq/app.py`'s `main()` prints to the console.
- `trainiq/app.py`'s `_log_sync_summary()` (lines 160–173) runs afterward, reading the same `ConnectorSyncResult` and logging a *second* summary line per connector through its own `_Reporter` — this second line omits `flagged` entirely, and it's this second line's text that `main()` prints to stdout (`for line in sync_reporter.lines: print(line)`). Both the engine's line and `_log_sync_summary`'s line go to `summary.log`, producing the duplicate.

## Scope

- Console output of `scripts/renormalize_strava_unofficial.py` (and any other one-off script performing bulk re-normalization): the "training_load unknown / athlete_profile not available" message must not print once per record to the console. Per-record detail, if kept, belongs only in the diagnostic log at DEBUG level.
- The terminal summary line printed by `trainiq` (`trainiq/app.py`) for each connector: must include `flagged N implausible`, matching `summary.log`'s existing per-connector line exactly.
- `summary.log` content: each connector's sync summary must appear exactly once per run, not twice.
- Tests asserting the terminal summary includes the flagged count, and that a sync run writes each connector's summary line to `summary.log` exactly once.

### Out of scope

- Any change to the flagging logic itself or the over-flagging bug it hid (#42) — tracked separately.
- Any change to `training_load`/TRIMP/TSS computation (#epic 6) — only where/how its "unknown" reason is logged is in scope, not the computation.
- Any change to what counts get computed (`inserted`/`updated`/`malformed`/`skipped`/`flagged`) — all of these already exist on `ConnectorSyncResult`; this issue is about making the two output surfaces (console, `summary.log`) consistent and non-duplicated, not adding new counters.
- Live-account verification — not available to the pipeline; must be testable via fixtures/log capture (capsys/loguru capture), per the issue's own acceptance criteria.

## Acceptance criteria

1. Running `scripts/renormalize_strava_unofficial.py` (or any script that performs bulk re-normalization via `build_canonical_record()`/`compute_training_load()`) against data with no `AthleteProfile` prints the "training_load unknown — athlete_profile not available" message at most once to the console for the whole run, regardless of how many records are processed. Per-record detail, if retained, appears only in the diagnostic log at DEBUG level, not on the console and not in `summary.log`.
2. The terminal line `trainiq` prints for each connector's sync result includes `flagged N implausible`, using the exact same count and wording already written to `summary.log` for that connector.
3. For a single run, `summary.log` contains exactly one summary line per connector (the one with the `flagged` clause) — not two.
4. A test (capsys-based) runs a synchronization with at least one connector result that has `records_flagged_implausible > 0` and asserts the captured stdout contains `flagged N implausible` with the correct count.
5. A test (log-capture based, e.g. via a loguru sink or file read) runs a synchronization and asserts the connector's summary text appears in `summary.log` exactly once for that run.
6. A test covering the re-normalization script's console output (or the shared logging configuration it should use) asserts the per-record "training_load unknown" message does not appear more than once on stdout/stderr across a multi-record run.
7. All existing tests in `tests/test_app.py`, `tests/test_sync_engine.py`, `tests/test_logging_setup.py`, and `tests/test_renormalize.py` continue to pass.

## Open questions

Not blocking — these are implementation choices for the Architect, not gaps in the BO's intent:

- Whether the fix for AC 1 is making `scripts/renormalize_strava_unofficial.py` call `trainiq.logging_setup.configure()` like `trainiq/app.py` does, or de-duplicating/rate-limiting the "unknown" message at its source in `compute_training_load()`/`_unknown()`, or both. Either satisfies "at most once per run," and the Architect should pick based on whether other current or future one-off scripts have the same gap.
- Whether AC 3's fix is removing the duplicate summary call from `trainiq/app.py`'s `_log_sync_summary()` (and instead capturing the engine's own `summary_logger().info(...)` line for console printing), or removing the direct call from `trainiq/sync/engine.py` and relying solely on `_log_sync_summary()` (after adding `flagged` to it) — either removes the duplicate and satisfies AC 2 and AC 3 together. The Architect's call; the BO only requires one line, with flagged, in both places.
