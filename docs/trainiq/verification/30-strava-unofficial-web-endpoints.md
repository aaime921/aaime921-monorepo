# QA Verification: Switch StravaUnofficialConnector to Strava's Web Endpoints

**Issue:** #30
**PR:** [#31](https://github.com/aaime921/aaime921-monorepo/pull/31) (`c7f7010`)
**Requirements:** [`docs/trainiq/requirements/30-strava-unofficial-web-endpoints.md`](../requirements/30-strava-unofficial-web-endpoints.md)
**Architecture:** [`docs/trainiq/architecture/30-strava-unofficial-web-endpoints.md`](../architecture/30-strava-unofficial-web-endpoints.md)

This is QA's own verification record (mocked-HTTP, CI-only, per this
project's live-verification constraint) — distinct from
[`strava-unofficial-2026-10-05.md`](strava-unofficial-2026-10-05.md), the
BO-run live-account template the Developer created to satisfy AC7.

## Method

Checked out PR #31 (`c7f7010`) against base `7db83ca`. Ran the connector's
dedicated test file and the full repo suite in a clean venv. Read
`trainiq/connectors/strava_unofficial.py` against the architecture doc's
interfaces/contracts line by line. Wrote one additional scripted scenario
(not in the PR, see "Additional QA check" below) to exercise a case the
Developer's own tests didn't cover.

## Acceptance criteria

| # | Criterion | Result | Evidence |
|---|---|---|---|
| 1 | `download()` fetches from `/athlete/training_activities`, paginated via `page`/`total` | **PASS** | `strava_unofficial.py:258-291`; `test_download_requests_against_training_activities_endpoint`, `test_download_pagination_terminates_via_total_not_empty_page`, `test_download_total_not_evenly_divisible_by_page_size_still_terminates_correctly`, `test_download_empty_first_page_zero_total_returns_empty_list_one_request` all pass |
| 2 | Request includes `X-Requested-With`, `Accept: application/json`, browser `User-Agent` | **PASS** | `WEB_ENDPOINT_HEADERS` merged into every `_authenticated_get` call (`strava_unofficial.py:206`); `test_download_includes_web_endpoint_headers`, `test_submit_manual_recovery_includes_web_endpoint_headers` pass |
| 3 | `submit_manual_recovery()` validates against the web endpoint, not `/api/v3/athlete` | **PASS** | `submit_manual_recovery()` calls `_authenticated_get(TRAINING_ACTIVITIES_PATH, params={"page": 1}, ...)` (`strava_unofficial.py:181-183`); `test_submit_manual_recovery_validates_against_training_activities_endpoint` passes; no `/api/v3` call site remains anywhere in the module |
| 4 | `normalize()` maps `*_raw` fields to the existing canonical shape; unmapped fields stay `None`; units documented | **PASS** | `normalize()` docstring documents units for `distance_raw`/`elapsed_time_raw`/`moving_time_raw`/`elevation_gain_raw` (`strava_unofficial.py:294-306`); HR/power/calories explicitly `None`, commented as "never fabricated"; `test_normalize_maps_web_payload_fields_to_canonical_shape` and `test_normalize_falls_back_to_display_type_when_activity_type_display_name_absent` pass |
| 5 | Expired/invalid cookie (redirect, non-2xx, HTML-not-JSON) → `AuthenticationError`, credentials cleared, never `TransientError` | **PASS** | `_authenticated_get` classifies 3xx, 401/403, and a `.json()` `ValueError` all as `AuthenticationError` via `_clear_session_credentials()` (`strava_unofficial.py:219-248`); `_RequestsSession.get()` passes `allow_redirects=False` so a 3xx is actually observable (not silently followed); 7 dedicated tests cover redirect-with/without-`Location`, HTML body, and 401/403 for both `download()` and `submit_manual_recovery()`, all pass |
| 6 | Unit tests (mocked HTTP) cover multi-page pagination termination, cookie-validation success/failure, expired/invalid-cookie → recovery path | **PASS** | `tests/test_strava_unofficial_connector.py`: 38/38 pass (was 23 before this PR) |
| 7 | Live-verification doc created at `docs/trainiq/verification/strava-unofficial-<date>.md` | **PASS** | `strava-unofficial-2026-10-05.md` present in the PR, copied from `PROTOCOL.md` with issue-#30-specific notes on the four flagged assumptions (start-time field name, `*_raw` units, pagination order, actual expired-cookie response shape) |

## Test execution

```
cd projects/trainiq && pytest tests/test_strava_unofficial_connector.py -v
...
38 passed in 1.73s
```

Full suite:

```
cd projects/trainiq && pytest tests/
...
2 failed, 371 passed in 10.94s
```

The 2 failures (`test_peloton_csv_import.py::test_real_csv_import_is_idempotent`,
`::test_real_csv_full_regression`) are **pre-existing and unrelated**:
confirmed by running the same file against base commit `7db83ca` (before
this PR), which fails identically with the same
`FileNotFoundError: /home/claude/peloton_work/aimea75_workouts.csv` — a
missing local fixture file, not present in this sandbox, uninvolved in
Strava code at all.

## Additional QA check (beyond the Developer's own tests)

The Developer's incremental-stop tests (`test_download_checkpoint_stops_early_mid_page_and_requests_no_further_pages`,
`test_download_checkpoint_older_than_every_item_proceeds_through_full_pagination`)
only exercise the early-stop boundary within a single page. QA additionally
scripted a two-page scenario — page 1 entirely newer than the checkpoint,
page 2 containing a mix of newer and older-than-checkpoint items — to
confirm the early-stop logic also works correctly across a page boundary,
not just mid-first-page. Result: returns exactly the 3 items newer than the
checkpoint (2 from page 1, 1 from page 2) and stops after 2 requests,
matching the expected behavior. This was a mocked, local script (not
committed to the PR, per this QA pass's `docs/trainiq/verification/*`-only
scope) — confirms AC1/AC6's pagination design holds at a page boundary,
not just within one page.

## Code review notes (non-blocking)

- No dead `/api/v3/*` code paths remain in `strava_unofficial.py` — only
  historical comments referencing the old endpoint for context.
- `CredentialStore.delete()` is a no-op (not an exception) when the
  credential doesn't already exist, so `_clear_session_credentials()` is
  safe to call even when `submit_manual_recovery()` rejects a cookie before
  anything was ever persisted (exercised by
  `test_submit_manual_recovery_rejected_cookie_returns_false_and_persists_nothing`).
- The architecture doc's residual risk ("a 200 with a valid-but-empty JSON
  body, e.g. `{}`, wouldn't be caught as an auth failure — `download()`
  would just see `total=0` and silently return no activities") is real and
  unaddressed by this PR, but it was explicitly flagged as acceptable
  residual risk in the architecture doc and acceptance criteria don't
  require covering it — not treated as an AC failure here. The BO's live
  verification doc already calls this out under item 4.

## Verdict

**All 7 acceptance criteria pass.** PR #31 is approved and ready to merge
(left open per protocol — merging `main` is reserved for the BO). Issue
moved to `stage:done`.
