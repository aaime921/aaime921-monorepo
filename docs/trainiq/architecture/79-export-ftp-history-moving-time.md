# 79 – Coach export: FTP history + moving time (design)

Requirements: `docs/trainiq/requirements/79-export-ftp-history-moving-time.md` (also `79-coach-export-ftp-and-moving-time.md`, a duplicate BA run with the same content; the first has the ACs, either may be used). Builds on #71 design `docs/trainiq/architecture/71-coach-export.md`.

## DEPENDENCY: PR #78 is NOT merged
The BA said #78 is merged. It is **open** (branch `claude/magical-euler-y6ywfb`); `projects/trainiq/trainiq/export/` does not exist on `main`. I designed against that branch. The Developer must confirm #78 is merged and branch from the updated `main`; never branch off #78. `blocked:dependency` is set; Team Lead unblocks it on merge.

## Root cause of the duplicate `-` rows
`export/profile.py::_ftp_history` queries `normalized_activities` directly and ignores `primary_ids` (all other renderers filter via `primary_activity_ids()`, see `export/__init__.py`). A linked Peloton/Strava pair of an FTP Test ride yields two rows: Peloton with power, Strava copy with `avg_power` NULL, rendered `-`/`-`. Same defect gives the wrong "current" (first row whose estimate equals the profile FTP). Dev must first reproduce with a failing test (linked pair), not assume.

## Approach
1. **FTP history** (`profile.py`): select only rows with `id IN primary_ids` (pass `primary_ids` from `run_export`; signature becomes `render(conn, config_path, as_of, primary_ids)`). Keep the 15-min minimum and 0.95 × avg_power estimate. Defensive: if two primary rows share a date, keep the one with the larger `avg_power` (non-NULL over NULL). Rows with NULL estimate are not shown.
2. **Labels**: "latest" = max date (ties: highest id), "best" = highest estimate (ties: latest date); one row may carry both (`"latest, best"`). Never "current". Below the table add a separate line `Current FTP (profile): 166 W` (`-` if no profile/FTP). Keep it out of the table so it is independent of any row.
3. **Moving time**: one helper in `export/data.py`:
   `def effective_duration_s(a: Activity) -> Optional[int]: return a.moving_time_s if a.moving_time_s is not None else a.duration_s`
   (treat `0` as present? No: use `is not None`, per AC "non-null"). Use it in `recent.py` (min column + `duration_s` JSON value), `last_done.py`, `load.py` (`duration_h` sum), and `performance.py` (the `>= TWENTY_MIN_S` filter: use effective duration; a 20-min-power proxy should need 20 min of moving time). The FTP 15-min test filter in `profile.py` stays on `duration_s` (raw column), not an Activity; use `COALESCE(moving_time_s, duration_s)` there too, for consistency.
4. **Headers**: relabel columns "Duration (moving)" / JSON key stays `duration_s` (consumers unchanged). Resolves the BA's open question; `load.md` header "Moving time (h)", JSON key `duration_h` unchanged.
5. TRIMP/TSS untouched (out of scope). No schema change.

## Files
Edit: `export/profile.py`, `export/__init__.py`, `export/data.py`, `export/recent.py`, `export/last_done.py`, `export/load.py`, `export/performance.py`; tests `tests/test_export_{profile,recent,last_done,load,performance,run_export}.py`.

## Task breakdown
1. Confirm #78 merged; branch from `main`.
2. Failing test: linked Peloton/Strava FTP Test pair (Strava `avg_power` NULL) → duplicate row; fix via `primary_ids`.
3. Labels + separate profile FTP line; update old "current" expectations (AC 7).
4. `effective_duration_s` + apply in the four renderers; header text.
5. Tests below; run determinism test (repeat run byte-identical).

## Tests (fixtures only)
- Profile: 5 tests, latest ≠ best, profile FTP (166) ≠ latest; each date once; one linked-pair duplicate; labels as in BO data (2026-09-29 latest, 2026-01-28 best); profile line separate.
- Walk elapsed 537 min / moving 86 min: `recent.md`, `recent.json`, `last_done.md` show 86; `load.md` week total includes 86 min (1.43 h), not 537.
- NULL `moving_time_s` → falls back to `duration_s`.
- Existing tests: update only expectations encoding old behaviour.

## Risks
- Existing consumers reading `recent.json` `duration_s` now get moving time (intended, same key).
- Live check by BO: re-run export on the real DB copy; expected 5 FTP rows, 86 min walk, weekly 29.03 h drops.
