# Verification: Wire StravaUnofficialConnector into app.py and setup_wizard.py

**Issue:** #22
**PR:** [#24](https://github.com/aaime921/aaime921-monorepo/pull/24) (`issue-22-wire-strava-unofficial-connector`, head `d279bfd`)
**Requirements:** `docs/trainiq/requirements/22-wire-strava-unofficial-connector.md`
**Architecture:** `docs/trainiq/architecture/22-wire-strava-unofficial-connector.md`

---

## Method

1. Fetched and checked out PR #24's head commit (`d279bfd`) directly (not
   just re-running the Developer's own reported numbers).
2. Installed `trainiq[dev]` into a clean venv and ran the full suite
   (`pytest tests/ -q`), then the new tests in isolation
   (`pytest tests/test_app.py tests/test_setup_wizard.py -v`).
3. Diffed `app.py`/`setup_wizard.py` against `origin/main` and against the
   architecture doc's prescribed code, line by line.
4. Wrote 4 **independent** tests (not shipped in the PR) that wire the
   **real** `StravaUnofficialConnector` through the **real**
   `_setup_strava_unofficial()` wizard step, with only HTTP faked — the
   PR's own tests fake one side of this seam at a time (fake connector in
   `test_setup_wizard.py`, fake HTTP session in
   `test_strava_unofficial_connector.py`), so this closes the gap between
   them and catches any mismatch at the actual call boundary.
5. Confirmed the 2 unrelated pre-existing failures are not a regression by
   running the same test file against `origin/main` directly.
6. Ran `ruff check` on all 4 changed/added files and diffed the findings
   against the same check on `origin/main` to separate pre-existing nits
   from anything newly introduced.

## Test suite results

- Full suite on PR head: **360 passed, 2 failed** (`tests/test_peloton_csv_import.py::test_real_csv_import_is_idempotent`,
  `::test_real_csv_full_regression` — both `FileNotFoundError` on
  `/home/claude/peloton_work/aimea75_workouts.csv`, a local fixture path
  not present in this sandbox).
- Confirmed **identical** 2 failures when running the same file against
  `origin/main` with none of this PR's changes applied — pre-existing,
  unrelated to this issue. Not a blocker.
- New tests shipped in the PR (`test_app.py` + `test_setup_wizard.py`,
  `strava_unofficial`-related cases): **36/36 passed**.
- My own 4 independent real-connector-through-real-wizard tests: **4/4
  passed** (accept+valid persists all 3 credentials and sends the typed
  cookie on the wire; reject (401) persists nothing; 429 during validation
  is caught and reported as failure, not a crash; empty-string input is
  caught and reported as failure, not a crash).

## Acceptance criteria

| # | Criterion | Verdict | Evidence |
|---|---|---|---|
| 1 | `app.py` registers connector when `session_cookie` present | **PASS** | `test_strava_unofficial_configured_when_session_cookie_present`; diff matches architecture doc's branch exactly. |
| 2 | `app.py` skips + logs when not configured | **PASS** | `test_strava_unofficial_skipped_when_no_session_cookie_stored`. |
| 3 | `app.py` degrades gracefully on construction failure | **PASS** | `test_strava_unofficial_construction_failure_is_caught_and_skipped`; `try/except Exception` matches the other 3 branches' pattern. |
| 4 | `app.py` coexistence (official + unofficial both present) | **PASS** | `test_strava_unofficial_coexists_with_official_strava`; independent provider keys confirmed in `strava_unofficial.py` (`PROVIDER = "strava_unofficial"`). |
| 5 | Wizard offers step unconditionally after official Strava | **PASS** | Call site in `run_first_time_setup()` is an unconditional sequential call (no `if` gating it on `_setup_strava()`'s return value) — true by construction, not just by the one scripted-decline test case shipped. |
| 6 | Decline stores nothing | **PASS** | `test_strava_unofficial_declined_stores_nothing`. |
| 7 | Accept + valid cookie stores all 3 credentials, reports success | **PASS** | PR's own test only checks the fake's `submitted_with` + return value; my independent test (`test_real_connector_through_wizard_accept_valid_cookie_persists_all_three`) exercises the **real** connector through the **real** wizard step and confirms `session_cookie`/`session_obtained_at`/`session_expires_at` are all actually persisted, and that the typed value is what's sent as the `Cookie` header. |
| 8 | Accept + rejected cookie stores nothing, clear message | **PASS** | `test_strava_unofficial_accepted_with_rejected_cookie_reports_nothing_saved` (fake) + my `test_real_connector_through_wizard_reject_401_persists_nothing` (real connector, real 401 response). |
| 9 | Accept + unexpected exception during validation stores nothing | **PASS** | PR's test uses a generic `RuntimeError` from a fake. My `test_real_connector_through_wizard_transient_429_propagates_as_failure_not_crash` confirms the real failure mode this AC actually describes — a live 429 during validation raises `TransientError` inside `submit_manual_recovery()` (it only catches `AuthenticationError`/`StravaUnofficialHTTPError` internally) — and the wizard's `except Exception` still catches it correctly. Also independently caught the empty-cookie `ValueError` path (`submit_manual_recovery("")` raises, doesn't return `False`) via `test_real_connector_through_wizard_empty_cookie_input_does_not_crash_setup` — not explicitly named in the ACs but the same code path, and it isn't a gap: both are plain exceptions, not expected-rejection, caught by the same `except Exception`. |
| 10 | Cancellation mid-step propagates `SetupCancelled`, nothing stored | **PASS** | `test_strava_unofficial_cancellation_propagates_and_stores_nothing`; confirmed `_prompt_yes_no`/`_prompt_text` raise `SetupCancelled` on `KeyboardInterrupt`/EOF, uncaught locally, same as every other step. |
| 11 | No regression on existing Strava/Peloton/Eufy tests | **PASS** | Full suite run; all pre-existing `test_app.py`/`test_setup_wizard.py` cases still pass. The 2 failures present are pre-existing and unrelated (see above). |
| 12 | All new tests use mocked HTTP/connectors — no real network | **PASS** | Confirmed via code read: `tests/test_setup_wizard.py` module docstring states this explicitly, `in_memory_keyring` fixture is autouse, and every new case injects either a fake connector or (in my independent tests) a fake HTTP session — no test imports `requests` directly or constructs the default `_RequestsSession`. |

**All 12 acceptance criteria: PASS.**

## Additional checks

- **Lint:** `ruff check` on the 4 changed files found 6 findings; diffing
  against the same check on `origin/main` shows 5 of the 6 are
  **pre-existing** (unrelated `F841`/`RUF059`/`F401` in untouched lines of
  `test_app.py`/`test_setup_wizard.py`, and a pre-existing `UP045` on the
  *existing* `_setup_strava()` signature this PR didn't touch). The one
  **new** finding is `I001` (import block sort order) in `app.py` and
  `test_app.py`, triggered by the new `strava_unofficial` imports breaking
  isort's expected ordering. This is a cosmetic nit only — the repo has no
  `[tool.ruff]` section in `pyproject.toml` and no lint gate is documented
  as part of this pipeline's QA criteria — so it is **not blocking**, but
  flagged here for visibility in case the BO wants a trivial follow-up fix.
- **Design fidelity:** both `app.py`'s new branch and `setup_wizard.py`'s
  new function/call-site are a verbatim match to the code blocks in the
  architecture doc (including the aliased `PROVIDER`/`CRED_STRAVA_SESSION_COOKIE`
  imports called out there to avoid the documented naming-collision risk).
- **Scope:** diff is confined to `trainiq/app.py`, `trainiq/setup_wizard.py`,
  `tests/test_app.py`, `tests/test_setup_wizard.py` — no changes to
  `strava_unofficial.py`, `strava.py`, the sync engine, or any other
  connector, matching the requirements doc's explicit out-of-scope list.

## Verdict

**✅ All 12 acceptance criteria verified. PR #24 approved.**
