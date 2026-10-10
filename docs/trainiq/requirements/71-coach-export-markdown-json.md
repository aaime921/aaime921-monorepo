# Requirements: Coach export (Markdown/JSON summaries for the Grok coach)

**Issue:** #71
**Related:** Phase 2 tracking issue; Peloton class candidates ticket (extends this export); #37 (dedup)

## Summary

The Grok coach reads files from the private repo `aaime921/trainiq-data` and has
no other view of the data. The BO wants `trainiq export --out <dir>` to write
compact, deterministic Markdown (plus JSON where useful) so the coach can answer
"I have 45 min on the Peloton / 1 h for a run, what should I do?" from recent
training, last-done dates, fitness/fatigue, weight trend vs goal and
performance history. The issue also bundles two bug fixes in load calculation.

## Scope

In:
- New `export` command writing six files (below), each target < 50 KB.
- Fix TRIMP to use moving time where available, else elapsed.
- Fix `scripts/renormalize_strava_unofficial.py` to pass the stored athlete profile.

Out:
- Pushing/committing the files to `trainiq-data` (BO/ops, not this issue).
- Peloton class candidate suggestions (separate ticket).
- Schema migrations; backfill against real accounts.
- Inventing data: absent fields stay absent/"n/a" in the output, never estimated.

## Acceptance criteria

1. `trainiq export --out <dir>` creates `<dir>` if missing and writes `profile.md`, `recent.md`, `last_done.md`, `load.md`, `weight.md`, `performance.md`. JSON companions are optional per file ("where useful").
2. Each file is < 50 KB for the BO's current dataset size; tests assert size on a large synthetic dataset.
3. `profile.md`: FTP 166 W, HR rest 69 / max 181, sex male, DOB, from the stored athlete profile; weight goal −10 kg from 82.1 kg to ~72 kg; FTP history listing every FTP Test ride (date, resulting FTP).
4. `recent.md`: every activity in the last 30 days with date, sport, class title, instructor, class type, duration, distance, avg HR, avg power, TSS/TRIMP, and pace for runs. Missing values shown as "n/a".
5. `last_done.md`: for each Peloton class type, each instructor, and each sport (run, walk, ride): date of last session plus key metrics (duration, distance, avg HR/power, load).
6. `load.md`: weekly volume and load for the last 12 weeks; CTL/ATL/TSB (from TSS + TRIMP) with a one-line explanation of each.
7. `weight.md`: weekly averages for the last 26 weeks from Eufy, excluding flagged readings; rate of change (kg/week); progress to the −10 kg goal (kg lost, kg remaining).
8. `performance.md`: personal bests and trends, including 20-min power and average pace on runs.
9. Dedup: linked Peloton/Strava pairs appear and count once everywhere, per `primary_activity_ids()`.
10. TRIMP uses `moving_time_s` when present, else elapsed duration. Test: a walk and run with known HR/times score by moving time; the 9 km walk (136) vs 8.8 km run (69) discrepancy no longer arises from elapsed time.
11. `scripts/renormalize_strava_unofficial.py` passes the stored athlete profile so loads are not reset to unknown; test covers a renormalize run preserving computed loads.
12. Deterministic: exporting twice from the same DB yields byte-identical files (no generation timestamps; stable ordering). Test compares two runs.
13. Unit tests exist for each of the six files using fixture data (CI has no live accounts).

## Open questions (non-blocking)

- Exact "key metrics" columns for `last_done.md` and the PB definitions in `performance.md` beyond 20-min power and run pace are left to the Architect; BO can adjust in review.
- Where DOB/sex/goal values come from if not already in the stored profile (e.g. goal 72 kg): Architect to confirm the source; if absent from the DB, the Architect should propose where they are configured rather than hard-coding.
