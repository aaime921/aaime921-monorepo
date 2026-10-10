# QA verification: #71 Coach export (PR #78, supersedes #77)

Run 2 (rework). `pytest tests/` in `projects/trainiq` on PR #78 head (60e65c4): 753 passed, 10 deselected
(`test_peloton_csv_import.py`, needs a BO-local CSV; pre-existing).

| AC | Result | Reason |
|---|---|---|
| 1 files + exit 0, <50 KB | Pass | `test_export_run_export.py` |
| 2 profile.md | Pass | Athlete facts, goal, FTP trend now present (see R4) |
| 3 recent.md | Pass | 30-day window, `-` for missing |
| 4 last_done.md | Pass | class type / instructor / sport rows |
| 5 load.md | Pass | 12 weeks, CTL/ATL/TSB explained |
| 6 weight.md | Pass | 26 weeks, flagged excluded, epoch Eufy timestamps parsed |
| 7 performance.md | Pass | 20-min power PB, run pace trend |
| 8 dedup | Pass | linked pair counts once |
| 9 TRIMP moving time | Pass | `test_training_load.py` |
| 10 renormalize profile | Pass | `test_renormalize.py` |
| 11 deterministic | Pass | byte-identical re-runs |
| 12 tests per file + fixes | Pass | |
| R1 shared parser (epoch + ISO, UTC-aware, used everywhere) | Pass | `parse_timestamp` in `export/data.py`; used in data.py and profile.py; no other `fromisoformat` in `export/` |
| R2 real-mix fixtures incl. linked pair | Pass | `test_export_data.py` mixed epoch/ISO, Peloton/Strava pair |
| R3 end-to-end integration | Pass | `test_run_export_succeeds_against_real_shaped_mixed_timestamp_formats` |
| R4 FTP trend (0.95 x avg, round, current/best, <15 min excluded) | Pass | `test_export_profile.py`: 171->162, 175->166 current, 192->182 best, 4-min attempt excluded |

Notes (non-blocking): (a) FTP query orders by raw TEXT `start_time`, so a mixed epoch/ISO set could mis-order rows;
fine for Peloton-only (all epoch) data. (b) The query matches any provider's "FTP Test" title, not only Peloton.
(c) Real-DB run not repeated here (BO-local data); BO should re-run `trainiq export` on the real DB and check the
five expected FTP rows (incl. 2026-09-21 144 -> 137).
