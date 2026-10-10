# 71 – Coach export (Markdown/JSON for the Grok coach)

## Summary
The BO's Grok coach reads files from the private repo `aaime921/trainiq-data` via its GitHub connector. The export is the coach's whole view of the data. It must answer "I have 45 min on the Peloton / a 1h run, what should I do?" using recent training and load, when each session type was last done, fitness/fatigue, weight trend vs goal, and performance history. Part of Phase 2 (see tracking issue).

## Scope
In:
- New command `trainiq export --out <dir>` writing six Markdown files (JSON companions where useful).
- Two fixes bundled: TRIMP moving-time, and `scripts/renormalize_strava_unofficial.py` athlete profile.

Out:
- Pushing/committing files to `trainiq-data` (delivery mechanism), the Peloton class-candidates ticket (extends this later), live-account verification (tests use fixtures).
- Schema migrations.

## Acceptance criteria
1. `trainiq export --out <dir>` creates `profile.md`, `recent.md`, `last_done.md`, `load.md`, `weight.md`, `performance.md` in `<dir>` (creating the dir if absent); exit code 0. Each file is < 50 KB for the BO's current DB size.
2. `profile.md`: FTP 166 W, HR rest 69 / max 181, male, DOB; weight goal −10 kg from 82.1 kg to ~72 kg; FTP history built from all FTP Test rides (date, value).
3. `recent.md`: every activity in the last 30 days with date, sport, class title, instructor, class type, duration, distance, avg HR, avg power, TSS/TRIMP, and pace (runs only). Missing values render as `-`, never invented.
4. `last_done.md`: one row per Peloton class type, per instructor, and per sport (run, walk, ride) with date of last session plus key metrics (duration, distance, avg HR, avg power, load).
5. `load.md`: weekly volume and load for the last 12 weeks; CTL/ATL/TSB computed from TSS + TRIMP; a one-line plain-language explanation of each of CTL, ATL, TSB.
6. `weight.md`: weekly averages for the last 26 weeks from Eufy, excluding flagged readings; rate of change (kg/week); progress to goal (kg lost, kg remaining).
7. `performance.md`: personal bests and trends, at minimum best 20-min power and average pace on runs over time.
8. Dedup: linked Peloton/Strava pairs count once everywhere (via `primary_activity_ids()`); test with a linked pair showing a single row and single load contribution.
9. TRIMP uses moving time when available, falling back to elapsed only if moving time is absent. Test: a walk and a run with equal elapsed time but different moving time score by moving time; the 9 km walk (was 136) no longer outscores the 8.8 km run (was 69) due to pauses.
10. `scripts/renormalize_strava_unofficial.py` passes the stored athlete profile to normalization; test/fixture shows loads are not reset to unknown after a run of the script.
11. Deterministic: running export twice on the same DB yields byte-identical files (no timestamps of "now" in output; "last 30 days"/weeks anchored to a stated as-of date, e.g. latest data date or an `--as-of` value).
12. Unit tests cover each of the six files and the two fixes, using fixture data only.

## Open questions (non-blocking; Architect to decide)
- As-of date for windows: today vs. `--as-of` option (needed for AC 11).
- JSON companions: which files, if any (BO said "where useful").
- If a file would exceed 50 KB: truncation rule (target, not hard limit, per issue).
