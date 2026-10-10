# #79 Coach export: FTP history duplicates / wrong "current", and moving time instead of elapsed

## Summary
Two display bugs in `trainiq export` (#71, PR #78), found by the BO on a copy of the real DB (2026-10-10). The export
otherwise works. (1) `profile.md` → `## FTP history` lists each test twice and puts "current" on the wrong row.
(2) Durations use elapsed time (`duration_s`) although moving time (`moving_time_s`, from #50 streams) exists, so
long walks inflate `recent.md`, `last_done.md` and `load.md`.

## Scope
In: FTP history section of `profile.md`; every place the export shows or sums an activity duration (`recent.md`,
`last_done.md`, `load.md` "Duration (h)", and any other export file doing so).
Out: sync/normalization behaviour, how `moving_time_s` is populated, other export content, schema changes.

## Real data (BO's DB)
FTP tests: 2025-09-18 163 W, 2025-10-15 166 W, 2026-01-28 182 W (best), 2026-09-21 137 W, 2026-09-29 167 W.
Profile FTP = 166 W. Today the section shows these 5 rows, then the same 5 dates again with `-` / `-`
(leftovers from the pre-rework path), and "current" sits on 2025-10-15 (first test whose estimate equals the profile FTP).
Walk 2026-09-26: elapsed 537 min, moving 86 min. `load.md` week of 2026-08-03 shows 29.03 h because of long walks.

## Acceptance criteria
1. Each FTP test appears exactly once in `## FTP history`; no rows with `-` / `-` placeholders for dates that already have a test.
2. The latest test (by date) is marked "latest"; the highest-estimate test is marked "best". One row can carry both.
   With the BO data: 2026-09-29 167 W = latest, 2026-01-28 182 W = best.
3. The profile FTP is shown separately, labelled "current FTP (profile)" (166 W with the BO data). No test row is labelled "current".
4. Wherever the export shows or sums a duration, it uses `moving_time_s` when present (non-null), otherwise `duration_s`.
   Applies to per-activity durations and to `load.md` weekly "Duration (h)" totals.
5. With the BO data the 2026-09-26 walk shows 86 min (not 537), and weekly totals are sums of the moving-or-fallback values.
6. Tests (fixtures only): (a) FTP tests incl. a duplicate-producing history show each date once, latest/best/profile labelled as in 1-3,
   incl. latest ≠ best and profile FTP ≠ latest; (b) a walk with elapsed 537 min and moving 86 min shows 86 in `recent.md`/`last_done.md`
   and contributes 86 min to `load.md`; (c) an activity with null `moving_time_s` falls back to `duration_s`.
7. Existing export tests still pass (update expected output only where it encoded the old behaviour).

## Open questions
- None blocking. Architect to find the source of the leftover `-` rows (the issue attributes them to a pre-rework path).
