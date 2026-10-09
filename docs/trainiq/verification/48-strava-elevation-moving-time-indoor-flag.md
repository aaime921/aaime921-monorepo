# Verification: Keep Elevation Gain, Moving Time and Indoor/Outdoor Flag (Strava)

**Issue:** #48
**PR:** #56 (`issue-48-strava-elevation-moving-time-indoor-flag` → `main`)
**Requirements:** [`docs/trainiq/requirements/48-strava-elevation-moving-time-indoor-flag.md`](../requirements/48-strava-elevation-moving-time-indoor-flag.md)
**Architecture:** [`docs/trainiq/architecture/48-strava-elevation-moving-time-indoor-flag.md`](../architecture/48-strava-elevation-moving-time-indoor-flag.md)

## Method

Checked out PR branch `issue-48-strava-elevation-moving-time-indoor-flag` at
`30cb68a` in an isolated worktree, installed `projects/trainiq` into a clean
venv, ran the full test suite, and read every changed production file
(`schema.py`, both Strava connectors, `normalization/engine.py`,
`sync/engine.py`) against the architecture doc's Interfaces/contracts
section line by line. Separately, did a test-merge of `origin/main` into the
PR branch (not committed) to check mergeability, since the branch's base
predates a PR that merged after it was opened.

## Pass/fail by acceptance criterion

| AC | Description | Verdict | Notes |
|----|------|------|-------|
| 1 | `strava_unofficial` stores `elevation_gain_m`/`moving_time_s`/`is_indoor` from `elevation_gain_raw`/`moving_time_raw`/`trainer` | ✅ PASS | `normalize()` (`strava_unofficial.py:334-336`) uses `.get()` exactly per the architecture snippet. Covered by `test_normalize_outdoor_activity_maps_elevation_and_moving_time_is_not_indoor` and `test_normalize_indoor_trainer_ride_is_indoor_true`. |
| 2 | Official `strava` connector populates the same three fields via `_activity_to_raw_dict()` + `normalize()` | ✅ PASS | Both methods updated (`strava.py:199-205` capture, `:237-239` map) — confirmed `_activity_to_raw_dict()` is not a no-op fix, matching the requirements doc's explicit warning. Covered by `test_outdoor_ride_with_elevation_round_trips_through_raw_dict_and_normalize` and `test_indoor_trainer_ride_round_trips_through_raw_dict_and_normalize` (full round-trip, not `normalize()` alone). |
| 3 | Missing source data stays `None`, never defaulted (`is_indoor` never `False` by default) | ✅ PASS | All three sites use `.get()` with no second argument. Schema migration 6 has no `NOT NULL`/`DEFAULT`. Directly proven at the schema layer by `test_v6_migration_leaves_pre_existing_rows_null_not_false_or_zero` (asserts `(None, None, None)` on a pre-existing row) and at the connector layer by `test_all_three_fields_missing_normalize_to_none_not_fabricated` / `test_normalize_all_three_fields_missing_resolve_to_none_not_fabricated` — both assert `is None`, not just falsy, so a `0`/`False` regression would be caught. |
| 4 | Existing `strava_unofficial` rows backfill via `renormalize_provider()` | ✅ PASS | `test_renormalize_backfills_elevation_moving_time_indoor_from_stored_raw_payload` runs an existing stored raw payload through `renormalize_provider()` end-to-end and asserts the three columns populate — not just that `normalize()` produces the right dict in isolation. |
| 5 | Official-`strava` backfill constraint documented | ✅ PASS | `renormalize.py` (lines ~76-80) documents that official-`strava` rows synced before this fix lack the source fields in `raw_activities` entirely, so a fresh sync is required before re-normalization helps; also stated in the PR body. |
| 6 | Tests cover outdoor, indoor-trainer, and all-missing cases | ✅ PASS | Confirmed present for both connectors (6 dedicated tests across `test_strava_connector.py` / `test_strava_unofficial_connector.py`), plus the schema-layer and renormalize-layer tests above. `moving_time_s` vs `duration_s` distinctness is also asserted (different fixture values for `moving_time_raw` vs `elapsed_time_raw`). |
| 7 | All existing tests continue to pass | ✅ PASS (on PR branch alone) | `pytest` on the PR branch as checked out: 494 passed, 2 failed. The 2 failures (`test_peloton_csv_import.py::test_real_csv_import_is_idempotent`, `::test_real_csv_full_regression`) are a `FileNotFoundError` for a BO-local CSV path not present in this sandbox. Independently reproduced the identical 2 failures on `origin/main` alone (pre-existing, unrelated to this PR). |

## Blocking defect found — not an AC, but blocks merge

**The PR branch is stale relative to `origin/main` in a way that causes a real
data-loss bug, not just a textual merge conflict.** PR #55 (issue #46,
"Peloton class metadata + Strava name/sport_type") merged to `main` *after*
PR #56 was opened, and it **also** added a schema migration numbered `6` to
`normalized_activities` (`activity_title`, `instructor_name`, `class_type`,
`planned_duration_s`, `provider_class_id`, `sport_type_raw`).

`_MIGRATIONS` in `schema.py` is a plain Python `dict` literal keyed by
integer version. Two entries with the same key `6` do not "merge" — the
second one silently overwrites the first in the resulting dict. Concretely:
a naive merge/rebase of PR #56 onto current `main` would compile and run,
but whichever migration-6 block loses the key collision would **never run
against any database**, silently dropping either this issue's three new
columns or #46's six new columns (and `CURRENT_SCHEMA_VERSION` would still
read `6`, so nothing would signal the loss — no error, no test failure,
just missing columns the first time code tries to read/write them).

Reproduced directly: test-merged `origin/main` into the PR branch locally
(not committed/pushed). Git reports conflicts (not a silent dict collision
at the git level, since both sides touch overlapping lines), but confirms
the two migrations are genuinely both numbered `6`:

```
CONFLICT (content): Merge conflict in projects/trainiq/trainiq/storage/schema.py
CONFLICT (content): Merge conflict in projects/trainiq/trainiq/connectors/strava.py
CONFLICT (content): Merge conflict in projects/trainiq/trainiq/sync/engine.py
CONFLICT (content): Merge conflict in projects/trainiq/tests/test_storage.py
CONFLICT (content): Merge conflict in projects/trainiq/tests/test_strava_connector.py
```

The `strava.py` and `test_strava_connector.py` conflicts are from both
issues editing the same functions (`_activity_to_raw_dict()`, `normalize()`,
`_fake_activity()`) — resolvable by a human/developer combining both sets of
fields, but not something QA should resolve by picking a side. The
`schema.py` conflict specifically requires renumbering one migration to `7`
(and updating `CURRENT_SCHEMA_VERSION` accordingly) — a real code decision,
not a mechanical merge.

GitHub's own `mergeable_state` for PR #56 already reports `dirty`
(unmergeable against current `main`), consistent with this finding.

This is exactly the kind of conflict `docs/trainiq/PIPELINE.md`'s
"Cross-issue dependencies" section describes as the Developer's job to
resolve (rebase, renumber the migration, re-run the suite), not QA's — QA
verifies, it doesn't rewrite the Developer's migration numbering or merge
two in-flight schema changes.

## Verdict

All 7 acceptance criteria pass when the PR branch is evaluated on its own
merge base. However, the PR is **not currently mergeable into `main`** and
contains a real migration-version collision with already-merged work (PR
#55 / issue #46), not a cosmetic conflict — sending back to the Developer to
rebase onto current `main`, renumber the migration to `7`, resolve the
`strava.py`/`test_strava_connector.py` conflicts (combining both issues'
fields), and re-run the full suite before re-requesting QA.
