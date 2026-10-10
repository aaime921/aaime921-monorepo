# QA Verification: Flag Implausible Eufy Weigh-Ins

**Issue:** #38
**PR:** #41 (`issue-38-eufy-weigh-in-plausibility-flagging`, commit `340e3b7`)
**Requirements:** `docs/trainiq/requirements/38-eufy-implausible-weigh-in-flagging.md`
**Architecture:** `docs/trainiq/architecture/38-eufy-implausible-weigh-in-flagging.md`
**ADR:** `docs/trainiq/adr/ADR-039-weigh-in-plausibility-flagging.md`

## Method

- Checked out PR branch `issue-38-eufy-weigh-in-plausibility-flagging`
  (commit `340e3b7`) locally.
- Built a fresh virtualenv (`python3.13 -m venv` + `pip install -e ".[dev]"`)
  and ran the full existing suite, then each new/changed test file in
  isolation.
- Read `plausibility.py`, `backfill.py`, the `sync/engine.py` and
  `normalization/engine.py` diffs, and `confirm_weigh_in.py` line-by-line
  against the architecture doc's interfaces/contracts section.
- Confirmed the 2 pre-existing `test_peloton_csv_import.py` failures are
  identical on `origin/main` via a separate `git worktree` (not introduced
  by this PR).
- Independently re-derived the AC4 backfill math by hand (median/deviation)
  rather than only trusting the Developer's assertions.
- Ran a three-dot diff scope check (`origin/main...issue-38-...`) to confirm
  only `project:trainiq` paths are touched.

## Acceptance criteria — pass/fail

| # | Criterion | Result | Evidence |
|---|---|---|---|
| 1 | Explicit, documented plausibility rule; relative to athlete's own history, not a fixed absolute range | **PASS** | `trainiq/normalization/plausibility.py::evaluate_weigh_in_plausibility()` implements two independent axes (body-fat floor ≤3.0%, weight deviation >25% from a 5-reading rolling median, skipped below 3 prior readings). Fully documented in `docs/trainiq/adr/ADR-039-weigh-in-plausibility-flagging.md` including the exact constants and rationale. No fixed absolute kg range anywhere in the rule. |
| 2 | Flagged reading's raw values persisted unchanged; flag state persisted separately | **PASS** | Schema v4 (`storage/schema.py`) adds `is_flagged_implausible`, `plausibility_reason`, `bo_confirmed_valid`, `bo_confirmed_at` as new columns — no existing column dropped/altered. `_upsert_weigh_in()`'s INSERT/UPDATE column lists still write `weight_kg`/`body_fat_pct`/`muscle_mass_pct` verbatim from the normalized record; `test_v4_migration_adds_plausibility_columns_to_weigh_ins` confirms the migration is additive only. |
| 3 | Analytics/trend computation excludes flagged rows | **PASS** (contract documented, vacuously satisfied) | Confirmed via `grep` that no analytics/trend module reading `weigh_ins` exists anywhere in `trainiq/` yet (only `sync/engine.py` and `storage/backfill.py` read/write it). The predicate `WHERE is_flagged_implausible = 0 OR bo_confirmed_valid = 1` is documented in ADR-039 as the required contract for the first query that does read `weigh_ins` for trends — consistent with the requirements' explicit framing ("wherever `weigh_ins` is currently read for that purpose" — nowhere, today). Not a gap introduced by this PR. |
| 4 | Backfill flags exactly the issue's 7 known-bad records, none of the normal readings | **PASS** | `tests/test_storage.py::test_v4_backfill_flags_exactly_the_issues_known_bad_rows` inserts the issue's exact evidence-table values (including both `2025-09-14 18:52/18:53` and `2026-07-05/06`, `2026-09-05` rows) plus 2 additional sub-40kg rows to reach 7, interleaved with 7 normal 80–88.3 kg readings, at schema v3; after `migrate()` to v4, asserts the flagged set equals exactly the 7 bad `external_id`s. Verified by direct test run (passes) and hand-checked the median math for 2 of the 7 rows independently. |
| 5 | Sync run summary reports a flagged-reading count | **PASS** | `tests/test_sync_engine.py::test_weigh_in_sync_summary_reports_flagged_count` asserts `"flagged 1 implausible"` appears in the summary log; `test_activity_sync_summary_always_reports_flagged_zero` confirms the clause is unconditional (always present, even at 0) for ACTIVITY-kind connectors. `ConnectorSyncResult.records_flagged_implausible` is additive at the end of the dataclass, consistent with the RC1-HF-006 precedent of never reordering existing fields. |
| 6 | BO has a documented, working, auditable way to un-flag a false positive | **PASS** | `projects/trainiq/scripts/confirm_weigh_in.py` + `confirm_weigh_in()`: looks up `(provider, external_id)`, raises without writing if the row doesn't exist (`NoSuchWeighIn`) or isn't currently flagged (`WeighInNotFlagged`), otherwise sets `bo_confirmed_valid=1`/`bo_confirmed_at=now` while leaving `is_flagged_implausible`/`plausibility_reason` untouched (auditable — confirmed in `tests/test_confirm_weigh_in_script.py::test_confirming_a_flagged_reading_sets_confirmed_fields`). `_upsert_weigh_in()` never writes `bo_confirmed_valid`/`bo_confirmed_at`, so a resync can't silently revert a confirmation — confirmed by `test_resync_never_clears_bo_confirmed_valid_on_already_confirmed_row`. Documented in ADR-039's "Un-flag mechanism" section and the script's own docstring/usage. |
| 7 | Tests cover a normal reading, an obvious outlier, and a borderline case near the threshold | **PASS** | `tests/test_plausibility.py` covers all three directly at the pure-function level, plus explicit boundary pinning (`test_deviation_exactly_at_threshold_is_not_flagged` at exactly 25.0% deviation — strict `>`), insufficient-history, body-fat-only, and None-input cases. `tests/test_normalization_engine.py` and `tests/test_sync_engine.py` re-exercise the same three cases at the `build_canonical_record()`/sync level. |
| 8 | All existing Eufy connector, normalization, and sync engine tests continue to pass | **PASS** | Full suite: **409 passed, 2 failed** (`projects/trainiq && python -m pytest -q`). The 2 failures (`test_peloton_csv_import.py::test_real_csv_import_is_idempotent`, `::test_real_csv_full_regression`) are `FileNotFoundError` on a hardcoded path (`/home/claude/peloton_work/aimea75_workouts.csv`) that only exists on the BO's machine — confirmed identical on `origin/main` via a separate `git worktree` (same 2 failures, same error, 8 other tests in that file pass on both). No regression introduced by this PR. |

## Additional verification beyond the Developer's own tests

- Independently confirmed via `grep -rn "weigh_ins"` that no analytics/trend
  module exists yet in `trainiq/` outside `sync/engine.py` and
  `storage/backfill.py` — the basis for AC3's "vacuously satisfied, contract
  documented" verdict above, rather than taking the PR description's claim
  at face value.
- Read `trainiq/sync/engine.py`'s `_recent_weights_before()` and the
  WEIGH_IN-batch chronological-sort logic directly: confirmed the query
  excludes still-flagged/unconfirmed rows
  (`is_flagged_implausible = 0 OR bo_confirmed_valid = 1`) and is **not**
  filtered by `provider`, matching the architecture doc's explicit
  "relative to the individual athlete, not one device" decision.
  `test_recent_weights_before_excludes_flagged_unconfirmed_readings` covers
  this directly.
- Confirmed `_upsert_weigh_in()`'s INSERT and UPDATE column lists both
  independently exclude `bo_confirmed_valid`/`bo_confirmed_at` by reading
  the SQL literally (not just trusting the docstring claim).
- Spot-checked the backfill's rolling window construction
  (`recent_weights[-DEFAULT_ROLLING_WINDOW_SIZE:]`) — order within the
  slice doesn't affect `median()`, so the lack of explicit
  newest-first ordering there (unlike `_recent_weights_before()`'s
  `ORDER BY timestamp DESC`) does not cause any behavioral difference.
- Confirmed ADR-039 is cross-referenced correctly from `docs/trainiq/adr/INDEX.md`
  and that `BACKLOG.md`'s BL-009 entry correctly notes it as "related, not
  resolved by" this migration, per the requirements' explicit scope cut.

## Scope check

Three-dot diff (`origin/main...issue-38-eufy-weigh-in-plausibility-flagging`)
touches only:
`docs/trainiq/adr/ADR-039-weigh-in-plausibility-flagging.md`,
`docs/trainiq/adr/INDEX.md`,
`projects/trainiq/BACKLOG.md`,
`projects/trainiq/scripts/confirm_weigh_in.py`,
`projects/trainiq/tests/test_confirm_weigh_in_script.py`,
`projects/trainiq/tests/test_normalization_engine.py`,
`projects/trainiq/tests/test_plausibility.py`,
`projects/trainiq/tests/test_storage.py`,
`projects/trainiq/tests/test_sync_engine.py`,
`projects/trainiq/trainiq/normalization/engine.py`,
`projects/trainiq/trainiq/normalization/plausibility.py`,
`projects/trainiq/trainiq/storage/backfill.py`,
`projects/trainiq/trainiq/storage/schema.py`,
`projects/trainiq/trainiq/sync/engine.py`.

All under `projects/trainiq/*` or `docs/trainiq/adr/*` — correctly scoped
to `project:trainiq`. No `connectors/eufy.py` change, matching the
architecture doc's explicit "no change to the Eufy connector" design (the
rule is provider-agnostic, wired in at the shared normalization/sync
layer).

## Verdict

**All 8 acceptance criteria pass.** PR #41 is approved and ready to merge
(left open per protocol — merging `main` is the BO's call).
