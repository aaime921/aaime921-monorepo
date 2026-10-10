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

---

# Addendum: PR #82 (second implementation of #72)

A second Developer run opened PR #82 (`claude/magical-euler-8axzle`) for the same issue, duplicating #81.
`pytest tests/` on #82 head (clean venv): 794 passed, 2 failed (`test_peloton_csv_import.py`, needs a BO-local CSV; same pre-existing failures as on #81).
Class tests (`test_export_classes.py`, `test_peloton_class_catalog.py`, `test_export_run_export.py`): 39 passed.

| AC | Result | Reason |
|---|---|---|
| 1-9 | Pass | Same coverage as the table above; `browse_category` enforced in `fetch_archived_classes` |

Same unverified live items as #81 (`data[].id` vs `provider_class_id`; `metadata_mappings` shape).
BO must merge only one of #81 / #82 and close the other (they overlap and will conflict).

---

# Addendum 2: PR #82 rework (list-shaped `metadata_mappings`)

`pytest tests/` in `projects/trainiq` on #82 head 7a2e88b (clean venv): 789 passed, 10 deselected
(`test_peloton_csv_import.py`, needs a BO-local CSV; pre-existing). Class tests: 42 passed.

| Rework AC | Result | Reason |
|---|---|---|
| Parse `class_types`/`instructors` as lists; id->name lookups | Pass | `_build_lookups()` in `classes.py` |
| Filter to `fitness_discipline == "cycling"` and `is_active` | Pass | filter test with inactive/other-discipline noise |
| Fixtures/tests use real list shapes | Pass | dict shape now rejected (regression test) |
| Integration-style test: real-shaped fake catalog gives non-empty md for "Power Zone" | Pass | present in `test_export_classes.py` |
| QA run against real-shaped data | Pass (offline only) | real-shaped fixtures only; no live account access from QA |

Not verified, needs BO live re-run of `trainiq export`:
- Archived `data[].id` equals stored `provider_class_id` (else every row shows `new`).
- Class-type matching uses the catalog `name` exactly (case-insensitive). The live capture shows `"name": "Warm Up Ride"`
  and also a `display_name`. If history tags are display-style ("Warm Up", "Power Zone"), they will not match
  `name` ("Warm Up Ride") and will be listed as unmatched. Probe: `_match_class_type_id("Warm Up", ...)` returns None
  for `name="Warm Up Ride", display_name="Warm Up"`. Check which form the history `class_type` tags use.
