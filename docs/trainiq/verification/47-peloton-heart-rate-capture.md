# Verification: Capture Peloton Heart-Rate Data (avg/max HR, HR Zones, Max Power)

**Issue:** #47
**PR:** [#59](https://github.com/aaime921/aaime921-monorepo/pull/59) — `issue-47-peloton-hr-zone-effort-points`
**Requirements:** [`docs/trainiq/requirements/47-peloton-heart-rate-capture.md`](../requirements/47-peloton-heart-rate-capture.md)
**Architecture:** [`docs/trainiq/architecture/47-peloton-heart-rate-capture.md`](../architecture/47-peloton-heart-rate-capture.md)
**PR head commit verified:** `62add1613a2580ed00b4fa9bc00dfaa26c411cf1` ("feat(trainiq): implement Peloton AC2 — avg/max HR, max power (issue #47)")

## Mergeability check

- PR base SHA `1410efd` is on `origin/main`'s history. `origin/main` has since
  advanced (docs-only architecture for #57/#58, plus an unrelated QA
  verification commit) — no code overlap with this PR's files.
- Local dry-run merge of `origin/main` into the PR branch (`git merge
  --no-commit --no-ff`) completed cleanly with no conflicts; aborted after
  checking.
- **Verdict: mergeable, clean.**

## Test suite

Checked out PR branch `issue-47-peloton-hr-zone-effort-points` at `62add16`,
installed `pip install -e ".[dev]"`, ran the full suite from
`projects/trainiq/` (`python3 -m pytest`):

```
546 passed, 2 failed in 32.58s
```

The 2 failures (`test_real_csv_import_is_idempotent`,
`test_real_csv_full_regression` in `test_peloton_csv_import.py`) are a
missing BO-local fixture file (`/home/claude/peloton_work/aimea75_workouts.csv`),
not present in this sandbox. Independently reproduced identically against
`origin/main` via a separate worktree (`736ce79`, current tip) — confirmed
pre-existing and unrelated to this PR's change.

Also re-ran the specific files the architecture doc's Task 9 calls out plus
this issue's own new/changed suites in isolation:
`test_sync_engine.py`, `test_renormalize.py`, `test_architecture_invariants.py`,
`test_peloton_connector.py`, `test_upsert_normalized_activity.py`,
`test_backfill_peloton_workout_details.py` → **165 passed, 0 failed.**

## Acceptance criteria

| # | Criterion | Verdict | Evidence |
|---|---|---|---|
| 1 | `hr_zone_1_s`…`hr_zone_5_s`/`effort_points` stored when `effort_zones` present, NULL (not zero) when `effort_zones` is null, sourced from the existing workouts-list call | ✅ PASS | `peloton.py` `normalize()` reads `EFFORT_ZONES_FIELD` straight off `raw` (lines 950-963); `effort_zones is None` branch sets every field to `None`, never `0`. `test_normalize_effort_zones_null_is_handled_as_absent_not_an_error`, `test_normalize_new_hr_zone_and_effort_fields_null_when_effort_zones_is_null`, `test_normalize_new_hr_zone_and_effort_fields_read_straight_off_effort_zones` all pass. No new network call added for this part (confirmed by reading `download()` — zone data is read from the same per-page record already fetched). |
| 2 | `avg_hr`/`max_hr`/`max_power` stored from the performance endpoint when available, NULL when no HR/power data (e.g. no monitor) — never fabricated/derived | ✅ PASS | `_parse_performance_response()` (peloton.py:375-410) looks up `metrics[]` **by `slug`, never position**, against the BO's real captured shape (`avg_hr`/`max_hr` from `slug="heart_rate"`, `max_power` from `slug="output"`, guarded on `display_unit` bpm/watts with a logged fallback to `None` on mismatch). A workout with no `heart_rate` entry → `avg_hr`/`max_hr` stay `None`. `test_parse_performance_response_extracts_hr_and_power_by_slug_not_position`, `test_parse_performance_response_no_heart_rate_entry_yields_none_not_zero`, `test_parse_performance_response_unexpected_display_unit_yields_none_and_warns` all pass and use the BO's exact captured numbers (avg_hr=135, max_hr=163, max_power=294). |
| 3 | Reuses #46's skip-if-already-synced-since-checkpoint optimization, in-run cache pattern, and `retry_with_backoff()` — not a second implementation | ✅ PASS | `download()` computes `is_new_since_checkpoint` once per workout and both the #46 class-lookup branch and the #47 performance-fetch branch read the same variable (peloton.py:866-908). `fetch_workout_performance()` calls the single module-level `retry_with_backoff()` defined in `trainiq/sync/engine.py:303` — confirmed only one definition exists repo-wide (`grep` for `def retry_with_backoff`). No in-run cache is added for the performance fetch, correctly — per-workout data has nothing to deduplicate, matching the architecture doc's explicit call. |
| 4 | Resumable, rate-limited backfill covers all 136 existing workouts for ACs 1-2, sharing #46's backfill mechanism | ✅ PASS | AC1's zone/effort-points data needs no backfill script at all — it's covered for free via the existing `renormalize_provider()` path (confirmed safe below). AC2 is covered by `scripts/backfill_peloton_workout_details.py` (renamed from `backfill_peloton_class_metadata.py`, confirmed via `ls scripts/` — old name no longer exists in code), which selects rows missing either `class_type` or `performance_fetch_status`, resolves each independently, and **commits after every row** (`scripts/backfill_peloton_workout_details.py:237`) for resumability. `test_backfill_peloton_workout_details.py` passes, including resume-after-interruption and `--retry-failed` cases. |
| 5 | A performance-fetch failure is logged and degrades to NULL, never aborting the sync or backfill, for both paths | ✅ PASS | `fetch_workout_performance()` catches `TransientError`/`PelotonHTTPError`, logs a warning, returns `None` (peloton.py:805-812) — `download()` sets `_performance_fetch_status = "failed"` and continues the loop rather than raising. `AuthenticationError` still propagates unchanged (connector-wide concern, correctly not swallowed). Backfill's `_process_performance()` writes the same `"failed"` sentinel via `apply_hr_performance_update()` and still commits/continues. `test_fetch_workout_performance_404_returns_none_logged_not_raised`, `test_fetch_workout_performance_retries_exhausted_returns_none_not_raised`, `test_fetch_workout_performance_401_raises_authentication_error`, `test_download_performance_fetch_failure_sets_failed_status_does_not_raise` all pass. |
| 6 | Tests from a real captured performance-endpoint fixture covering full HR/power data, `effort_zones: null`, and a fetch failure | ✅ PASS | `_REAL_PERFORMANCE_RESPONSE` fixture in `test_peloton_connector.py` (~line 1170) uses the BO's exact captured values (max_value=294/131/128/57/39.8, average_value=131/91/34/28.3/135, slug-keyed) — not a guessed shape. All three required cases present: full data (`test_fetch_workout_performance_success_returns_parsed_dict`), `effort_zones: null` (`test_normalize_new_hr_zone_and_effort_fields_null_when_effort_zones_is_null`), and fetch failure (`test_normalize_performance_fetch_failed_yields_none_for_all_three`, `test_download_performance_fetch_failure_sets_failed_status_does_not_raise`). |
| 7 | All existing tests continue to pass | ✅ PASS | 546 passed; the only 2 failures are the pre-existing, unrelated, BO-local-fixture-dependent CSV import tests, independently reproduced identically on `origin/main`. |

## Additional checks performed (not just re-running the Developer's tests)

- Confirmed the upsert's COALESCE split is correct, not inverted: `avg_hr`,
  `max_hr`, `max_power`, `performance_fetch_status` use `COALESCE(?, col)` in
  `sync/engine.py`'s `UPDATE` branch (lines 213-224), while `hr_zone_1_s`…
  `hr_zone_5_s`/`effort_points` are unconditional overwrite in the **same**
  statement — matching the architecture doc's reasoning (zone data is always
  fully re-derivable every pass; HR/power genuinely has a "not attempted this
  pass" case). `test_update_with_hr_and_power_fields_none_preserves_existing_values`
  and `test_update_with_real_hr_zone_values_overwrites` both pass and prove
  the two groups behave independently, not just that each passes in
  isolation.
- Confirmed `renormalize_provider()` stays safe for the new fields by reading
  `normalize()`'s own docstring/comments and `test_renormalize.py` — a
  pre-#47 stored raw payload has no `_avg_hr`/`_performance_fetch_status`
  keys, `normalize()` returns `None` for all of them, and the COALESCE upsert
  preserves whatever's already stored (no accidental wipe on re-run).
- Confirmed `apply_hr_performance_update()` only ever writes `avg_hr`,
  `max_hr`, `max_power`, `performance_fetch_status` (peloton.py:462-472) —
  no accidental write to any canonical-identity column, same narrow
  single-writer exception already granted to `apply_class_metadata_update()`.
- Confirmed no stale references to the old script name
  `backfill_peloton_class_metadata.py` remain anywhere in code (`grep -rn`
  across `projects/trainiq/`) — only historical docs/verification files (for
  #46/#57/#58) mention it, which is expected and correct.
- Confirmed the naming deviation (`performance_fetch_status` instead of the
  architecture doc's proposed `hr_fetch_status`) is explained consistently
  in three places — `peloton.py`'s Feature 3.8 docstring, the PR description,
  and `BACKLOG.md`'s BL-012 entry — and matches #57's architecture doc's
  request to share one status column rather than add a second for the same
  underlying `fetch_workout_performance()` call.
- Confirmed `BACKLOG.md`'s BL-012 entry is marked resolved with the BO's
  captured evidence cited, and `docs/trainiq/verification/peloton-2026-09-28.md`
  carries the 2026-10-09 addendum with the full captured `performance_graph`
  response shape (Task 1's output), matching the live values used in the
  test fixtures.

## Result

**All 7 acceptance criteria pass.** PR #59 is approved and ready to merge
(BO's call, per pipeline protocol).
