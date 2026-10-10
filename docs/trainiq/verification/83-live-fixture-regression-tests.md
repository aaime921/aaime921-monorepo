# #83 Verification (QA, PR #86)

Run: `pytest projects/trainiq/tests/` on PR branch → 821 passed, 2 failed (pre-existing: `test_peloton_csv_import.py` needs a BO-local CSV, `FileNotFoundError`). `test_live_fixtures.py` → 12 passed. Fixtures untouched (PR diff is only the new test module). Sandbox note: keyring auto-detect panics here; ran with `PYTHON_KEYRING_BACKEND=keyring.backends.fail.Keyring`.

| AC | Result | Reason |
|---|---|---|
| 1 One+ test per fixture | PASS | 10 cases + guard test that every fixture file is referenced |
| 2 Peloton workouts page / normalize | PASS | distance_m None without summary; miles→m with performance summary; epoch start_time sane |
| 3 Session → class ride_id | PASS | resolves `ride_id`, not session `id` |
| 4 Ride details metadata | PASS | nested `ride` + top-level `class_types` |
| 5 Performance graph by slug | PASS | avg/max HR, max power, distance parsed |
| 6 Class-catalog builder | PASS | metadata_mappings + archived yield candidate(s); #82 merged |
| 7 Strava normalize | PASS | UTC from `start_time`, not `start_date_local_raw` |
| 8 Strava streams | PASS | derived avg HR, moving time, pace |
| 9 Eufy | PASS | deci-kg→kg; body_fat 0→NULL |
| 10 DB export | PASS | mixed epoch/ISO timestamps parse and render |
| 11 Read-only, no network | PASS | socket connect patched to fail; no fixture changes |
| 12 Fix code if fixture fails | PASS | no failures, no production code changed |
