# Architecture: Sync Output Cleanup

**Issue:** #44
**Requirements:** `docs/trainiq/requirements/44-sync-output-cleanup.md`
**Related:** #25 (console summary), #36 (re-normalization script), #38 (flagged count), #42 (over-flagging bug this noise hid)

## Approach

Two independent root causes, two independent fixes. Neither touches
flagging logic, training-load computation, or any counter definition —
both are purely about where existing, already-correct values get logged
and printed.

**1. Console flood from `scripts/renormalize_strava_unofficial.py`.**
The script never calls `trainiq.logging_setup.configure()`, so loguru's
default handler (stderr, unfiltered, added automatically on import) stays
live for the whole run, and every `diagnostic_logger().info(...)` call —
including `_unknown()` in `trainiq/normalization/load.py`, invoked once per
re-normalized record — prints straight to the terminal. Fix at both ends,
per the requirements doc's "either/both" framing:
- The script calls `configure()` exactly like `trainiq/app.py main()` does,
  as its first action. This removes the default handler, so nothing
  logged through `summary_logger()`/`diagnostic_logger()` reaches the
  console again, for this script or any future one built the same way.
- `_unknown()`'s call drops from `.info` to `.debug`. This is per-record
  detail with no operator-facing significance (three fixed, known reasons,
  none actionable) — it belongs at the diagnostic log's lowest severity,
  not alongside real INFO-level sync events. This is a correctness fix
  independent of the `configure()` gap: it's what AC1 asks for literally
  ("DEBUG level in the diagnostic log"), and it's defense in depth for the
  diagnostic log's own readability even once the script is fixed.

**2. Duplicate `summary.log` lines / missing `flagged` on console.**
Traced to two independent writers of the same per-connector result:
`SynchronizationEngine.sync_connector()` logs the full line (with
`flagged N implausible`) directly via `summary_logger()`; `trainiq/app.py`'s
`_log_sync_summary()` then logs a *second*, hand-rebuilt line (without
`flagged`) through `_Reporter`, which is also the only one that reaches
`reporter.lines` and therefore the console.

The fix is **not** "delete the Sync Engine's own logging and make
`_log_sync_summary()` the only writer" (the requirements doc's second open
question, option B). Two existing tests —
`test_weigh_in_sync_summary_reports_flagged_count` and
`test_activity_sync_summary_always_reports_flagged_zero` in
`tests/test_sync_engine.py` — call `engine.run_once(...)` directly, with no
`trainiq.app` involved at all, and assert the flagged-count line appears in
a raw loguru capture. `scripts/benchmark.py` also calls `run_once()`
directly. The Sync Engine logging its own summary is a load-bearing
contract for every caller that isn't `trainiq/app.py`, not an accident to
delete. AC7 requires these tests keep passing unmodified.

Instead: the Sync Engine keeps logging its line exactly as today (one
`summary_logger().info(...)` call, unchanged text, unchanged tests), and
also hands that **exact same string** back on `ConnectorSyncResult`.
`trainiq/app.py` stops rebuilding its own version of the line — it just
echoes the Sync Engine's string to the console, without logging it again.
Net effect: `summary.log` gets the line once (only the Sync Engine ever
writes it), the console gets it too (now including `flagged`), and no
caller's behavior changes except `trainiq/app.py`'s console output.

The `error` / `skipped_reason` branches of `_log_sync_summary()` are
**not** touched — see Risks/tradeoffs.

## Affected components/files

| File | Change |
|---|---|
| `trainiq/logging_setup.py` | New `DEFAULT_LOG_DIR` constant (moved out of `trainiq/app.py`, see below) — this is fundamentally a logging-location concern, not a composition-root one, and the re-normalize script needs it without importing all of `app.py`. |
| `trainiq/app.py` | `LOG_DIR = DEFAULT_LOG_DIR` (import, not a redefinition — name kept for anything already referencing `trainiq.app.LOG_DIR`). `_Reporter` gains an `echo()` method. `_log_sync_summary()`'s success branch calls `report.echo(r.summary_line)` instead of rebuilding the line. |
| `trainiq/sync/engine.py` | `ConnectorSyncResult` gains `summary_line: Optional[str] = None`. `sync_connector()`'s success path builds the line into a local variable once, logs it exactly as today, and sets it on the returned result. |
| `trainiq/normalization/load.py` | `_unknown()`: `diagnostic_logger().info(...)` → `diagnostic_logger().debug(...)`. |
| `scripts/renormalize_strava_unofficial.py` | Imports `configure` and `DEFAULT_LOG_DIR` from `trainiq.logging_setup`; calls `configure(DEFAULT_LOG_DIR)` as the first statement in `main()`, before the `print(f"Database: ...")` line. |
| `tests/test_app.py` | New test for AC4; existing `test_main_prints_console_summary_of_connector_status_and_sync_results` needs no change (substring assertion still holds — see Risks/tradeoffs). |
| `tests/test_sync_engine.py` | New test for AC5. Existing flagged-count tests (line ~1208, ~1228) need no change. |
| `tests/test_renormalize.py` or `tests/test_logging_setup.py` | New test for AC6 (see Test strategy notes — targets the mechanism, not the script's `main()` wholesale). |

No change to `trainiq/normalization/engine.py`, any connector, `ConnectorSyncResult`'s other fields, or anything under `records_flagged_implausible`'s computation.

## Interfaces/contracts

### `trainiq/logging_setup.py`
```python
DEFAULT_LOG_DIR: Path = Path.home() / "Library" / "Logs" / "TrainIQ"
```

### `trainiq/sync/engine.py`
```python
@dataclass
class ConnectorSyncResult:
    ...
    # Set only on the success path (mirrors the other per-run counters);
    # stays None for the error/skipped_reason branches, which build their
    # own separate text in trainiq/app.py and are unaffected by this issue.
    summary_line: Optional[str] = None
```

`sync_connector()`'s success path (replacing the current inline f-string
passed straight to `summary_logger().info(...)`):
```python
summary_line = (
    f"{provider}: downloaded {len(raw_records)}, "
    f"inserted {inserted_count}, updated {updated_count}, "
    f"malformed {skipped_malformed}, skipped {skipped_no_external_id}, "
    f"flagged {flagged_implausible_count} implausible"
)
summary_logger().info(summary_line)
...
return ConnectorSyncResult(
    provider=provider, state=connector.get_state(),
    records_upserted=count, duration_s=time.monotonic() - start,
    records_inserted=inserted_count, records_updated=updated_count,
    records_malformed=skipped_malformed, records_skipped=skipped_no_external_id,
    records_flagged_implausible=flagged_implausible_count,
    summary_line=summary_line,
)
```
The BL-006 line (`"...incremental filtering unavailable..."`, logged
separately a few lines later) is untouched — it was never duplicated and
isn't part of this issue's scope.

### `trainiq/app.py`
```python
class _Reporter:
    ...
    def echo(self, msg: str) -> None:
        """Append to self.lines for console printing only. Used when the
        message was already logged elsewhere (the Sync Engine's own
        summary_logger call) so it isn't written to summary.log twice."""
        self.lines.append(msg)
```

`_log_sync_summary()`:
```python
def _log_sync_summary(result, reporter=None) -> None:
    report = reporter if reporter is not None else _Reporter(summary_logger())
    for r in result.connector_results:
        if r.error:
            report.warning(f"{r.provider}: {r.state.value} — {r.error}")
        elif r.skipped_reason:
            report.info(f"{r.provider}: skipped this run — {r.skipped_reason}")
        else:
            report.echo(r.summary_line)
```

### `scripts/renormalize_strava_unofficial.py`
```python
from trainiq.logging_setup import DEFAULT_LOG_DIR, configure

def main() -> int:
    configure(DEFAULT_LOG_DIR)
    import argparse
    ...
```

## Task breakdown

1. Add `DEFAULT_LOG_DIR` to `trainiq/logging_setup.py`.
2. In `trainiq/app.py`, replace the `LOG_DIR = Path.home() / ...` literal with `LOG_DIR = DEFAULT_LOG_DIR` (imported) — no other line in `app.py` changes.
3. In `trainiq/normalization/load.py`, change `_unknown()`'s log call from `.info` to `.debug`.
4. In `trainiq/sync/engine.py`: add the `summary_line` field to `ConnectorSyncResult`; refactor `sync_connector()`'s success path to build the line once into a local variable, log it unchanged, and pass it into the returned result.
5. In `trainiq/app.py`: add `_Reporter.echo()`; change `_log_sync_summary()`'s success (`else`) branch to call `report.echo(r.summary_line)` instead of rebuilding the message.
6. In `scripts/renormalize_strava_unofficial.py`: import `configure` and `DEFAULT_LOG_DIR`; call `configure(DEFAULT_LOG_DIR)` as the first line of `main()`.
7. Run the full existing suite first, before writing anything new. Confirm these pass **unmodified**: `test_weigh_in_sync_summary_reports_flagged_count`, `test_activity_sync_summary_always_reports_flagged_zero`, `test_main_prints_console_summary_of_connector_status_and_sync_results`. If any of them needed edits to pass, the implementation has drifted from this design — stop and reconcile before continuing, don't adjust the test to match.
8. Add the three new tests for AC4/AC5/AC6 (see Test strategy notes).
9. Update `scripts/renormalize_strava_unofficial.py`'s module docstring: add one line noting it now configures logging like `trainiq/app.py`, so the "Note on training_load" paragraph already there isn't read as the only logging-relevant note.

## Test strategy notes

- **AC4 (console includes `flagged`):** extend `tests/test_app.py` along the existing `test_main_prints_console_summary_of_connector_status_and_sync_results` pattern (`_make_fake_sync_connector`), but give the fake connector's `download()` data that `build_canonical_record()` will flag (reuse the `WEIGH_IN` + outlier-reading fixtures already in `tests/test_sync_engine.py`, e.g. `MockWeighInConnector` + `_OUTLIER_READING`, wired into `app_module.main()` the same way the existing Strava fake is). Assert `"flagged 1 implausible" in capsys.readouterr().out`.
- **AC5 (summary.log exactly once):** in `tests/test_sync_engine.py`, call `logging_setup.configure(tmp_path)` then `engine.run_once([connector])`, then read `(tmp_path / "summary.log").read_text()` and assert `.count(f"{provider}: downloaded") == 1`. This exercises the real file sink rather than an ad hoc `logger.add(io.StringIO())`, so it actually catches a regression where the line gets written twice to the file (an in-memory capture of loguru's dispatch wouldn't necessarily distinguish "logged once, line appears once" from "logged twice to the same stream" the way counting lines in the real sink does).
- **AC6 (renormalize console noise):** test the mechanism, not the script's `main()` wholesale — `main()` hardcodes `APP_SUPPORT_DIR` and builds a real `StravaUnofficialConnector`/`CredentialStore`, which is more than this AC needs and already outside this project's live-verification boundary (per `technical-architect.md`'s testing-scope note). Instead, in `tests/test_logging_setup.py` (or a new test in `tests/test_training_load.py`), call `logging_setup.configure(tmp_path)` and then call `compute_training_load(normalized, profile=None)` several times under `capsys`; assert the "training_load unknown" text appears in neither `captured.out` nor `captured.err`. Add one thin additional test asserting `scripts/renormalize_strava_unofficial.py`'s `main` calls `logging_setup.configure` (e.g. via `monkeypatch` on `trainiq.logging_setup.configure`, short-circuiting before the real DB open) so a future edit can't silently drop the call again.
- AC1–AC3 are otherwise covered by the existing suite once the above three new tests are added — no other new tests needed.

## Risks/tradeoffs

- **`error`/`skipped_reason` branches are intentionally left alone.** They have their own pre-existing pattern of the Sync Engine and `_log_sync_summary()` each logging *different* text for the same event (e.g. engine logs `"{provider}: authentication failed — {exc}"`, `_log_sync_summary()` separately logs `"{provider}: {state} — {error}"`) — a similar-looking but textually distinct duplicate, not reported by the BO and not covered by this issue's acceptance criteria or scope ("the over-flagging bug... was visible... but not on screen" is specifically about the success-path summary line). Fixing it would be scope creep; flagging it here so it isn't assumed fixed by this change. Worth its own future issue if it turns out to matter.
- **`_unknown()`'s level change is global, not reason-specific.** All three "unknown" reasons (`athlete_profile not available`, `no valid duration_s`, `insufficient data for TSS or TRIMP`) move from INFO to DEBUG together, even though the issue only names the athlete_profile one by name. This is deliberate and consistent with AC1's own wording ("the... message," generalized) — all three are equally per-record, non-actionable detail. No existing test asserts on this call's level (checked `tests/test_training_load.py`, `tests/test_logging_setup.py`).
- **`DEFAULT_LOG_DIR` relocation is a rename, not a new constant.** `trainiq.app.LOG_DIR` is kept (as an import of the new constant) specifically so nothing that already references `trainiq.app.LOG_DIR` — none found in the current codebase outside `app.py` itself, but worth preserving defensively — breaks.
- **`scripts/benchmark.py`, `scripts/debug_peloton.py`, `scripts/debug_eufy.py` have the same missing-`configure()` gap** as the re-normalize script. None of them are in scope here: `debug_peloton.py`/`debug_eufy.py` never call `run_once()` at all (per their own docstrings), and `benchmark.py` only ever logs one line per connector via `summary_logger()` directly (no per-record diagnostic spam), so it doesn't exhibit the reported symptom. Worth a follow-up tooling note, not a blocker for this issue.
- **`summary_line` is `None` on error/skipped paths by construction.** `_log_sync_summary()`'s `if r.error` / `elif r.skipped_reason` branches are checked before the `else` that calls `report.echo(r.summary_line)`, so `echo(None)` is never reachable — but worth stating explicitly since nothing in the type system enforces it.

## Handoff

Design complete. No open questions remain — both of the requirements doc's
"Open questions" are resolved above (AC1: both script-level `configure()`
and source-level DEBUG demotion; AC2/AC3: Sync Engine keeps owning the log
write, `trainiq/app.py` echoes its text to console instead of rebuilding
it, chosen specifically to avoid breaking the two standalone Sync-Engine
tests that assert the flagged line via a raw loguru capture).
