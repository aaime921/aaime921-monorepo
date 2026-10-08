# Verification: Stop Treating Eufy's "Not Measured" Body-Fat Sentinel as Implausible

**Issue:** #42
**PR:** #52 (`claude/exciting-knuth-clf2yh` → `main`)
**Requirements:** [`docs/trainiq/requirements/42-eufy-body-fat-zero-not-implausible.md`](../requirements/42-eufy-body-fat-zero-not-implausible.md)
**Architecture:** [`docs/trainiq/architecture/42-eufy-body-fat-zero-not-implausible.md`](../architecture/42-eufy-body-fat-zero-not-implausible.md)

## Method

Checked out PR branch `claude/exciting-knuth-clf2yh` at `a084418` (base
`da87e9e`, current `origin/main`). Read every changed file in full against
the architecture doc's "Affected components/files" table, installed the
project into a clean venv (`python3.11 -m venv` + `pip install -e ".[dev]"`),
ran the full `projects/trainiq` test suite, ran every test file touched by
this PR individually, and — beyond re-running the Developer's own tests —
wrote and ran an independent script exercising the real
`SynchronizationEngine.sync_connector()` path end-to-end with a fresh
`EufyConnector` against a fabricated batch reproducing the BO's evidence
shape (3 baseline readings, 20 good readings with `body_fat=0.0`, 7
genuinely-bad sub-40kg readings), asserting on the live sync result and
final DB rows rather than only on the migration/backfill code path the
Developer's own `test_v4_to_v5_backfill_reproduces_bo_evidence_exactly`
already covers.

## Scope check

```
git diff --stat origin/main...HEAD
```

Touches exactly the files the architecture doc scopes this issue to:
`trainiq/connectors/eufy.py`, `trainiq/normalization/plausibility.py`,
`trainiq/normalization/engine.py`, `trainiq/storage/schema.py`,
`trainiq/storage/backfill.py`, `trainiq/sync/engine.py`,
`scripts/confirm_weigh_in.py`, `docs/trainiq/adr/ADR-039-*.md`,
`projects/trainiq/BACKLOG.md`, plus the corresponding test files. No
unrelated code touched.

## Acceptance criteria

| AC | Result | Evidence |
|----|--------|----------|
| 1. `EufyConnector.normalize()` normalizes `body_fat_pct`/`muscle_mass_pct` of `0.0` to `NULL` before persistence/plausibility | **PASS** | `connectors/eufy.py::normalize()`: `"body_fat_pct": None if raw_body_fat == 0 else raw_body_fat` (and same for `muscle_mass_pct`). `test_eufy_connector.py::test_normalize_maps_zero_body_fat_to_none`, `::test_normalize_maps_zero_muscle_mass_to_none`, `::test_normalize_passes_through_non_zero_body_fat_and_muscle_mass_unchanged` all pass. Independently confirmed via my own end-to-end script: `EufyConnector` fed raw records with `body_fat=0.0` via a fake session produced `body_fat_pct IS NULL` rows in the database after a live `sync_connector()` run. |
| 2. A `NULL` body-composition value never flags the weight; weight plausibility is weight-deviation-only | **PASS** | `plausibility.py::evaluate_weigh_in_plausibility()` returns two fully independent verdicts (`WeighInPlausibility`); the weight branch only ever reads `weight_kg`/`recent_weights_kg`, never `body_fat_pct`. `test_plausibility.py::test_body_fat_zero_does_not_flag_either_axis_and_body_fat_axis_is_plausible` and my independent script (20 good-zero-body-fat readings, all `is_weight_flagged_implausible == 0`) confirm this. |
| 3. If the body-fat floor is kept, it applies only to present, non-null, non-zero values, and flags only the body-fat aspect | **PASS** | `body_fat_pct is not None and body_fat_pct > 0 and body_fat_pct <= body_fat_floor_pct` guard in `evaluate_weigh_in_plausibility()`. `test_plausibility.py::test_body_fat_at_floor_flags_independent_of_weight` and `::test_body_fat_just_above_floor_does_not_flag_on_that_axis` cover this, including the decoupling case (non-zero sub-floor value, e.g. `1.5`, with a normal weight → only the body-fat axis flags) via `test_normalization_engine.py::test_weigh_in_non_zero_sub_floor_body_fat_flags_only_that_axis`. |
| 4. Corrective pass against fixture data reproducing the BO's full evidence (568-row shape: 7 bad + 123 good-zero + normal) leaves exactly the 7 flagged, 123 unflagged | **PASS** | `test_storage.py::test_v4_to_v5_backfill_reproduces_bo_evidence_exactly` inserts rows directly at schema v4 (7 known-bad + 123 representative good-zero + 3 normal), migrates to v5, and asserts the flagged set equals exactly the 7 bad IDs, the 123 good IDs are unflagged with `body_fat_pct IS NULL`. Passes. My independent script exercises the *other* code path (live sync, not migration-backfill) with the same shape (7 bad / 20 good / 3 normal) and gets the same result: `records_flagged_implausible == 7`, all 20 good rows unflagged with `body_fat_pct IS NULL`. |
| 5. Corrective pass never modifies `bo_confirmed_valid`/`bo_confirmed_at` | **PASS** | `decouple_weigh_in_plausibility()`'s UPDATE statements touch only the four flag/reason columns, never `bo_confirmed_valid`/`bo_confirmed_at`. Same test as AC4 confirms a pre-confirmed row (`bad1`) keeps `bo_confirmed_valid == 1` and its original `bo_confirmed_at` timestamp after the migration. |
| 6. Regression: `82.2 kg, body_fat_pct 0.0` → not flagged, `body_fat_pct` stored as `NULL` | **PASS** | `test_eufy_connector.py::test_normalize_maps_zero_body_fat_to_none` and `test_plausibility.py::test_body_fat_zero_does_not_flag_either_axis_and_body_fat_axis_is_plausible` both cover this exact case at the unit level. |
| 7. #38's original 7-row evidence (sub-40kg, `body_fat_pct` 0.0 or 5.0) still flagged — no weakening of the original rule | **PASS** | `test_storage.py::test_v4_backfill_flags_exactly_the_issues_known_bad_rows` (retained, unmodified) and the AC4 test above both still flag exactly the 7 bad rows. My independent live-sync script's 7 bad readings (weights 19.1–35.8kg against an ~83kg baseline, `body_fat` 0.0 or 5.0) all came back `is_weight_flagged_implausible == 1`. |
| 8. ADR-039 updated to describe the corrected rule (missing-vs-zero distinction, decoupling from weight) | **PASS** | `docs/trainiq/adr/ADR-039-weigh-in-plausibility-flagging.md` has a new "Correction (Issue #42)" section: both root causes, the schema v5 column split with an ownership table, the corrective-pass description, and the explicit "no confirm/override path for the body-fat axis" tradeoff note. |
| 9. All existing Eufy connector, normalization, plausibility, and backfill tests continue to pass (adjusted only where they asserted the old behavior) | **PASS** | Full targeted run: `test_plausibility.py test_eufy_connector.py test_normalization_engine.py test_storage.py test_sync_engine.py test_confirm_weigh_in_script.py` → **122 passed**. |

**All 9 acceptance criteria pass.**

## Full test suite (regression check)

```
cd projects/trainiq && pytest -q
```

Result on PR branch (`a084418`), in a clean venv built from `pyproject.toml`'s
`[dev]` extra: **443 passed, 2 failed**.

Both failures (`test_peloton_csv_import.py::test_real_csv_import_is_idempotent`,
`::test_real_csv_full_regression`) are `FileNotFoundError` on
`/home/claude/peloton_work/aimea75_workouts.csv` — a live-account CSV fixture
not present in this sandbox. Confirmed **pre-existing and unrelated**:
running `tests/test_peloton_csv_import.py` against `origin/main` (before
this PR's changes) reproduces the identical `2 failed, 8 passed`. This PR
touches no CSV-import code or test.

## Independent end-to-end verification (beyond the Developer's own tests)

Wrote a standalone script (not part of the PR's test files) that:

1. Builds a fresh v5 database via `schema.migrate()`.
2. Constructs a real `EufyConnector` with a fake HTTP session returning raw
   device-data records shaped exactly like the documented API response
   (`scale_data.weight`/`body_fat`/`muscle_mass`), including 3 baseline
   readings, 20 "good" readings at 82–89kg with `body_fat=0.0` (the BO's
   actual sentinel pattern), and the 7 genuinely-bad sub-40kg readings from
   #38's original evidence.
3. Runs `SynchronizationEngine.sync_connector()` — the real production
   sync path, not the migration-backfill path — and inspects the resulting
   `weigh_ins` rows directly via SQL.

Result: `records_flagged_implausible == 7` (exactly the bad ones), all 20
good-zero-body-fat rows have `is_weight_flagged_implausible == 0` and
`body_fat_pct IS NULL`, and all 7 bad rows have
`is_weight_flagged_implausible == 1`. This independently confirms the fix
holds on the live-sync code path, not only on the one-time migration
backfill the Developer's own fixture test already exercised.

## Verdict

✅ All 9 acceptance criteria verified pass, via both the Developer's own
tests and independent verification against the live sync path. No
regressions (pre-existing, unrelated test failures only, confirmed present
on `origin/main` too). PR #52 approved.
