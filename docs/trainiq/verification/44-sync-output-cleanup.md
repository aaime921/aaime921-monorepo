# Verification: Sync Output Cleanup (noisy re-normalize logging, missing flagged count, duplicate summary lines)

**Issue:** #44
**PR:** #49 (`claude/exciting-knuth-tsapyn` → `main`)
**Requirements:** [`docs/trainiq/requirements/44-sync-output-cleanup.md`](../requirements/44-sync-output-cleanup.md)
**Architecture:** [`docs/trainiq/architecture/44-sync-output-cleanup.md`](../architecture/44-sync-output-cleanup.md)

## Method

Checked out PR branch `claude/exciting-knuth-tsapyn` at `21ef415` (base
`da87e9e`, current `origin/main`). Diffed against the merged architecture
commit (`a135386`) to confirm exact scope, read every changed file in full,
installed the project into a clean venv, ran the full `projects/trainiq`
test suite, ran each of the four new AC-specific tests individually, and
confirmed the three tests the architecture doc required to pass **unmodified**
are byte-for-byte unchanged in the diff and still pass.

## Scope check

`git diff --stat a135386 21ef415`:

```
projects/trainiq/scripts/renormalize_strava_unofficial.py |  7 +++
projects/trainiq/tests/test_app.py                        | 70 ++++++++++++++++++++++
projects/trainiq/tests/test_renormalize.py                 | 38 ++++++++++++
projects/trainiq/tests/test_sync_engine.py                 | 19 ++++++
projects/trainiq/tests/test_training_load.py                | 23 +++++++
projects/trainiq/trainiq/app.py                            | 17 +++---
projects/trainiq/trainiq/logging_setup.py                   |  2 +
projects/trainiq/trainiq/normalization/load.py              |  2 +-
projects/trainiq/trainiq/sync/engine.py                     | 10 +++-
9 files changed, 178 insertions(+), 10 deletions(-)
```

Exactly the files the architecture doc's "Affected components/files" table
scopes this issue to. No connector, no flagging logic, no
`training_load`/TRIMP/TSS computation changed — only the log level of one
call and where the already-computed summary string gets logged/printed, per
the requirements doc's explicit out-of-scope list.

## Acceptance criteria

| AC | Result | Evidence |
|----|--------|----------|
| 1. "training_load unknown" message prints at most once per run to console; per-record detail (if kept) is DEBUG in the diagnostic log only | **PASS** | `scripts/renormalize_strava_unofficial.py` now calls `logging_setup.configure(DEFAULT_LOG_DIR)` as the first statement in `main()`, which removes loguru's default stderr handler entirely (`logger.remove()` in `configure()`) — stricter than "at most once," the message now never reaches the console once configured. `normalization/load.py`'s `_unknown()` drops from `.info` to `.debug`. Verified by `test_unknown_training_load_never_prints_to_console_across_many_records` (50 calls, asserts absent from `capsys` out/err, present 50× in `diagnostic.log`) and `test_renormalize_script_calls_logging_setup_configure_first` (asserts the script's `main()` calls `configure()` with `DEFAULT_LOG_DIR` before any DB access). Both pass. |
| 2. Terminal summary line includes `flagged N implausible`, identical to `summary.log` | **PASS** | `app.py`'s `_log_sync_summary()` success branch now calls `report.echo(r.summary_line)` — the exact string the Sync Engine already logged to `summary.log` — instead of rebuilding a flagged-less copy. Verified by new `test_main_console_summary_includes_flagged_count` (`capsys` asserts `"flagged 1 implausible"` in stdout *and* in `summary.log`'s content) and the pre-existing `test_main_prints_console_summary_of_connector_status_and_sync_results`, confirmed unmodified in the diff, still passing (its substring assertion `"...malformed 0, skipped 0"` still holds since it's a prefix of the new, longer line). |
| 3. Each connector's summary written to `summary.log` exactly once per run | **PASS** | `sync/engine.py`'s `sync_connector()` builds the line once into a local variable, logs it once via `summary_logger()`, and hands the same string back on `ConnectorSyncResult.summary_line` — `app.py` no longer calls `summary_logger()`/`report.info()` a second time for the success path, it only appends to `reporter.lines` for console display (`_Reporter.echo()`, which does not log). Verified by new `test_connector_summary_written_to_summary_log_exactly_once`, which configures a real file sink and asserts `summary_log_content.count("strava: downloaded") == 1`. Passes. |
| 4. Capsys-based test: a connector result with `records_flagged_implausible > 0` → stdout contains `flagged N implausible` with correct count | **PASS** | `test_main_console_summary_includes_flagged_count` (above) does exactly this via a fake WEIGH_IN connector returning 5 baseline + 1 outlier reading, asserting `"flagged 1 implausible" in captured.out`. |
| 5. Log-capture-based test: a sync run → connector's summary text appears in `summary.log` exactly once | **PASS** | `test_connector_summary_written_to_summary_log_exactly_once` (above), using the real file sink rather than an in-memory loguru capture, per the architecture doc's own reasoning for why that distinguishes "logged once" from "logged twice to the same stream." |
| 6. Test covering the re-normalization script's console output (or its logging config) asserts the per-record message doesn't appear more than once across a multi-record run | **PASS** | `test_unknown_training_load_never_prints_to_console_across_many_records` targets the underlying mechanism (`compute_training_load()` under `capsys` after `logging_setup.configure()`), per the architecture doc's explicit rationale for not exercising the script's `main()` wholesale (hardcoded `APP_SUPPORT_DIR`, real DB/connector construction, outside this project's live-verification boundary). Paired with `test_renormalize_script_calls_logging_setup_configure_first`, which closes the gap between "the mechanism suppresses console noise" and "the script actually invokes that mechanism first" — together these cover the AC's intent for the actual script, not just the function it calls. |
| 7. All existing tests in `tests/test_app.py`, `tests/test_sync_engine.py`, `tests/test_logging_setup.py`, `tests/test_renormalize.py` continue to pass | **PASS** | Ran all four files together: `67 passed`. Additionally confirmed by diff inspection that `test_weigh_in_sync_summary_reports_flagged_count`, `test_activity_sync_summary_always_reports_flagged_zero` (`test_sync_engine.py`), and `test_main_prints_console_summary_of_connector_status_and_sync_results` (`test_app.py`) — the three tests the architecture doc specifically required to pass **unmodified** — are untouched in the diff (only new tests appended after them) and pass. |

**All 7 acceptance criteria pass.**

## Full test suite (regression check)

```
cd projects/trainiq && pytest -q
```

Result on PR branch (`21ef415`), in a clean venv built from `pyproject.toml`'s
`[dev]` extra: **437 passed, 2 failed**.

Both failures (`test_peloton_csv_import.py::test_real_csv_import_is_idempotent`,
`::test_real_csv_full_regression`) are `FileNotFoundError` on
`/home/claude/peloton_work/aimea75_workouts.csv` — a live-account CSV fixture
not present in this sandbox. Confirmed **pre-existing and unrelated**: running
just `tests/test_peloton_csv_import.py` against the architecture-doc commit
(`a135386`, i.e. before any of this PR's code changes) reproduces the
identical `2 failed, 8 passed`. This PR touches no CSV-import code or test,
so no new failures are expected or found — matches the Developer's own
reported count (437 passed / 2 pre-existing failures).

## New tests run individually

```
pytest -q tests/test_app.py::test_main_console_summary_includes_flagged_count \
          tests/test_sync_engine.py::test_connector_summary_written_to_summary_log_exactly_once \
          tests/test_training_load.py::test_unknown_training_load_never_prints_to_console_across_many_records \
          tests/test_renormalize.py::test_renormalize_script_calls_logging_setup_configure_first
```

Result: **4 passed**.

## Verdict

✅ All 7 acceptance criteria verified pass. No regressions (pre-existing,
unrelated test failures only, confirmed present on the base commit too). PR
#49 approved.
