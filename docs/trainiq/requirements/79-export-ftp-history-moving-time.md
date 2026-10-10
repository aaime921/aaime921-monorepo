# 79 – Coach export: FTP history fixes + moving time for durations

## Summary
Two display bugs in `trainiq export` (#71, PR #78), found by the BO on a copy of the real DB (2026-10-10). (1) `profile.md` FTP history lists every test twice and marks the wrong row "current". (2) Durations use elapsed time (`duration_s`) although `moving_time_s` exists (#50), inflating walks (537 min vs 86 min moving) and weekly volume (29.03 h).

## Scope
In:
- `profile.md` → `## FTP history`: one row per FTP test; labels; separate profile FTP line.
- Every place the export shows or sums duration (`recent.md`, `last_done.md`, `load.md` "Duration (h)", and any other): prefer `moving_time_s`, else `duration_s`.

Out: TRIMP (already moving-time, #71 AC 9), schema changes, backfilling `moving_time_s`, other export files' layout.

## Acceptance criteria
1. FTP history shows each FTP test exactly once; no rows with `-` / `-` values. For the BO's data: 5 rows (2025-09-18 163, 2025-10-15 166, 2026-01-28 182, 2026-09-21 137, 2026-09-29 167).
2. The most recent test (by date) is labelled "latest"; the highest-value test is labelled "best"; one row may carry both. No row is labelled "current".
3. The profile FTP (166 W) is shown separately, labelled "current FTP (profile)", independent of which test row matches it.
4. Duration shown or summed anywhere in the export uses `moving_time_s` when present (non-null), otherwise `duration_s`; missing both renders `-`.
5. Test: a walk with elapsed 537 min and moving 86 min shows 86 min in `recent.md` and `last_done.md`, and contributes 86 min (not 537) to `load.md` weekly "Duration (h)".
6. Test: an activity with no `moving_time_s` still uses `duration_s`.
7. Test: FTP history with multiple tests yields no duplicate dates, correct latest/best labels, and separate profile FTP line.
8. Export stays deterministic (byte-identical on repeat runs) and existing export tests pass; fixtures only.

## Open questions
- If a duration column header should say "moving" explicitly (Architect to decide; non-blocking).
