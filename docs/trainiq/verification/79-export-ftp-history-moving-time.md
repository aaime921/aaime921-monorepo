# Verification: #79 export FTP history + moving time (PR #80, head 8ef7b88)

Suite on PR branch: 765 passed (`pytest tests --ignore=tests/test_peloton_csv_import.py`).
Extra QA test (not committed; BO figures end-to-end via `run_export`, with linked Strava duplicates of each FTP test): passed, 766 total.

| # | Criterion | Result | Reason |
|---|---|---|---|
| 1 | Each FTP test once, no `-`/`-` rows | PASS | Filtered on `primary_ids`; 5 BO dates each appear once even with linked Strava copies |
| 2 | latest / best labels | PASS | 2026-09-29 167 W = latest, 2026-01-28 182 W = best |
| 3 | Profile FTP separate, no "current" row | PASS | `Current FTP (profile): 166 W`; no row labelled current |
| 4 | moving_time_s preferred, else duration_s | PASS | recent, last_done, load, performance use `effective_duration_s` |
| 5 | BO walk shows 86 min; weekly sums use moving | PASS | recent.json 5160 s, no "537" in recent/last_done; week 2026-09-21 = 1.77 h (20+86 min) |
| 6 | Fixture tests a/b/c | PASS | Dev tests cover FTP dedup/latest≠best/profile≠latest, long-elapsed walk, null-moving fallback |
| 7 | Existing export tests pass | PASS | Full suite green |
