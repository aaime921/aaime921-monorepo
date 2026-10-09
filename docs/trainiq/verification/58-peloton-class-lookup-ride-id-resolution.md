# Verification: Fix Peloton Class Lookup — Two-Step `peloton_id` → `ride_id` Resolution (resolves BL-011)

**Issue:** #58
**PR:** [#60](https://github.com/aaime921/aaime921-monorepo/pull/60) — `issue-58-peloton-class-lookup-ride-id-resolution`
**Requirements:** [`docs/trainiq/requirements/58-peloton-class-lookup-ride-id-resolution.md`](../requirements/58-peloton-class-lookup-ride-id-resolution.md)
**Architecture:** [`docs/trainiq/architecture/58-peloton-class-lookup-ride-id-resolution.md`](../architecture/58-peloton-class-lookup-ride-id-resolution.md)
**PR head commit verified:** `7de2e6d52ba4b02feafb30b9996af4a9e97af2f0`
**Mergeable state:** `clean`

## Environment note (not a PR defect)

This sandbox's `keyring` install crashes (`pyo3_runtime.PanicException` from
`cryptography`'s rust bindings, via `secretstorage`'s SecretService backend)
on any test that touches `CredentialStore`. Reproduced identically on
`origin/main` via a separate worktree at the same commit this PR is based on
— confirmed pre-existing and unrelated to this change. Worked around for this
pass with `PYTHON_KEYRING_BACKEND=keyring.backends.null.Keyring`, which
avoids loading the SecretService backend entirely and let the full suite
actually run (rather than erroring out at fixture setup).

## Test suite

Checked out PR branch `issue-58-peloton-class-lookup-ride-id-resolution` at
`7de2e6d`, fresh deps (`pip install -e ".[dev]"`), ran from
`projects/trainiq/`:

```
PYTHON_KEYRING_BACKEND=keyring.backends.null.Keyring python3 -m pytest -q
521 passed, 2 failed in 20.63s
```

The 2 failures (`test_real_csv_import_is_idempotent`,
`test_real_csv_full_regression` in `test_peloton_csv_import.py`) are a
missing BO-local fixture file (`/home/claude/peloton_work/aimea75_workouts.csv`),
not present in this sandbox. Independently reproduced identically on
`origin/main` (`1410efd`, this PR's base) via a separate worktree — confirmed
pre-existing and unrelated to this PR's change, matching the Developer's own
report and the same note from the #48 QA pass.

## Acceptance criteria

| # | Criterion | Verdict | Evidence |
|---|---|---|---|
| 1 | Resolves `peloton_id` via `GET /api/peloton/{peloton_id}` → `ride_id`, then `GET /api/ride/{ride_id}/details` with that resolved id; replaces the direct `peloton_id`-as-ride-id call (resolves BL-011) | ✅ PASS | `trainiq/connectors/peloton.py`: new `fetch_class_session()` (`SESSION_ENDPOINT_TEMPLATE = "/api/peloton/{peloton_id}"`), `download()`'s loop calls it before `fetch_class_details()`, passing the resolved `ride_id` — never the raw `peloton_id`. `test_regression_old_bug_session_id_used_directly_as_ride_id_404s` proves the old call pattern 404s; `test_fetch_class_session_success_returns_parsed_session_dict` and the `download()` tests prove the new path works end to end. |
| 2 | Field mapping: `activity_title`←`ride.title`, `instructor_name`←`ride.instructor.name`, `class_type`←`class_types[].name`, `planned_duration_s`←`ride.duration`, `provider_class_id`←resolved `ride_id` (not `peloton_id`) | ✅ PASS | `_extract_ride_metadata()` (`peloton.py`) implements exactly this mapping against the live-verified nested shape (`class_types` top-level, `ride.*` nested). `test_extract_ride_metadata_maps_all_six_fields` asserts all 6 fields; `test_download_two_workouts_sharing_ride_id_fetches_ride_details_once` asserts `_provider_class_id == "ride-1"` (the resolved id), not either input `peloton_id`. |
| 3 | Repeated class (same `peloton_id` twice, or two `peloton_id`s → same `ride_id`) costs ≤1 session call per distinct `peloton_id` and ≤1 ride-details call per distinct `ride_id` | ✅ PASS | Two independent caches in `download()` (`session_ride_id_cache`, `ride_details_cache`) and the same pattern, created once per invocation, in `run_backfill()`/`_process_row()`. `test_download_two_workouts_sharing_ride_id_fetches_ride_details_once` (2 session calls, 1 ride-detail call), `test_download_same_peloton_id_twice_resolves_session_once` (1 session call, 1 ride-detail call), and `test_cross_row_caching_two_rows_sharing_resolved_ride_id_cost_one_ride_details_call` (backfill-script-level) all assert exact call counts. |
| 4 | A failure at either step is logged as a warning and recorded as `lookup_failed`, never fabricated or confused with partial success | ✅ PASS | Both steps in `download()` and `_process_row()` set `CLASS_TYPE_LOOKUP_FAILED` and `continue`/`return "failed"` before any field assignment on a `None` result. `test_download_session_lookup_404_sets_lookup_failed_and_logs_without_fetching_ride_details` (step 1, asserts no ride-detail call follows) and `test_download_ride_details_404_after_successful_session_resolution_sets_lookup_failed_and_logs` (step 2) are two distinct, separately-asserted test cases, each checking the log message and distinguishing which id(s) are mentioned. Backfill-script equivalents: `test_session_resolution_404_is_recorded_as_lookup_failed_without_fetching_ride_details`, `test_ride_details_404_after_successful_session_resolution_is_recorded_as_lookup_failed`. |
| 5 | `backfill_peloton_class_metadata.py --retry-failed` against fixtures shaped like the BO's 131 `lookup_failed` rows resolves them via the fixed two-step lookup | ✅ PASS | `test_retry_failed_flag_reattempts_previously_failed_rows` and `test_successful_run_processes_class_and_non_class_rows` seed `lookup_failed` rows and a fake connector returning the live-evidence shapes; rows resolve to real class metadata including `difficulty_estimate`. Running against the BO's real 131 rows remains an ops task (requirements doc, "Out of scope") — correctly not attempted here. |
| 6 | New syncs populate class metadata for new class workouts, no regression to `not_a_class` handling | ✅ PASS | `test_download_non_class_workout_gets_not_a_class_sentinel_no_network_call` still passes unchanged; `test_download_class_workout_with_since_none_is_always_attempted` / `..._older_than_since_is_not_fetched` cover the live-sync path end to end through the new two-step lookup. |
| 7 | Existing 429/`Retry-After` handling (ADR-037) applies unchanged to both calls | ✅ PASS | `test_download_session_lookup_rate_limited_propagates_transient_error` and `test_download_ride_details_rate_limited_propagates_transient_error` each assert `TransientError.retry_after_s == 30.0` for their respective step. Backfill script wraps each call independently in `retry_with_backoff()` (`_process_row()`), confirmed by reading the diff — a failure on one step doesn't force a retry of an already-succeeded, cached step. `test_simulated_rate_limiting_backs_off_and_still_succeeds` exercises this at the script level. |
| 8 | Tests cover: old-bug regression, session-resolution success/404, ride-details success/404 (distinct from session 404), and AC3's caching | ✅ PASS | All present and individually verified by reading each test body (not just names): `test_regression_old_bug_session_id_used_directly_as_ride_id_404s`, `test_fetch_class_session_success_returns_parsed_session_dict`, `test_fetch_class_session_404_returns_none`, `test_extract_ride_metadata_maps_all_six_fields`, the two distinct 404 tests from AC4 above, `test_download_two_workouts_sharing_ride_id_fetches_ride_details_once`/`test_download_same_peloton_id_twice_resolves_session_once`. Edge cases also covered: `test_extract_ride_metadata_empty_class_types_is_empty_string_not_none` (the COALESCE-contract hazard called out in the architecture doc), `test_extract_ride_metadata_multiple_class_types_comma_joined`, `test_extract_ride_metadata_missing_instructor_is_none_not_an_error`. |
| 9 | All existing tests continue to pass | ✅ PASS | 521 passed, 2 pre-existing/unrelated failures (see "Test suite" above), reproduced identically on `main`. `test_architecture_invariants.py` has zero diff from `main` — confirms no new write path was introduced, matching the architecture doc's "Unchanged" list. |

## Additional checks performed (not just re-running the Developer's tests)

- Read the full diff of every changed file (`peloton.py`, `schema.py`,
  `sync/engine.py`, `normalization/engine.py`,
  `backfill_peloton_class_metadata.py`, `BACKLOG.md`) against the
  architecture doc's "Interfaces/contracts" and "Task breakdown" sections —
  implementation matches the design line-for-line, including the one
  explicitly-flagged deviation (`apply_class_metadata_update()` writing 6
  columns instead of 5, which is correct: without it `--retry-failed` could
  never persist `difficulty_estimate`).
- Verified `upsert_normalized_activity()`'s INSERT column list, `VALUES`
  placeholder count (21/21), and the UPDATE branch's `COALESCE` list and
  bound-params tuple all stay in sync for the new 7th column — a common
  class of bug when extending this kind of hand-written SQL.
- Confirmed the empty-`class_types` → `""` (not `None`) contract is actually
  implemented as written (`", ".join(...)` on an empty list yields `""`
  naturally) and independently asserted by its own test, not merely
  mentioned in a docstring.
- Confirmed `CURRENT_SCHEMA_VERSION` bumped to `7` and migration key `7` is
  distinct from the existing `6` (no collision, unlike the issue #48 PR's
  first QA pass).
- Confirmed the two caches in both `download()` and the backfill script are
  constructed once per call/run (not per row/workout) by reading the actual
  call sites, not just the docstrings.
- Independently reproduced the sandbox's `keyring`/`secretstorage` crash on
  `origin/main` at this PR's base commit before treating it as unrelated —
  not simply trusted as a known issue.

## Result

**All 9 acceptance criteria pass.** PR #60 is approved and ready to merge
(BO's call, per pipeline protocol).
