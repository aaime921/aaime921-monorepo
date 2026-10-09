# Verification: Strava Unofficial Start-Time Timezone Fix

**Issue:** #43
**PR:** #53 (`claude/exciting-knuth-t1blwo` → `main`, commit `9993819`)
**Requirements:** `docs/trainiq/requirements/43-strava-unofficial-start-time-timezone-fix.md`
**Architecture:** `docs/trainiq/architecture/43-strava-unofficial-start-time-timezone-fix.md`

## Method

Checked out PR #53's branch (`claude/exciting-knuth-t1blwo`, sha `9993819`).
Reviewed the diff against the architecture doc's design line by line, then
ran the full `pytest` suite for `projects/trainiq`. Environment note: the
sandbox's `keyring`/`cryptography` install was broken independent of this
PR (a Debian system `cryptography` package shadowing the pip one, breaking
every test file that imports `CredentialStore`); fixed locally with `pip
install --ignore-installed cffi cryptography` to actually execute the suite
— this is a sandbox setup issue, not a code change, and affects no file in
the PR's diff.

**Full suite result:** 458 passed, 2 failed. The 2 failures
(`test_peloton_csv_import.py::test_real_csv_import_is_idempotent`,
`::test_real_csv_full_regression`) need a real CSV file
(`/home/claude/peloton_work/aimea75_workouts.csv`) not present in this
sandbox. Confirmed `tests/test_peloton_csv_import.py` has zero diff between
`main` and the PR branch — these failures are pre-existing and unrelated to
this change, consistent with the project's "no live-account/CSV fixture in
CI" boundary.

## Acceptance criteria

| # | Criterion | Result | Evidence |
|---|---|---|---|
| 1 | `start_time` (ISO 8601 with offset) is the first choice, parsed with its offset, stored as UTC | ✅ Pass | `_START_FIELD_CANDIDATES` now `("start_time", "start_date_local_raw", "start_date_raw", "start_date", "start_day")`; `_parse_offset_aware_start_time()` uses `datetime.fromisoformat()` + `.astimezone(timezone.utc)`. `test_normalize_bst_summer_payload_matches_peloton_epoch_for_same_ride` and `test_normalize_start_time_with_colon_offset_also_parses_correctly` pass. |
| 2 | `start_date_local_raw` never treated as UTC; explicit local-timezone conversion on fallback | ✅ Pass | `_convert_local_epoch_to_utc()` strips the UTC label, re-labels with `ZoneInfo(local_timezone)`, converts to UTC — no `tz=timezone.utc` applied to this field anywhere. `test_normalize_falls_back_to_local_raw_with_explicit_timezone_when_start_time_absent` and the winter-unaffected counterpart both pass. |
| 3 | BST and GMT fixtures both normalize to the correct UTC instant matching Peloton's epoch | ✅ Pass | `test_normalize_bst_summer_payload_matches_peloton_epoch_for_same_ride` (shifted case, `18:57:34` recovered from a `19:57:34`-labelled local epoch) and `test_normalize_gmt_winter_payload_matches_peloton_epoch_for_same_ride` (unaffected case) both reproduce the BO's evidence table exactly and pass. |
| 4 | `renormalize_strava_unofficial.py` corrects stored rows from unchanged `raw_activities`, without modifying `raw_activities` | ✅ Pass | `test_renormalize_corrects_bst_shifted_start_time` seeds a `normalized_activities` row at the old wrong `19:57:34` value against an unchanged raw payload, runs `renormalize_provider()`, asserts the corrected `18:57:34` value and that `raw_activities.payload_json` is byte-identical before/after. |
| 5 | `run_dedup_backfill.py` links previously-missed BST pairs without duplicating existing winter pairs | ✅ Pass | `test_backfill_links_previously_missed_bst_pairs_without_duplicating_existing` seeds 2 pre-linked winter pairs + 3 correctly-normalized summer pairs; `run_backfill()` links exactly the 3 new pairs (`linked == 3`, `skipped_existing == 2`), and the 2 winter `dedup_links` rows are untouched (`resolution` unchanged). |
| 6 | `sync_checkpoints.last_cursor` stays consistent so no activity is skipped/re-downloaded at the sync boundary | ✅ Pass | New `recompute_checkpoint_from_normalized()` in `sync/engine.py`, called from the renormalize script after `renormalize_provider()`. `test_stale_cursor_from_old_bug_incorrectly_skips_a_later_bst_activity` reproduces the exact failure (old +1h cursor causes `download()` to wrongly return `[]`); `test_recomputed_cursor_after_renormalization_does_not_skip_that_same_activity` shows the recomputed cursor fixes it (the same activity is now returned). `test_recompute_checkpoint_from_normalized_lowers_an_existing_too_high_cursor` covers the general case directly. |
| 7 | All existing tests continue to pass, buggy fixtures updated | ✅ Pass | Full suite: 458 passed. The only 2 failures are the pre-existing, unrelated CSV-fixture-file-absent tests (file untouched by this PR, identical on `main`). Fixtures in `test_strava_unofficial_connector.py` and `test_renormalize.py` that encoded `start_date_local_raw` as a bare UTC epoch were moved to `start_time`, consistent with the requirements doc's call-out. |

## Additional checks performed (beyond re-running the Developer's own tests)

- Confirmed `start_date_raw`/`start_date`/`start_day` handling is byte-for-byte
  unchanged (out of scope per requirements) by diff inspection — only
  `start_time` and `start_date_local_raw` branches are new.
- Confirmed the fail-loud behavior holds in both new failure modes: an
  offset-naive `start_time` (`test_normalize_start_time_without_offset_raises_rather_than_assuming_utc`)
  and an unresolvable local timezone on the fallback path, both via no
  detectable zone (`test_normalize_local_raw_fallback_with_no_known_timezone_raises_not_silently_utc`)
  and via an invalid configured zone name
  (`test_normalize_local_raw_fallback_with_unknown_zone_name_raises_strava_unofficial_http_error`)
  — none of these silently default to UTC, which is the original bug class
  this fix must not reintroduce.
- Confirmed `scripts/renormalize_strava_unofficial.py`'s new
  `recompute_checkpoint_from_normalized()` call uses the same
  `(provider, strategy)` key the live `SynchronizationEngine` reads/writes
  (`connector.active_strategy().value` → `"unofficial_session"`, matching
  `AcquisitionStrategy.UNOFFICIAL_SESSION` — verified via
  `test_list_acquisition_strategies_reports_unofficial_session`), so the
  recomputed cursor is actually the one a live sync will read next.
- Verified `get_athlete_timezone()`/`set_athlete_timezone()` round-trip and
  preserve pre-existing `config.json` keys (e.g. Eufy's `device_id`) —
  `test_set_athlete_timezone_preserves_other_existing_config_keys`.
- Re-ran the targeted files individually
  (`test_strava_unofficial_connector.py`: 57 passed;
  `test_renormalize.py`, `test_dedup_detector.py`, `test_sync_engine.py`,
  `test_config.py`: all passed) in addition to the full-suite run.

## Verdict

All 7 acceptance criteria verified. PR #53 is approved and ready to merge.
