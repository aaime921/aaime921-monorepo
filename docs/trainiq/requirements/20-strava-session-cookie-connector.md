# Requirements: Strava Data Retrieval via Session-Cookie Authentication

**Issue:** #20  
**Discovery Phase:** Complete  
**Constraint:** $0 ongoing cost, free Strava account

---

## Summary

The BO's running data is recorded in the Strava app on an Apple Watch 7 and syncs to Strava. Strava's official API is now blocked for free accounts (June 2026 subscription gate). As an interim solution (until/if free API access is restored or a direct Watch source becomes available), build a new connector `StravaUnofficial` that retrieves activity data using the `_strava4_session` browser cookie, following the pattern proven by the open-source `strava-offline` project. This connector operates in parallel to the existing `StravaConnector` — if official API access is ever unblocked, the official connector can be preferred; if this cookie method breaks, fallback to manual bulk export is documented but not automated.

**Known constraints:**
- Session cookies expire (typically weekly, sometimes days under load)
- Strava has no official blessing for this method; ToS prohibits scraping/automation
- Strava actively rate-limits and bot-detects cookie-based requests
- If Strava's auth flow changes, this connector will break
- Requires periodic user re-authentication when cookies expire (same pattern as Peloton manual bearer-token recovery)

This is accepted as a free-tier workaround, not a permanent solution.

---

## Scope

### In scope

1. **New connector class:** `StravaUnofficial` in `trainiq/connectors/strava_unofficial.py` (parallel to existing `StravaConnector`, not replacing it).

2. **Authentication via session cookie:**
   - New credential types: `CRED_STRAVA_SESSION_COOKIE`, `CRED_STRAVA_SESSION_OBTAINED_AT`, `CRED_STRAVA_SESSION_EXPIRES_AT`
   - Store `_strava4_session` cookie value and metadata (obtained time, expiry estimate)
   - `authenticate()` checks if stored cookie is still valid (current time < expiry - 1 day buffer)
   - If cookie expired/missing: `request_manual_recovery()` prompts user to log into Strava in a browser, extract `_strava4_session` from browser cookies (via DevTools), and paste it back
   - `submit_manual_recovery(cookie_value)` validates the cookie via a test request to `/api/v3/athlete` and stores it

3. **Download activities:**
   - Retrieve full activity history via `GET https://api.strava.com/api/v3/athlete/activities?after={checkpoint}&per_page=200` (iterate pages until no results)
   - Use stored cookie for all requests: `Cookie: _strava4_session={cookie_value}`
   - Checkpoint is `after`-timestamp (Unix seconds), same as existing `StravaConnector`
   - If no checkpoint exists, retrieve full history (backfill)

4. **Normalize activities:**
   - Reuse field mapping from existing `StravaConnector.normalize()` — this should be identical or nearly identical
   - Required fields: `id`, `start_time` (Unix), `distance_m`, `calories`, `duration_s`, `avg_power`, `max_power`, `avg_hr`, `max_hr`, `discipline_raw`
   - Optional fields: `elevation_gain_m`, `perceived_exertion`
   - If existing `StravaConnector` already maps these correctly, copy that logic

5. **Incremental sync:**
   - Extract checkpoint from last activity's `start_time` (same as existing `StravaConnector`)
   - Each subsequent sync retrieves only activities with `start_time > checkpoint`

6. **Error handling:**
   - `429 Too Many Requests`: Parse `Retry-After` header, return `TransientError` with `retry_after_s` (same pattern as existing connector)
   - `401 Unauthorized` / `403 Forbidden`: Treat as cookie stale, escalate to `RecoveryRequired` state (same pattern as Peloton manual recovery)
   - `404`, `5xx`: Treat as transient errors with standard backoff
   - HTTP errors that indicate cookie is invalid: clear all three session credentials and escalate to `RecoveryRequired`

7. **Lifecycle state handling:**
   - Follows standard connector state machine (Connected → Degraded → RecoveryRequired)
   - Respects `SynchronizationEngine`'s checkpoint and backoff policies
   - No special bypass for recovery (unlike Peloton OAuth, which bypasses Degraded backoff — cookies don't have that alternative path)

8. **Testing:**
   - Unit tests with fixtures (mocked `_strava4_session` cookie responses, paginated activity lists, rate-limit responses)
   - No live-account test in CI (same as existing `StravaConnector`)
   - Tests cover: successful authentication, cookie expiry detection, pagination, normalize() field mapping, checkpoint extraction, rate-limit handling, 401/403 escalation

### Out of scope

- Building or automating cookie extraction (e.g., browser automation to grab cookies from a real browser) — the BO manually extracts the cookie via DevTools and pastes it, same as the Peloton manual bearer-token flow
- Replacing the existing `StravaConnector` — both connectors can coexist
- Solving Strava's subscription gate for official API access — this is a workaround, not a fix
- Rewriting Strava's auth flow or seeking Strava's permission/blessing — we're using documented, publicly-accessible browser cookies within their standard HTTPS flow
- Integration testing against a real Strava account in CI — not available, same constraint as other connectors

---

## Acceptance criteria

1. **Authentication without official API key:**
   - No `client_id`, `client_secret`, or OAuth tokens required
   - Authentication is cookie-based only (`_strava4_session`)

2. **Cookie lifecycle:**
   - Given a valid `_strava4_session` cookie stored in credentials, `authenticate()` returns `True` without prompting
   - Given a missing or expired cookie, `authenticate()` returns `False` and enters recovery flow
   - `request_manual_recovery()` prompts the user with clear instructions: "Log into Strava in your browser, open DevTools (Cmd+Opt+I) → Application → Cookies → find `_strava4_session`, copy its value"
   - `submit_manual_recovery(cookie_value)` validates the cookie by making a test request to `GET /api/v3/athlete`; on success, stores it with `obtained_at = now`, `expires_at = now + 7_days` (conservative estimate; actual Strava cookie TTL varies)

3. **Download and pagination:**
   - `download()` with no checkpoint retrieves all available activities (full backfill), iterating through pages until no results returned
   - `download()` with checkpoint `after=1704067200` retrieves only activities with `start_time > 1704067200`
   - Given a paginated response with `per_page=200` and 5 pages of results, all 5 pages are retrieved and returned as a single list
   - No results (empty pages) are handled correctly — function returns empty list, not an exception

4. **Normalize field mapping (identical to existing StravaConnector):**
   - Raw Strava fields are mapped to canonical normalized fields (same as issue #5 established for the official connector)
   - `id`, `start_time`, `distance_m`, `calories`, `duration_s` are copied directly or derived correctly
   - `avg_power`, `max_power`, `avg_hr`, `max_hr` are handled consistently with existing connector (either derived from available fields or `None`)
   - `discipline_raw` is preserved from Strava's `type` or `sport_type` field

5. **Checkpoint extraction:**
   - Given a list of normalized activities, `extract_resume_cursor()` returns the `start_time` of the most recent activity as a string (for safe comparison with stored checkpoint)
   - If activities list is empty, return the existing checkpoint unchanged

6. **Error handling — rate limits:**
   - Given a 429 response with `Retry-After: 3600` header, `download()` raises `TransientError` with `retry_after_s=3600`
   - Given a 429 response without `Retry-After`, use conservative default (e.g., 3600s)

7. **Error handling — cookie stale (401/403):**
   - Given a 401 or 403 response (indicating invalid/expired cookie), the connector clears all three session credentials and escalates to `RecoveryRequired` state
   - Subsequent calls to `authenticate()` return `False` and trigger recovery flow

8. **Unit tests:**
   - Fixtures include: valid paginated responses, rate-limit (429) responses, stale-cookie (401/403) responses, empty-page responses
   - Tests verify: successful auth with valid cookie, auth failure with missing/invalid cookie, pagination across all pages, checkpoint extraction, normalize() field mapping, error handling for all documented error cases
   - All existing unit tests for this connector pass

---

## Open questions

None that block the Architect. The discovery phase (issue #20) identified the running data source as Strava-recorded, and session-cookie scraping is the chosen path. Known tradeoffs (ToS gray area, cookie expiry, fragility) are accepted by the BO as a free-tier interim solution.

**Non-blocking note for future consideration:**
If Strava's official API subscription pricing changes or a free tier is restored, the existing `StravaConnector` can be preferred and `StravaUnofficial` deprecated. This design keeps both options available.

---

## References

- **Discovery analysis:** `docs/discovery/20-strava-zero-cost-paths.md`
- **strava-offline (open-source reference):** https://github.com/camjccc/strava-offline
- **Existing StravaConnector:** `trainiq/connectors/strava.py` (use field mapping as baseline)
- **Existing manual-recovery pattern:** `PelotonConnector.submit_manual_recovery()` and `request_manual_recovery()` (issue #5, use as template for cookie recovery flow)
