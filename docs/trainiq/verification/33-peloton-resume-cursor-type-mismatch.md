# Verification: Fix Peloton Resume Cursor int/str TypeError

**Issue:** #33
**PR:** #35 (`claude/exciting-knuth-20d8o0` → `main`)
**Requirements:** [`docs/trainiq/requirements/33-peloton-resume-cursor-type-mismatch.md`](../requirements/33-peloton-resume-cursor-type-mismatch.md)
**Architecture:** [`docs/trainiq/architecture/33-peloton-resume-cursor-type-mismatch.md`](../architecture/33-peloton-resume-cursor-type-mismatch.md)

## Method

Checked out PR branch `claude/exciting-knuth-20d8o0` at `cb74f1b` in a
separate worktree. Diffed it against `origin/main` (`edbfe9a`) to confirm
exact scope, read both changed source files in full, ran the full `trainiq`
test suite, and — to confirm the new regression tests are not vacuous —
temporarily reverted `extract_resume_cursor()`'s coercion and re-ran the
three new/changed tests to confirm they fail with the exact `TypeError`
from the BO's log, then restored the fix and confirmed green again.

## Scope check

`git diff --stat origin/main cb74f1b`:

```
projects/trainiq/trainiq/connectors/peloton.py |  8 ++--
projects/trainiq/trainiq/sync/engine.py        |  2 +-
projects/trainiq/tests/test_peloton_connector.py | 77 +++++++++++++++
3 files changed, 82 insertions(+), 5 deletions(-)
```

Exactly the files the design doc scopes this fix to. `normalize()` is
untouched; no schema, migration, or other-connector changes.

## Acceptance criteria

| AC | Result | Evidence |
|----|--------|----------|
| 1. `extract_resume_cursor()`, persistence, and reload agree on one type; `SynchronizationEngine`'s comparison never mixes `int`/`str` | **PASS** | `PelotonConnector.extract_resume_cursor()` now returns `str(start_time) if start_time is not None else None`. `sync/engine.py`'s comparison logic is unchanged (not the bug's location, per the design doc); verified by re-running with the old `return normalized.get("start_time")` behavior restored — the exact `int`/`str` `TypeError` from the BO's log reproduces immediately. |
| 2. A pre-existing `str`-persisted checkpoint (e.g. `'1790883770'`) does not raise | **PASS** | `test_end_to_end_sync_with_preexisting_str_checkpoint_does_not_raise` seeds `last_cursor='1790883770'` (str) directly via `_set_checkpoint`, syncs a workout with `start_time=1791134056` (int), asserts `ConnectorState.HEALTHY` and `get_checkpoint("peloton") == "1791134056"`. Passes on the PR branch; confirmed it reproduces the BO's crash when the fix is reverted (see Method). |
| 3. Two consecutive syncs with a checkpoint reload in between, using the issue's logged fixture values, pass | **PASS** | `test_end_to_end_two_consecutive_syncs_cursor_advances_without_error` runs two separate `engine.run_once()` calls against the same DB (first `start_time=1790883770`, second `start_time=1791134056` — the log's own values), asserting `HEALTHY` and the advancing string checkpoint after each. Also confirmed failing (same `TypeError`) with the fix reverted. |
| 4. `DEBUG CURSORS` log no longer at `ERROR` | **PASS** | `sync/engine.py` line ~503: `diagnostic_logger().error(...)` → `diagnostic_logger().debug(...)`. Same call/args, level only; no other line in `engine.py` changed (confirmed via diff). |
| 5. BO recovery note documented | **PASS** | PR description and the design doc's "Recovery note for the BO" section both state: no manual DB fix/reset needed — this is a non-auth failure, so ADR-038's lifecycle policy self-heals `Degraded → Healthy` on the next eligible retry once the fix ships, with the 10-day `RecoveryRequired` escalation caveat called out explicitly. |
| 6. All existing Peloton/sync-engine tests continue to pass | **PASS** | See full run below — `77 passed` in `test_peloton_connector.py` + `test_sync_engine.py`, no existing test modified (diff shows only additions in the test file). |

**All 6 acceptance criteria pass.**

## Full test suite

```
cd projects/trainiq && pytest tests/test_peloton_connector.py tests/test_sync_engine.py -v
```
→ **77 passed** (including the 4 new tests: 2 unit tests on
`extract_resume_cursor()`, 2 end-to-end regression tests on the real
`SynchronizationEngine` + SQLite).

```
cd projects/trainiq && pytest tests/ -q
```
→ **2 failed, 375 passed** on the PR branch. Both failures
(`test_peloton_csv_import.py::test_real_csv_import_is_idempotent`,
`::test_real_csv_full_regression`) are `FileNotFoundError` on
`/home/claude/peloton_work/aimea75_workouts.csv`, a BO-local live-account
CSV fixture not present in this sandbox. Confirmed **pre-existing and
unrelated**: running the same two tests against `origin/main` (`edbfe9a`,
pre-fix) in an identical fresh venv produces the identical `2 failed, 8
passed`. This PR touches no CSV-import code, consistent with AC6.

## Regression-test sanity check (fix reverted)

To confirm the new tests are not vacuously passing, `extract_resume_cursor()`
was temporarily reverted to its pre-fix body
(`return normalized.get("start_time")`) and the three cursor-type tests
re-run:

```
FAILED test_extract_resume_cursor_coerces_int_start_time_to_str
FAILED test_end_to_end_sync_with_preexisting_str_checkpoint_does_not_raise
FAILED test_end_to_end_two_consecutive_syncs_cursor_advances_without_error
TypeError: '>' not supported between instances of 'int' and 'str'
```

This is the exact crash from the BO's diagnostic log. The fix was then
restored and the full targeted suite re-confirmed green (77 passed).

## Verdict

✅ All 6 acceptance criteria verified pass. No regressions (pre-existing,
unrelated CSV-fixture failures only). PR #35 approved.
