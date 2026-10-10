# QA verification: #71 Coach export (PR #77)

Run: `pytest tests/` on PR branch `claude/magical-euler-20uhy4` (472a89b): 692 passed, 10 deselected
(`test_peloton_csv_import.py`, needs a BO-local CSV; pre-existing). Export tests: `tests/test_export_*.py`,
TRIMP tests in `test_training_load.py`, script tests in `test_renormalize.py`.

| AC | Result | Reason |
|---|---|---|
| 1 files + exit 0, <50 KB | Pass | `test_export_run_export.py`: dir created, all files present, each <50 KB on a large dataset |
| 2 profile.md | Pass (gap) | Athlete facts, goal (from config), FTP Test dates present. FTP **value** column is always `-` (no schema column; Architect-sanctioned fallback, never inferred). BO to decide if acceptable |
| 3 recent.md | Pass | `test_export_recent.py`: 30-day window, `-` for missing, pace runs only |
| 4 last_done.md | Pass | `test_export_last_done.py`: class type / instructor / sport rows |
| 5 load.md | Pass | `test_export_load.py`: 12 weeks, CTL/ATL/TSB with explanations |
| 6 weight.md | Pass | `test_export_weight.py`: 26 weeks, flagged excluded, rate and progress |
| 7 performance.md | Pass | `test_export_performance.py`: 20-min power PB, run pace trend |
| 8 dedup | Pass | `test_run_export_dedup_pair_counts_once_in_recent_and_load` |
| 9 TRIMP moving time | Pass | Moving-time, fallback and non-positive tests in `test_training_load.py` |
| 10 renormalize profile | Pass | `test_renormalize_script_passes_stored_profile_so_loads_are_not_reset_to_unknown` + no-profile warning |
| 11 deterministic | Pass | Byte-identical re-run tests; default as-of = latest data date, no wall clock |
| 12 tests per file + fixes | Pass | One test module per file plus fix tests, fixtures only |
