# 71 – Coach export: design

Requirements: `docs/trainiq/requirements/71-coach-export.md` (13 ACs). ADRs: 015/016 (load, never fabricate), 018 (derived metrics), 019 (missing ≠ negative), 020 (profile), 039 (flagged weigh-ins).

## Approach
New package `projects/trainiq/trainiq/export/` — pure, read-only functions over the SQLite DB that return strings; a thin writer does file I/O. `app.py` gains an `export` subcommand (argparse subparser; no-arg and `--configure` behaviour unchanged). No schema migration. Derived values (CTL/ATL/weekly sums, PBs) are computed at export time, never stored (ADR-018).

## Decisions on open questions
- **As-of date:** `--as-of YYYY-MM-DD` optional; default = date of the latest `normalized_activities.start_time` (or weigh-in if later), NOT wall-clock "now". Printed in every file header (`As of: 2026-10-10`). This gives determinism (AC 11/12) with no timestamps. Windows: recent = `[as_of-29d, as_of]`; weeks are ISO weeks ending at as_of's week.
- **JSON:** `recent.json`, `load.json`, `weight.json` only (tabular, useful to LLM tooling); same data as the .md, `json.dumps(sort_keys=True, indent=2)`. Others MD only.
- **Size:** 50 KB is a target. Tables are capped (`recent.md` ≤ 200 rows newest-first, `last_done.md` instructors ≤ 60 by recency); on cap, append one line `Truncated: N older rows omitted`. Tests assert < 50 KB on `synthetic_dataset`-scale data.
- **Goal / DOB / sex source:** profile table already holds sex, DOB, HR, FTP. Goal is NOT in DB: add `config.json` keys `athlete.goal_weight_kg` (72) and `athlete.start_weight_kg` (82.1) with getters in `trainiq/config.py` (`get_weight_goal(config_path)`), config path from `app.CONFIG_PATH`. Missing → `profile.md`/`weight.md` print "goal: not configured" (don't hard-code). BO sets values in config; Dev notes this in README. `export` accepts `--config-path`/`--db-path` defaults like the renormalize script.
- **Sport (run/walk/ride):** ride = `discipline=="cycling"`, run = `"running"`; walk is `discipline=="other"` with `sport_type_raw=="Walk"` (Strava) or Peloton walking class (verify Peloton `activity_title`/`class_type` in a fixture; fall back to `other`). Put in one helper `sport_of(row)`.

## Files / interfaces
New (`trainiq/export/`):
- `__init__.py`: `run_export(conn, out_dir: Path, config_path: Path, as_of: date | None = None) -> list[Path]` — mkdir parents, open `primary_activity_ids()` once, build each doc, write UTF-8 with `\n`, files in fixed order.
- `data.py`: `load_activities(conn, primary_ids, since, until) -> list[Activity]` (dataclass; sorted by `(start_time, id)`), `load_weigh_ins(conn, since, until)` (excludes `is_weight_flagged_implausible=1` unless `bo_confirmed_valid=1`), `resolve_as_of(conn, override)`.
- `profile.py`, `recent.py`, `last_done.py`, `load.py`, `weight.py`, `performance.py`: each `render(...) -> str` (+ `to_json` where applicable).
- `fmt.py`: `md_table(headers, rows)`, `fmt(value, nd)` returning `-` for None (AC 3 of BA doc), pace `m:ss /km`, fixed rounding, so output is byte-stable.

Metric rules:
- Daily load = `training_load` (TSS or TRIMP per row; `None` rows contribute 0 and are counted in a "n activities without load" line, ADR-016). CTL = EWMA 42d, ATL = EWMA 7d (`k=1-exp(-1/τ)`), TSB = CTL−ATL(prior day), seeded from the first activity date and run through as_of. Mixing TSS+TRIMP is as the issue specifies; note the caveat in `load.md`.
- Weekly weight avg: mean of unflagged readings per ISO week; rate = least-squares slope over weeks with data (kg/week); progress = start−latest avg, remaining = latest avg−goal.
- FTP history: activities with `activity_title` matching `FTP Test` (case-insens.) → date + FTP; if the DB has no result field, show date and `-` (do not infer from avg_power; flag as gap to BO in PR).
- PBs: best 20-min power = max `avg_power` among cycling rows with `duration_s>=1200` (note: avg-of-ride proxy, no streams; label it as such); run avg pace per ISO month for trend; longest run/walk distance.

## TRIMP fix (AC 9)
`normalization/load.py::compute_training_load`: `time_s = normalized.get("moving_time_s") or duration_s` (validate `>0`), pass to `_compute_trimp`; apply same choice for TSS? **No** — keep TSS on elapsed duration (power averages include only pedalling on Peloton; out of scope). `engine.py` passes `normalized` which already contains `moving_time_s`. Update module docstring. Existing rows keep old loads until renormalized (see below); call that out in the PR.

## Renormalize script fix (AC 10)
`scripts/renormalize_strava_unofficial.py`: `athlete_profile = load_athlete_profile(conn)` and pass `athlete_profile=` to `renormalize_provider(...)` (verify its param name in `renormalize.py`). Print a warning if None. Docstring note about loads updated.

## Task breakdown
1. TRIMP moving-time change + tests (`tests/test_training_load.py`).
2. Renormalize script fix + test in `tests/test_renormalize.py` (profile preserved loads).
3. `config.py` goal getters + tests.
4. `export/fmt.py`, `data.py` (+ as-of, dedup, flagged filter).
5. Six renderers + JSON, one at a time, each with its test file `tests/test_export_<name>.py`.
6. `run_export` + `app.py` subcommand; README usage.
7. Cross-cutting tests: determinism (run twice, byte-equal), size on large synthetic set, linked pair counted once.

## Tests
Fixture DBs built via `open_db(tmp_path)` + helper inserts (no live accounts). Golden-string assertions for small fixtures; property checks for CTL/ATL (constant daily load converges to that load). Live-data checks (FTP Test titles, Peloton walk class naming) are BO verification.

## Risks
- `app.py` currently has only `--configure`; adding subcommands must keep bare `trainiq` = sync.
- Run-vs-walk detection depends on provider vocab; unknown → listed as `other`.
- Avg-power "20-min PB" is a proxy; labeled as such in output (ADR-028: no overclaiming).
- Existing TRIMP values stale until renormalize; Peloton/other providers need a re-run too (only Strava script exists).
