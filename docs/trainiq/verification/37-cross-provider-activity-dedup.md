# QA Verification: Cross-Provider Activity Deduplication (Peloton ↔ Strava)

**Issue:** #37
**PR:** #39 (`issue-37-cross-provider-activity-dedup`, commit `d6816b1`)
**Requirements:** `docs/trainiq/requirements/37-cross-provider-activity-dedup.md`
**Architecture:** `docs/trainiq/architecture/37-cross-provider-activity-dedup.md`

## Method

- Checked out PR branch `issue-37-cross-provider-activity-dedup` (commit `d6816b1`) locally.
- Ran the full existing suite (`pytest`, 395 tests) and the new
  `tests/test_dedup_detector.py` (11 tests) in isolation.
- Read `trainiq/dedup/detector.py` line-by-line against the architecture
  doc's `Interfaces/contracts` and scoring algorithm.
- Independently probed one case not covered by the Developer's own tests
  (see AC1 below) directly against `detector.run_backfill()` in a scratch
  script, rather than only re-running what the Developer wrote.
- Confirmed the "deleted file" shown in a two-dot diff
  (`docs/trainiq/architecture/38-eufy-implausible-weigh-in-flagging.md`) is
  a diff artifact, not an actual change in this PR: a three-dot diff
  (`origin/main...issue-37-cross-provider-activity-dedup`) shows only the 4
  new files this PR actually adds. Issue #38's architecture doc landed on
  `main` after this branch was cut; the PR never touches it.

## Acceptance criteria — pass/fail

| # | Criterion | Result | Evidence |
|---|---|---|---|
| 1 | Cross-provider pairs within the time window **and** agreeing on duration and/or discipline are scored; time-proximity alone is never sufficient | **PASS** | `_score_pair()` returns `None` (no `dedup_links` row at all) when neither `duration_match` nor `discipline_match` is true. Verified both via the Developer's `test_flagged_not_merged_below_threshold` (passes gate, below threshold → flagged) and an independent probe: two activities at the *same* start time but disagreeing on both duration and discipline produced `candidate_pairs=0` / 0 `dedup_links` rows. |
| 2 | Every scored pair gets a numeric confidence score; a stated, documented threshold (auto-link vs. flagged) is enforced | **PASS** | `AUTO_LINK_THRESHOLD = 0.6`, documented in the module docstring and as an inline comment with rationale. `test_exact_start_time_match_is_linked`, `test_near_match_within_tolerance_is_linked`, `test_flagged_not_merged_below_threshold` all assert the numeric `confidence_score` and the threshold boundary. |
| 3 | Linked and flagged pairs both written to `dedup_links`; `raw_activities`/`normalized_activities` never deleted or modified | **PASS** | Code only `SELECT`s from `normalized_activities` and `INSERT`s into `dedup_links` (confirmed by reading `detector.py` in full — no `UPDATE`/`DELETE` statement anywhere in the module). `tests/test_architecture_invariants.py` passes unchanged, confirming the single-writer invariant on `normalized_activities`/`weigh_ins` holds. |
| 4 | Auto-linked `resolution` records the primary side per the BO's rule (Peloton primary for rides; Strava contributes GPS/distance), documented in code | **PASS** | `primary_provider()` implements the rule exactly; module docstring documents both the BO's literal rule and the `strava`-vs-`strava_unofficial` extension with its rationale (capability tier). `test_near_match_within_tolerance_is_linked` asserts `resolution == "linked:primary=peloton"`. |
| 5 | Runs over existing data (not just new syncs); reproduces the BO's reported pairs via the fixture | **PASS** (scaled stand-in, per requirements doc's explicit scope) | `run_backfill(conn)` operates on whatever is already in `normalized_activities` — no live-sync dependency. `test_full_fixture_scenario_six_pairs_linked` runs it against all 12 rows `scenario_cross_provider_duplicates()` produces and asserts exactly 6 linked, 0 flagged, 0 skipped. Running against the BO's real 54-pair production DB is explicitly out of scope for this pipeline (no live DB access in CI) per the requirements doc — this is documented, not silently skipped. |
| 6 | A flagged pair's `resolution` is distinguishable from an auto-linked one, independently queryable | **PASS** | `RESOLUTION_FLAGGED = "flagged:needs_review"` vs. `RESOLUTION_PREFIX_LINKED = "linked:primary="` — distinct, prefix-queryable (`resolution LIKE 'linked:%'` vs. `= 'flagged:needs_review'`). `test_flagged_not_merged_below_threshold` and `test_primary_activity_ids_excludes_only_auto_linked_secondary` exercise both branches. |
| 7 | Tests cover: exact match, near match, non-match, epoch-int-vs-ISO-8601 | **PASS** | All four present and passing: `test_exact_start_time_match_is_linked`, `test_near_match_within_tolerance_is_linked`, `test_non_match_two_different_activities_same_day`, `test_epoch_int_vs_iso8601_format_handling` — the last one verified against the actual stored shape (`str(epoch_int)` via real `INSERT`, not a hand-constructed value), matching how `_upsert_normalized_activity` really persists it. |
| 8 | All existing tests continue to pass | **PASS** | Full suite: 393 passed, 2 failed. The 2 failures (`tests/test_peloton_csv_import.py::test_real_csv_import_is_idempotent`, `::test_real_csv_full_regression`) are a hardcoded path to a file that only exists on the BO's machine (`/home/claude/peloton_work/aimea75_workouts.csv`) — confirmed identical on `origin/main` via a separate git worktree, with zero relation to this PR's files. No regression introduced by this change. |

## Additional verification beyond the Developer's own tests

- Independently confirmed the AC1 primary gate (see row 1 above) with a
  case the Developer's suite doesn't explicitly construct: two activities
  at the *identical* normalized start time, differing on **both** duration
  and discipline. Result: `candidate_pairs == 0`, zero `dedup_links` rows —
  correct per the spec ("a time-proximity match alone is never
  sufficient").
- Confirmed `normalized_activities.duration_s`/`discipline` are `NOT NULL`
  in the schema, so `_score_pair()`'s unconditional `abs(x.duration_s -
  y.duration_s)` cannot hit a `None`/`TypeError` path on real data.
- Confirmed `dedup_links`'s schema (`activity_id_a`, `activity_id_b`,
  `confidence_score`, `resolution`, with FK references to
  `normalized_activities`) matches exactly what the design and tests
  assume — no migration present or needed.
- Re-ran `tests/test_architecture_invariants.py` and
  `tests/test_synthetic_dataset.py` in isolation: both pass, confirming no
  interaction between this new module and either invariant.

## Scope check

Diff (three-dot, against the actual branch point) touches only:
`projects/trainiq/trainiq/dedup/__init__.py`,
`projects/trainiq/trainiq/dedup/detector.py`,
`projects/trainiq/scripts/run_dedup_backfill.py`,
`projects/trainiq/tests/test_dedup_detector.py` — all within
`projects/trainiq/*`, correctly scoped to `project:trainiq`. No connector,
sync engine, normalization engine, or schema file touched, matching the
architecture doc's stated design.

## Verdict

**All 8 acceptance criteria pass.** PR #39 is approved and ready to merge
(left open per protocol — merging `main` is the BO's call).
