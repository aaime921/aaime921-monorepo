# Verification: Record Class Title, Instructor, Class Type and Planned Length for Every Workout (Peloton + Strava)

**Issue:** #46
**PR:** #55 (`issue-46-peloton-strava-class-metadata` → `main`)
**Requirements:** [`docs/trainiq/requirements/46-peloton-strava-class-metadata.md`](../requirements/46-peloton-strava-class-metadata.md)
**Architecture:** [`docs/trainiq/architecture/46-peloton-strava-class-metadata.md`](../architecture/46-peloton-strava-class-metadata.md)

## Method

Checked out PR branch `issue-46-peloton-strava-class-metadata` at `ad38c76`
(base `fb9d9dc`, current `origin/main`). Diffed against `origin/main` to
confirm exact scope, read every changed production file in full (schema,
both connectors, the normalization engine, the sync engine's upsert, and
the backfill script), installed the project into a clean venv from
`pyproject.toml`'s `[dev]` extra, ran the full `projects/trainiq` test
suite, ran every issue-46-specific test file individually, and independently
verified AC4/AC5 (the "no new logic needed, just schema shape" claim) with
a standalone script against the real `upsert_normalized_activity()` and
`open_db()` functions rather than trusting the architecture doc's reasoning
alone.

## Scope check

`git diff --stat origin/main...origin/issue-46-peloton-strava-class-metadata`:

```
projects/trainiq/BACKLOG.md                                     |  38 +++
projects/trainiq/scripts/backfill_peloton_class_metadata.py      | 207 +++++++++++++++++
projects/trainiq/tests/test_backfill_peloton_class_metadata.py   | 211 +++++++++++++++++
projects/trainiq/tests/test_peloton_connector.py                 | 257 ++++++++++++++++++++-
projects/trainiq/tests/test_renormalize.py                       |  56 +++++
projects/trainiq/tests/test_storage.py                            |  28 ++-
projects/trainiq/tests/test_strava_connector.py                  |  17 +-
projects/trainiq/tests/test_strava_unofficial_connector.py        |  24 ++
projects/trainiq/tests/test_upsert_normalized_activity.py         | 141 +++++++++++
projects/trainiq/trainiq/connectors/peloton.py                   | 187 ++++++++++++++-
projects/trainiq/trainiq/connectors/strava.py                    |  15 +-
projects/trainiq/trainiq/connectors/strava_unofficial.py          |   7 +
projects/trainiq/trainiq/normalization/engine.py                 |  12 +
projects/trainiq/trainiq/storage/schema.py                       |  17 +-
projects/trainiq/trainiq/sync/engine.py                          | 120 +++++++---
15 files changed, 1293 insertions(+), 44 deletions(-)
```

Matches the architecture doc's "Affected components/files" table exactly.
`trainiq/dedup/detector.py`, `trainiq/normalization/renormalize.py`, and
`tests/test_architecture_invariants.py` are untouched, as the doc said they
should be (AC4 needs no dedup-side change; the backfill's narrow `UPDATE`
path doesn't trip the static architecture-invariant checks because it is
neither an `INSERT INTO normalized_activities` nor a
`build_canonical_record()` call).

## Acceptance criteria

| AC | Result | Evidence |
|----|--------|----------|
| 1. Every `workout_type == "class"` Peloton workout stores title, instructor, class type, planned length (s), and class id; `duration_s` unaffected | **PASS** | `peloton.py`'s `download()` attaches `_class_title`/`_instructor_name`/`_class_type`/`_planned_duration_s`/`_provider_class_id` per class workout via the new `fetch_class_details()`; `normalize()` passes all 5 through verbatim. `test_normalize_class_workout_with_full_metadata` asserts all 5 fields **and** `duration_s == 299` (actual duration, computed from `start_time`/`end_time`, independently of the new fields) in the same assertion block. |
| 2. Non-class/failed-lookup workouts get an explicit, non-NULL type/category distinct from "not attempted"; instructor NULL but the reason captured | **PASS** | Two sentinels (`CLASS_TYPE_NOT_A_CLASS = "not_a_class"`, `CLASS_TYPE_LOOKUP_FAILED = "lookup_failed"`) are written into `class_type` by `download()`/`_is_class_workout()`'s branches. `test_normalize_non_class_workout_has_sentinel_and_no_instructor` (`class_type == "not_a_class"`, `instructor_name is None`) and `test_normalize_class_workout_with_failed_lookup` both pass. A genuinely untried workout (predates this feature, or skipped by the checkpoint optimization) instead yields `class_type is None` — the three-way distinction (`None` = not attempted / sentinel string = attempted-with-definite-outcome / real category = success) is exercised by `test_normalize_workout_with_no_class_keys_at_all_yields_none_for_every_new_field`. |
| 3. Strava (official) and Strava-unofficial store raw `name` and raw `sport_type` (or display-name fallback) independent of Peloton linkage | **PASS** | `StravaConnector._activity_to_raw_dict()` now reads `activity.name`; `normalize()` sets `activity_title`/`sport_type_raw` (the latter reusing the exact same `discipline_raw` fallback chain, not a second independent one). `StravaUnofficialConnector.normalize()` sets `activity_title = raw.get("name")` and `sport_type_raw = raw.get("activity_type_display_name") or raw.get("display_type")` — confirmed byte-identical to the pre-existing `discipline_raw` fallback order in the same file (`grep` confirms the same two keys, same order). `test_normalize_activity_title_is_none_when_strava_reports_no_name` and the unofficial-connector equivalents confirm the never-fabricate `None` case. |
| 4. For a linked `dedup_links` pair, querying the data surfaces the Peloton side's class metadata; Strava's `name` stays on its own row, not lost | **PASS** | No merge/override code exists for this (by design — see architecture doc's "Linked-pair precedence" section): the 5 Peloton-only columns are structurally NULL on every non-Peloton row, so any query scoped to `provider = 'peloton'` returns real values and a Strava row's own `activity_title` is untouched by anything this PR adds. **Independently verified** (not just trusted from the design doc): wrote a standalone script against the real `open_db()`/`upsert_normalized_activity()` functions — inserted a Peloton row with full class metadata and a linked Strava row with its own `name`, linked them via `dedup_links`, then ran the AC4 query (join `dedup_links` → `normalized_activities WHERE provider='peloton'`). Result: `('Power Zone Max', 'Matt Wilpers', 'power_zone_max')` for the Peloton side; the Strava row's own `activity_title`/`sport_type_raw` (`'45 min Power Zone Max Ride with Matt Wilpers'`, `'Ride'`) remained intact and unaffected. |
| 5. Instructor name and class type are queryable (e.g. "Power Zone rides with Matt Wilpers in the last 90 days"); query documented | **PASS** | Query documented in the architecture doc's "Interfaces/contracts" section (`WHERE provider = 'peloton' AND instructor_name = ? AND class_type = ? AND start_time >= ?`). Same standalone script above ran this exact shape (`instructor_name='Matt Wilpers' AND class_type='power_zone_max'`) against the seeded data and got exactly the one matching row back. |
| 6. Resumable, rate-limited backfill tool; tested via fixtures (success, rate-limit, resume, no duplication) | **PASS** | `scripts/backfill_peloton_class_metadata.py` commits after every row via `apply_class_metadata_update()` and selects candidates by `class_type IS NULL` (or also `'lookup_failed'` with `--retry-failed`), ordered by `id`. `test_successful_run_processes_class_and_non_class_rows`, `test_simulated_rate_limiting_backs_off_and_still_succeeds` (asserts the fake `sleep_fn` was called with the expected backoff delay), `test_resume_after_simulated_interruption_does_not_reprocess_completed_rows` (asserts `connector.calls == ["ride-1", "ride-2"]` across two invocations — `ride-1` is never re-fetched), and `test_retry_failed_flag_reattempts_previously_failed_rows` all pass. |
| 7. A failed lookup (any reason) logs a warning and stores NULL class-metadata fields, for both the live sync path and the backfill tool — never fabricated | **PASS** | `download()`: `fetch_class_details()` returning `None` (404) sets `_class_type = "lookup_failed"` and calls `diagnostic_logger().warning(...)`; confirmed by `test_download_class_lookup_404_sets_lookup_failed_and_logs`. Backfill: `_process_row()` does the identical `diagnostic_logger().warning(...)` + `apply_class_metadata_update(..., {"class_type": CLASS_TYPE_LOOKUP_FAILED})` on a `None` result, confirmed by `test_lookup_returning_none_is_recorded_as_lookup_failed`. Neither path writes a fabricated title/instructor/duration — `apply_class_metadata_update()` is called with only `class_type` set in the failure case, leaving the other 4 columns untouched (`COALESCE`-safe on the live path, and never referenced at all in the failure branch of `_process_row()`). |
| 8. Tests cover: Peloton class w/ full metadata, Peloton non-class, Peloton class w/ failed lookup, Strava official w/ name+sport_type, Strava-unofficial w/ same | **PASS** | All 5 present and passing: `test_normalize_class_workout_with_full_metadata`, `test_normalize_non_class_workout_has_sentinel_and_no_instructor`, `test_normalize_class_workout_with_failed_lookup`, `test_normalize_maps_core_fields`/`test_normalize_activity_title_is_none_when_strava_reports_no_name` (Strava official), `test_normalize_maps_web_payload_fields_to_canonical_shape`/`test_normalize_activity_title_is_none_when_name_absent` (Strava-unofficial). |
| 9. All existing tests continue to pass | **PASS** | See "Full test suite" below: 457 passed, 2 pre-existing failures confirmed unrelated and present identically on `origin/main`. |

**All 9 acceptance criteria pass.**

## Full test suite (regression check)

```
cd projects/trainiq && pytest -q
```

Result on PR branch (`ad38c76`), clean venv from `pyproject.toml`'s `[dev]`
extra: **457 passed, 2 failed**.

Both failures (`test_peloton_csv_import.py::test_real_csv_import_is_idempotent`,
`::test_real_csv_full_regression`) are `FileNotFoundError` on
`/home/claude/peloton_work/aimea75_workouts.csv`, a live-account CSV fixture
not present in this sandbox. Confirmed **pre-existing and unrelated**: the
same two tests reference the identical hardcoded path on `origin/main`
(`git show origin/main:projects/trainiq/tests/test_peloton_csv_import.py`).
This PR touches no CSV-import code — matches the Developer's own reported
count exactly (457 passed / 2 pre-existing failures).

## Issue-46-specific tests run individually

```
pytest -q tests/test_backfill_peloton_class_metadata.py tests/test_peloton_connector.py \
          tests/test_upsert_normalized_activity.py tests/test_renormalize.py \
          tests/test_strava_connector.py tests/test_strava_unofficial_connector.py tests/test_storage.py
```

Result: **152 passed**. Spot-checked the three most safety-critical tests
by reading their bodies directly (not just the pass/fail count) to confirm
they're not vacuous:
- `test_update_with_all_six_new_fields_none_preserves_existing_values` — proves `COALESCE` really preserves backfilled metadata on a renormalize-shaped re-upsert, while a pre-existing column (`avg_power`) still overwrites unconditionally in the same test.
- `test_renormalize_peloton_never_wipes_already_backfilled_class_metadata` — reproduces the exact regression scenario the architecture doc worried about (a pre-issue-46 raw payload with no `_class_*` keys, re-normalized against already-backfilled columns) and asserts the backfilled values survive.
- `test_resume_after_simulated_interruption_does_not_reprocess_completed_rows` — asserts the fake connector's call list, not just row counts, so a silent re-fetch-and-overwrite would actually be caught.

## Evidence-gap handling (Task 1 not run)

The architecture doc's Task 1 (live verification of `workout_type`/`peloton_id`
and the ride/class detail response shape against the BO's real account) was
not run — the Developer's handoff comment states this explicitly, citing the
same sandbox constraint (no stored Peloton credentials, no network path to
`api.onepeloton.com`) as issue #45's `BL-010`. This is the established,
accepted pattern in this repo for live-account-gated verification steps, not
a shortcut unique to this PR: every UNCONFIRMED field name has its own named
fallback already coded (`_is_class_workout()`'s dual branch), is tracked as
`BACKLOG.md` **BL-011**, and — critically — none of the schema, the
skip/COALESCE correctness logic, the backfill tool's resumability, or any
test's shape depends on what Task 1 eventually finds; only the literal
constant values would change. Per the requirements doc's own "Out of scope"
section, running Task 1 and the real 136-row backfill against the BO's
account are both explicitly ops tasks for the BO, not blocking criteria for
this pipeline. Not treated as an AC failure.

## Verdict

✅ All 9 acceptance criteria verified pass, including two (AC4/AC5)
independently re-derived against real code rather than taken on the
architecture doc's word. No regressions (pre-existing, unrelated test
failures only, confirmed present on `origin/main` too). PR #55 approved.
