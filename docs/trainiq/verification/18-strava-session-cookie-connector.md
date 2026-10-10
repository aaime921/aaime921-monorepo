# QA Verification: Strava session-cookie connector (issue #18)

**Issue:** #18
**PR:** [#21](https://github.com/aaime921/aaime921-monorepo/pull/21) — `issue-18-strava-session-cookie-connector` @ `aaf4c65a62f7338dd47ed9d09705afde8858254e`
**Requirements:** [`docs/trainiq/requirements/18-strava-session-cookie-connector.md`](../requirements/18-strava-session-cookie-connector.md)
**Architecture:** [`docs/trainiq/architecture/18-strava-session-cookie-connector.md`](../architecture/18-strava-session-cookie-connector.md)
**Date:** 2026-10-03
**Scope:** fixture/unit-test verification only — no live Strava account is available in CI, consistent with the requirements doc's "Live-account testing boundaries" and the existing `StravaConnector`'s own test coverage. No live capture exists yet for this cookie-auth endpoint (see architecture doc's Risks/tradeoffs); this QA pass does not change that.

This is an independent re-verification against actual code and a real test
run — not a re-statement of the Developer's own PR description. Each
acceptance criterion below was checked against `trainiq/connectors/strava_unofficial.py`
and `trainiq/connectors/base.py`/`trainiq/sync/engine.py` directly.

## Test run

- New file: `pytest tests/test_strava_unofficial_connector.py -v` — **27/27 passed**.
- Full suite: `pytest` — **349 passed, 2 failed**. The 2 failures
  (`test_peloton_csv_import.py::test_real_csv_import_is_idempotent`,
  `::test_real_csv_full_regression`) are `FileNotFoundError` on a
  sandbox-local path (`/home/claude/peloton_work/aimea75_workouts.csv`).
  Reproduced identically on `origin/main` (commit `98926df`) with this PR's
  files absent — confirmed pre-existing and unrelated to this change.

## Acceptance criteria

| AC | Result | Notes |
|----|--------|-------|
| 1. Cookie-only auth, no OAuth | ✅ Pass | `__init__`/`authenticate()` use no `client_id`/`client_secret`; all requests send `Cookie: _strava4_session=...` only. |
| 2. Cookie lifecycle | ✅ Pass | `authenticate()`: valid unexpired cookie → `True`, zero `self._session.get` calls (test asserts `fake.get_calls == []`). Missing or past-safety-margin cookie → `False`, no network call. `request_manual_recovery()` names `_strava4_session` and the DevTools path. `submit_manual_recovery()` calls `GET /api/v3/athlete` via `_authenticated_get` *before* any `credentials.rotate(...)`; a rejected (401/403) or `TransientError`-raising (429/5xx) validation persists nothing; empty string raises `ValueError` before any network call. |
| 3. Download and pagination | ✅ Pass | `download()` loops `page = 1, 2, 3, ...` until a batch is empty, concatenating all pages (verified by `test_download_no_checkpoint_full_backfill_concatenates_all_pages`). Checkpoint path converts the ISO `since` string to epoch seconds via `datetime.fromisoformat(since).timestamp()` and sends it as `after`. Empty first page → `[]`, no exception. `download()` before a successful `authenticate()` → `AuthenticationError`, no network call (`self._active_cookie is None` guard). |
| 4. Normalize field mapping | ✅ Pass | Compared `strava_unofficial.py::normalize()` line-by-line against `strava.py::normalize()`: same keys (`id`→`external_id`, `start_date`→`start_time`, `elapsed_time`→`duration_s`, `sport_type`/`type`→`discipline_raw`, `average_heartrate`/`max_heartrate`→`avg_hr`/`max_hr`, `average_watts`/`max_watts`→`avg_power`/`max_power`, `distance`→`distance_m`), identical `None`-handling, `calories: None` for the same documented reason (activities-list endpoint doesn't return it under either auth method). The unofficial version uses `.get()` instead of direct indexing for the optional fields — more defensive, not a behavioral divergence when the fields are present, which is what the shared fixture exercises. |
| 5. Checkpoint extraction | ✅ Pass | `extract_resume_cursor()` is not overridden; confirmed the inherited `Connector` base-class default (`normalized.get("start_time")`, `base.py:218`) is what's actually used, and that this connector's `normalize()` output is shaped the same as `StravaConnector`'s (ISO `start_time` string). Test confirms `{"start_time": "..."} → "..."` and `{} → None`. |
| 6. Rate limits (429) | ✅ Pass | `_parse_retry_after()` reads `Retry-After` and raises `TransientError(retry_after_s=<value>)`; absent header falls back to `DEFAULT_RETRY_AFTER_S = 3600`. Both paths covered by dedicated tests. |
| 7. Cookie stale (401/403) | ✅ Pass, per architecture doc's corrected wording | Requirements doc's literal AC7 ("escalate to `RecoveryRequired`") is genuinely not implementable: independently confirmed in `trainiq/connectors/base.py`'s `_ALLOWED_TRANSITIONS` that `HEALTHY → {WARNING, DEGRADED}` and `WARNING → {HEALTHY, DEGRADED}` — `RECOVERY_REQUIRED` is reachable only from `DEGRADED`, so a direct `Healthy/Warning → RecoveryRequired` call would raise `InvalidStateTransition`. The implemented behavior (`_authenticated_get` deletes all three credentials and raises `AuthenticationError` on 401/403, same pattern as `StravaConnector`/`PelotonConnector`) is functionally equivalent — "`authenticate()` subsequently returns `False` and triggers recovery" holds — and lets the existing, already-tested Sync Engine lifecycle machinery (`connector_state` keyed by `provider`, confirmed via `WHERE provider = ?` queries in `sync/engine.py`) drive the actual state transitions. Tests confirm both 401 and 403 delete `session_cookie`, `session_obtained_at`, and `session_expires_at`. |
| 8. Unit tests, fixtures only | ✅ Pass | 27/27 new tests pass; fixtures cover auth success/missing/expired, recovery success/401/403/empty-input/mid-validation-429, pagination (full backfill, checkpointed, empty-page), all documented error codes (401/403/429-with-and-without-header/404/5xx/network-level failure), unexpected-status fallthrough (`StravaUnofficialHTTPError`), normalize() incl. `sport_type`-absent fallback and missing-HR/power-as-`None`, and cursor extraction. No live-account test. |

## Provider isolation (architecture doc's central design claim)

Independently verified, not just taken on the doc's word:
- `PROVIDER = "strava_unofficial"` (distinct from `StravaConnector`'s `"strava"`).
- `connector_state`, `sync_checkpoints` and the raw/normalized activity
  tables are all queried `WHERE provider = ?` (confirmed via grep in
  `trainiq/sync/engine.py`) — a cookie-auth failure cannot write to or read
  the official connector's lifecycle row, and vice versa.
- `AcquisitionStrategy.UNOFFICIAL_SESSION` and `CapabilityTier.TIER_2_UNOFFICIAL`
  genuinely exist in `trainiq/connectors/base.py`'s enums (not newly invented
  by this PR without a base-class counterpart).

## Scope check

- `trainiq/connectors/strava.py`, `base.py`, `sync/engine.py`,
  `credentials/store.py` — unchanged in this PR (confirmed via `git diff
  origin/main...issue-18-strava-session-cookie-connector --stat`: only
  `trainiq/connectors/strava_unofficial.py` and
  `tests/test_strava_unofficial_connector.py` are touched).
- `trainiq/app.py` / `trainiq/setup_wizard.py` wiring is out of scope per
  the requirements doc's Scope section and is correctly not touched by this
  PR.

## Verdict

**All 8 acceptance criteria pass.** PR #21 is approved. No regressions:
the only failing tests in the full suite are the 2 pre-existing,
sandbox-path-dependent Peloton CSV failures, confirmed identical on `main`.

PR left open — merge is reserved for the BO.
