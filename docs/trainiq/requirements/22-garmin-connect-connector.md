# Requirements: Garmin Connect Connector for Direct Running Data

**Issue:** #22  
**Test Issue for BA Pilot (Phase 2)**

## Summary

Add a new connector `GarminConnector` to retrieve running workouts directly from Garmin Connect via Garmin's free personal-use OAuth API. This bypasses the need for Strava subscription and enables zero-cost aggregation of both Peloton (bike) and Garmin (running) training data. Follows the same authentication, download, normalize, and checkpoint patterns established by `PelotonConnector` and `StravaConnector`.

## Scope

- **Authenticate:** OAuth2 via Garmin's Personal Use API (free tier, no subscription required)
- **Download:** Retrieve full workout history via paginated API endpoint; incremental sync via `after` checkpoint
- **Normalize:** Map Garmin fields to canonical TrainIQ types (duration_s, distance_m, calories, avg_power, avg_hr, max_hr, etc.)
- **Persist:** Store checkpoint (`after`-timestamp) for next sync
- **Lifecycle:** Follow SynchronizationEngine state machine (Connected → Degraded → RecoveryRequired)
- **Testing:** Unit tests with mocked Garmin responses; no live-account CI test (same as existing connectors)

### Out of scope

- Building a web-based OAuth redirect handler (manual token extraction via DevTools, same as Peloton bearer-token and Strava session-cookie patterns)
- Live-account testing in CI (not available to pipeline)
- Mapping Garmin's training metrics beyond what TrainIQ's normalized schema currently supports (e.g., VO2Max estimates, training effect scores — flag for future features)

## Acceptance criteria

1. **OAuth authentication:** Connector obtains access token via Garmin OAuth2; stores access/refresh tokens in CredentialStore
2. **Token refresh:** Refresh token rotates on use (same pattern as StravaConnector); new tokens persist to avoid re-auth loops
3. **Download all pages:** `download()` retrieves full workout history across all paginated pages, not just first page
4. **Incremental sync:** Checkpoint `after`-timestamp retrieved from last workout's start_time; subsequent syncs retrieve only workouts with `start_time > checkpoint`
5. **Normalize correctly:** Given raw Garmin workout object, `normalize()` correctly maps:
   - `id` → preserved
   - `startTimeInSeconds` → `start_time` (Unix timestamp)
   - `endTimeInSeconds` → used to derive `duration_s = end - start`
   - `distance` (meters) → `distance_m`
   - `calories` → preserved
   - `avgHeartRate`, `maxHeartRate` → `avg_hr`, `max_hr` (null if absent)
   - `avgPower`, `maxPower` → null (Garmin doesn't provide power for running; if present, map correctly; if absent, stay null — never fabricate)
   - `activityType` / `activityName` → `discipline_raw`
6. **Cursor type consistency:** `extract_resume_cursor()` returns checkpoint as string (not int), for safe comparison on subsequent syncs
7. **Rate-limit handling:** If Garmin returns 429 with `Retry-After` header, raise `TransientError` with `retry_after_s` set
8. **Stale token handling:** If OAuth refresh fails (401/403), escalate to `RecoveryRequired` state and prompt for manual token re-auth
9. **Unit tests:** Fixtures with mocked paginated responses, stale-token scenarios, rate-limit responses; all existing tests pass
10. **Lifecycle integration:** Respects SynchronizationEngine's Degraded backoff and RecoveryRequired escalation (no special OAuth bypass like Peloton, since this is primary path, not fallback)

## Open questions

None that block the Architect. The BO has confirmed Garmin offers free API access for personal use; the OAuth flow is documented and standard. If implementation reveals that Garmin's actual API shape differs from documentation (rare but possible), the Architect should flag for live-account verification before Dev builds against it.

**Non-blocking note:** Garmin's `avgPower` is not a direct measurement for running (running power measurement requires special sensor hardware). If the BO later wants to surface running-specific metrics (cadence, stride length, vertical oscillation), that's a separate feature/issue, not this one.
