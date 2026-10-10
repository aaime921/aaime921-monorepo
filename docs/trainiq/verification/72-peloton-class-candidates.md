# QA verification: #72 Peloton class candidates (PR #81)

`pytest tests/` in `projects/trainiq` on PR #81 head (clean venv): 797 passed, 2 failed
(`test_peloton_csv_import.py`, needs a BO-local CSV; pre-existing, unrelated).
`test_export_classes.py`: 22 passed. No extra tests needed; every AC is already exercised.

| AC | Result | Reason |
|---|---|---|
| 1 md + json written with #71 files | Pass | `test_export_run_export.py` expects both names |
| 2 durations 20/30/45/60, used types only | Pass | `test_build_unused_types_are_omitted`, unmatched/cap tests |
| 3 row fields, instructor resolved, <=8, newest first | Pass | instructor test; `ROWS_PER_SECTION=8`; API sorts desc |
| 4 `done before (date)` / `new` | Pass | `test_build_marks_done_before_and_new`, latest-date test |
| 5 cycling only | Pass | `test_peloton_connector.py:2101` asserts `browse_category=cycling` |
| 6 1 metadata + <=1 search per duration x type | Pass | `test_build_call_count_is_bounded_by_types_times_durations` |
| 7 failure never breaks export | Pass | None/raise/empty-types/auth/transient/malformed tests; `run_export` wraps `build` |
| 8 deterministic | Pass | `test_build_render_is_byte_equal_on_rerun`; `as_of` injected |
| 9 fixtures shaped like live evidence, required cases | Pass | all five required cases covered |

Not verified (unverifiable offline, flagged by Architect and Developer, needs BO live check):
- Archived `data[].id` equals stored `provider_class_id`. If not, every row shows `new` with no error.
- Raw shape of `/api/ride/metadata_mappings` is modeled as lists of `{id, name}`, not captured live.
