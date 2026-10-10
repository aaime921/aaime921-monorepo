# Verification: Map `strava_unofficial` Activities to Canonical Discipline Taxonomy

**Issue:** #36
**PR:** #40 (`claude/exciting-knuth-spcs0e` → `main`)
**Requirements:** [`docs/trainiq/requirements/36-strava-unofficial-discipline-mapping.md`](../requirements/36-strava-unofficial-discipline-mapping.md)
**Architecture:** [`docs/trainiq/architecture/36-strava-unofficial-discipline-mapping.md`](../architecture/36-strava-unofficial-discipline-mapping.md)

## Method

Checked out PR branch `claude/exciting-knuth-spcs0e` at `fc43715`. Diffed it
against `origin/main` (`1d5c466`) to confirm exact scope, read every changed
source file in full (`taxonomy.py`, `sync/engine.py`, `renormalize.py`,
`scripts/renormalize_strava_unofficial.py`), ran the full `trainiq` test
suite, then ran the issue-specific tests individually and inspected their
assertions against each acceptance criterion rather than trusting the
Developer's own summary.

Note: this sandbox's system Python 3.11 `keyring` backend initially crashed
(`cryptography`/`_cffi_backend` native-extension issue, not related to this
PR) when collecting tests that use the `in_memory_keyring` fixture, producing
199 unrelated errors on the first run. Installing `cffi` fixed the sandbox
and the suite ran cleanly after — flagging this since it affected test
collection, but it is an environment issue, not a PR defect.

## Scope check

`git diff --stat origin/main...claude/exciting-knuth-spcs0e`:

```
docs/trainiq/architecture/36-strava-unofficial-discipline-mapping.md | 293 +++
docs/trainiq/requirements/36-strava-unofficial-discipline-mapping.md |  51 ++
projects/trainiq/scripts/renormalize_strava_unofficial.py            |  88 ++
projects/trainiq/tests/test_architecture_invariants.py               |   6 +
projects/trainiq/tests/test_renormalize.py                           | 204 ++++
projects/trainiq/tests/test_strava_unofficial_connector.py           |  59 +
projects/trainiq/tests/test_taxonomy.py                              |  61 +
projects/trainiq/trainiq/normalization/renormalize.py                | 112 ++
projects/trainiq/trainiq/normalization/taxonomy.py                   |  10 +-
projects/trainiq/trainiq/sync/engine.py                              |  96 +--
10 files changed, 938 insertions(+), 42 deletions(-)
```

Confined to `projects/trainiq/` and `docs/trainiq/` (plus the two design docs
carried onto this branch, since they hadn't reached `main` otherwise — no
other project's files touched). `strava_unofficial.py`'s extraction logic is
untouched, per scope.

## Acceptance criteria

| AC | Result | Evidence |
|----|--------|----------|
| 1. `map_discipline("strava_unofficial", "Run")` → `RUNNING` | **PASS** | `_STRAVA_MAP["Run"] = Discipline.RUNNING` (`taxonomy.py:57`), reused via `_PROVIDER_MAPS["strava_unofficial"] = _STRAVA_MAP` (`taxonomy.py:97`). Directly asserted by `test_strava_unofficial_run_maps_to_running` and end-to-end by `test_run_normalizes_and_maps_to_running_end_to_end`. Both pass. |
| 2. `map_discipline("strava_unofficial", "Ride")` → `CYCLING` | **PASS** | `_STRAVA_MAP["Ride"] = Discipline.CYCLING` (`taxonomy.py:52`). `test_strava_unofficial_ride_maps_to_cycling` passes. |
| 3. `map_discipline("strava_unofficial", "Workout")` → `STRENGTH` | **PASS** | `_STRAVA_MAP["Workout"] = Discipline.STRENGTH` (`taxonomy.py:61`). `test_strava_unofficial_workout_maps_to_strength` and `test_workout_normalizes_and_maps_to_strength_end_to_end` pass. |
| 4. `map_discipline("strava_unofficial", "Walk")` → `OTHER`, explicit, no "unrecognized" warning | **PASS** | `_STRAVA_MAP["Walk"] = Discipline.OTHER` (`taxonomy.py:64`) is a named key, so `map_discipline()`'s `canonical is None` branch (the only place the "unrecognized" warning fires, `taxonomy.py:122-132`) is never reached. `test_strava_unofficial_walk_maps_to_other_explicitly_not_via_warning` captures real `loguru` output via a stream handler (not `caplog`, which this codebase's own tests note doesn't capture `loguru`) and asserts neither "unrecognized" nor "no discipline mapping defined for provider" appears. Passes. Also confirmed the documented side effect: `test_strava_walk_maps_to_other_explicitly_not_via_warning` shows official `strava`'s `Walk` is now equally explicit. |
| 5. End-to-end fixture test: `display_type="Mountain Bike Ride"`, `activity_type_display_name="Ride"` → `normalize()` → `map_discipline()` → `CYCLING` | **PASS** | `test_mountain_bike_ride_normalizes_and_maps_to_cycling_end_to_end` builds exactly this raw payload, runs it through the real `StravaUnofficialConnector.normalize()`, asserts `normalized["discipline_raw"] == "Ride"` (proving the connector's existing `activity_type_display_name`-over-`display_type` preference, not a map entry, is what resolves this), then asserts `map_discipline(...) == Discipline.CYCLING`. Confirmed no `"Mountain Bike Ride"` key exists in `_STRAVA_MAP`. Passes. |
| 6. `MAPPING_VERSION` bumped from 1, dated comment | **PASS** | `taxonomy.py:47-49`: `MAPPING_VERSION = 2  # 2026-10-06: register strava_unofficial (...) — issue #36`. |
| 7. Idempotent re-normalization mechanism for the 352 existing rows, from `raw_activities`, never touching raw payloads, documented for the BO | **PASS** | `renormalize_provider()` (`normalization/renormalize.py`) reads `raw_activities` for a given provider, re-derives canonical records via `connector.normalize()` + `build_canonical_record()`, and persists via the extracted `upsert_normalized_activity()`. `test_renormalize_corrects_discipline_for_every_stale_row` seeds 5 fixture rows (Run/Ride/Walk/Mountain-Bike-Ride/Workout) stuck at `discipline="other"` and confirms each resolves to its expected discipline after one pass. `test_renormalize_never_modifies_raw_activities` confirms `raw_activities.payload_json` is byte-for-byte unchanged. `test_renormalize_is_idempotent_on_second_run` runs the pass three times total and confirms identical `normalized_activities` rows after the 1st and 3rd runs, with `inserted=0, updated=5` on every run after the first. `scripts/renormalize_strava_unofficial.py` wires a real DB connection + connector and documents the exact BO-run command in its own docstring and in PR #40's description. All four tests pass. |
| 8. Existing `taxonomy.py`/`strava_unofficial`/`sync_engine` tests pass unmodified | **PASS** | See regression check below — `test_sync_engine.py`'s 33 tests (covering `_upsert_normalized_activity`'s insert/update paths) pass unmodified against the behavior-preserving extraction into module-level `upsert_normalized_activity()`. Confirmed by reading the diff: the extracted function's body is identical to the original method's, the method is now a one-line delegator. |

**All 8 acceptance criteria pass.**

## Regression check (full test suite)

```
cd projects/trainiq && python3 -m pytest -q
```

Result on PR branch (`fc43715`): **2 failed, 395 passed**.

Both failures (`test_peloton_csv_import.py::test_real_csv_import_is_idempotent`,
`::test_real_csv_full_regression`) are `FileNotFoundError` on
`/home/claude/peloton_work/aimea75_workouts.csv` — a BO-local CSV fixture not
present in this sandbox. Confirmed **pre-existing and unrelated**: this PR
touches no code in `csv_import/` or its tests (see scope check above), and
the same two tests fail identically for the same reason regardless of which
commit is checked out.

Also separately ran, and read the assertions of, every issue-#36-specific
test file (`test_taxonomy.py`, `test_strava_unofficial_connector.py`,
`test_renormalize.py`, `test_architecture_invariants.py`) rather than relying
on the full-suite summary alone — all pass (47 + 4 + 2 relevant new/changed
tests, plus the full existing suites for each file).

## What was NOT verified (out of scope, per requirements/architecture docs)

- Running `scripts/renormalize_strava_unofficial.py` against the BO's real
  production database with the real 352 rows — explicitly a BO-side manual
  step per both docs' out-of-scope sections, not a pipeline deliverable.

## Verdict

✅ All 8 acceptance criteria verified pass. No regressions (pre-existing,
unrelated test failures only, matching the Developer's own count). PR #40
approved.
