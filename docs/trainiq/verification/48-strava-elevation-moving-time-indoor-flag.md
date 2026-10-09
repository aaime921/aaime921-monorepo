# Verification: Keep Elevation Gain, Moving Time and Indoor/Outdoor Flag (Strava)

**Issue:** #48
**PR:** [#56](https://github.com/aaime921/aaime921-monorepo/pull/56) — `issue-48-strava-elevation-moving-time-indoor-flag`
**Requirements:** [`docs/trainiq/requirements/48-strava-elevation-moving-time-indoor-flag.md`](../requirements/48-strava-elevation-moving-time-indoor-flag.md)
**Architecture:** [`docs/trainiq/architecture/48-strava-elevation-moving-time-indoor-flag.md`](../architecture/48-strava-elevation-moving-time-indoor-flag.md)
**PR head commit verified:** `4ab3407e516dbcb4963d73662bc8a9a62484f5d8` ("fix(trainiq): rebase issue #48 onto main, renumber schema migration to v7")

## Re-verification context

This is a second QA pass. The first pass (see the earlier verification comment
on the issue) found all 7 ACs passing in isolation but flagged PR #56 as
unmergeable: its branch predated PR #55 (#46), and both PRs independently
claimed schema migration version `6` on `normalized_activities` — a silent
dict-key collision, not a diff conflict. Sent back to `stage:dev` for a
rebase + renumber.

The Developer's rework (commit `4ab3407`) merged current `main` into the
branch and renumbered this issue's migration `6` → `7`
(`CURRENT_SCHEMA_VERSION` bumped to `7`). This pass re-verifies the fix and
re-runs every AC from scratch against the updated branch — not a rubber stamp
of the Developer's own claim.

## Mergeability check (the specific defect from the last QA pass)

- `pull_request_read` on PR #56 now reports `mergeable_state: "clean"`
  (previously `dirty`).
- PR base SHA is `12c17ae51a94671bedf3f91aab46de1ad18d653b`, which is on
  `origin/main`'s history. `origin/main` has advanced 3 commits past that
  (docs-only: requirements/architecture for issues #57 and #58) — no code
  overlap with this PR's files, so this does not reintroduce the collision.
- Read `trainiq/storage/schema.py` directly on the PR branch: `_MIGRATIONS`
  now has distinct keys `6` (issue #46's 6 class-metadata columns) and `7`
  (this issue's 3 columns), each with its own distinct docstring recording
  the renumbering. `CURRENT_SCHEMA_VERSION = 7`. No key collision.
- **Verdict: mergeability defect is fixed.**

## Test suite

Checked out PR branch `issue-48-strava-elevation-moving-time-indoor-flag` at
`4ab3407`, fresh venv, `pip install -e ".[dev]"`, ran
`pytest projects/trainiq/tests/` (from `projects/trainiq/`, full suite):

```
518 passed, 2 failed in 35.91s
```

The 2 failures (`test_real_csv_import_is_idempotent`,
`test_real_csv_full_regression` in `test_peloton_csv_import.py`) are a
missing BO-local fixture file (`/home/claude/peloton_work/aimea75_workouts.csv`),
not present in this sandbox. Independently reproduced identically on
`origin/main` (`e8b982c`, current tip) via a separate worktree — confirmed
pre-existing and unrelated to this PR's change.

## Acceptance criteria

| # | Criterion | Verdict | Evidence |
|---|---|---|---|
| 1 | `strava_unofficial` stores `elevation_gain_m`/`moving_time_s`/`is_indoor` from `elevation_gain_raw`/`moving_time_raw`/`trainer` | ✅ PASS | `trainiq/connectors/strava_unofficial.py:334-336`, uses `.get()`. `test_normalize_outdoor_activity_maps_elevation_and_moving_time_is_not_indoor` passes. |
| 2 | Official `strava` populates the same 3 fields via `_activity_to_raw_dict()` **and** `normalize()` | ✅ PASS | `trainiq/connectors/strava.py:199-205` (capture) and `:244-246` (map). `test_outdoor_ride_with_elevation_round_trips_through_raw_dict_and_normalize` and `test_indoor_trainer_ride_round_trips_through_raw_dict_and_normalize` exercise the full round trip, not `normalize()` in isolation. |
| 3 | Missing source field → `None`, never defaulted (`is_indoor` never `False` for absent `trainer`) | ✅ PASS | Schema: `ALTER TABLE ... ADD COLUMN` with no `NOT NULL`/`DEFAULT` (schema.py:187-191). Identity-checked (`is None`/`is False`/`is True`, not truthy/falsy) in `test_v7_migration_leaves_pre_existing_rows_null_not_false_or_zero`, `test_all_three_fields_missing_normalize_to_none_not_fabricated`, `test_normalize_all_three_fields_missing_resolve_to_none_not_fabricated`. |
| 4 | Existing `strava_unofficial` rows backfill via `renormalize_provider()` | ✅ PASS | `test_renormalize_backfills_elevation_moving_time_indoor_from_stored_raw_payload` passes — proves the backfill path end-to-end, not just `normalize()`'s output in isolation. |
| 5 | Official-`strava` backfill constraint documented | ✅ PASS | `trainiq/normalization/renormalize.py` docstring (lines ~76-80) states rows synced before this fix lack the source fields in `raw_activities` entirely, so a fresh sync is required before re-normalization helps; documents the one-line future wrapper shape without building unused tooling now. Matches the architecture doc's stated constraint. |
| 6 | Tests cover outdoor, indoor-trainer, all-missing, for both connectors | ✅ PASS | All 6 cases present and passing: `test_outdoor_ride_with_elevation_round_trips_through_raw_dict_and_normalize`, `test_indoor_trainer_ride_round_trips_through_raw_dict_and_normalize`, `test_all_three_fields_missing_normalize_to_none_not_fabricated` (strava.py); `test_normalize_outdoor_activity_maps_elevation_and_moving_time_is_not_indoor`, `test_normalize_indoor_trainer_ride_is_indoor_true`, `test_normalize_all_three_fields_missing_resolve_to_none_not_fabricated` (strava_unofficial.py). |
| 7 | Full test suite passes | ✅ PASS | 518 passed, 2 pre-existing failures (missing BO-local fixture) reproduced identically on `main` — unrelated. |

## Additional checks performed (not just re-running the Developer's tests)

- Confirmed `moving_time_s` is sourced from a key distinct from `duration_s`
  (`moving_time_raw` vs. `elapsed_time_raw`) by reading
  `strava_unofficial.py` directly — guards against a copy-paste mapping bug.
- Confirmed `sync/engine.py`'s `upsert_normalized_activity()` extends **both**
  the `INSERT OR IGNORE` and `UPDATE` column lists/params (two independently
  maintained SQL statements) — not just one.
- Confirmed `confidence.py`'s `_ACTIVITY_OPTIONAL_FIELDS` was **not** touched,
  per the architecture doc's explicit scope decision (avoids silently
  changing `source_confidence` for unrelated activities).
- Re-read the renumbered migration dict directly (not trusting the commit
  message alone) to confirm no residual key collision with issue #46's
  migration 6.

## Result

**All 7 acceptance criteria pass. The mergeability defect from the previous
QA pass is confirmed fixed.** PR #56 is approved and ready to merge (BO's
call, per pipeline protocol).
