# Requirements: Strava per-activity streams (HR, pace, GPS) and ride total output

**Issue:** #50 (spike result = GO, comment of 2026-10-09; live evidence is in that comment)
**Priority:** medium

## Summary

The BO wants Strava-only runs and walks (~190 activities, no Peloton copy) to carry heart rate,
true moving time, average pace and the GPS track, and Peloton rides to carry total output (kJ).
The list feed has none of this. The spike proved `GET /activities/{id}/streams?stream_types[]=...`
works with the existing `_strava4_session` cookie and `WEB_ENDPOINT_HEADERS` (200 JSON).

## Scope

In:
- **Streams fetch for Strava-only activities** (not linked to a Peloton workout in `dedup_links`, #37). One request per activity, once.
- New canonical fields on those activities: `avg_hr`, `max_hr` (time-weighted avg), `moving_time_s` (from `moving` + `time`; **replaces** the wrong list-feed value from #48, which equals elapsed for runs), `avg_pace_s_per_km` (derived).
- GPS track (`latlng`, `altitude`, `time`) in a **separate table**, one row per activity. Format and size: Architect decides.
- Peloton rides: `total_output_kj` from raw `total_work` / 1000 (offline via renormalize; `performance_graph` `summaries[total_output]` is an alternative source).
- Resumable, rate-limited backfill of existing Strava-only activities.
- Checkpoint/cursor: the backfill progress state touches checkpoint handling. **Flag for Architect**: must keep the cursor a string and not break incremental sync.

Out:
- `/activities/{id}/overview|laps|segments` (HTML, not data).
- Cadence and watts (absent without sensors; only handled as "key absent = NULL").
- Elevation gain: covered by #48 (BO runs `scripts/renormalize_strava_unofficial.py`; ops task).
- Real-account backfill execution (ops).
- Ride HR, distance, power, calories: already covered by #47, #57 and the Peloton list.

## Stream shape (live-captured, run 19869165154, 4398 samples)

Dict keyed by stream type, equal-length lists: `time` (int s), `distance` (float m), `latlng` ([lat,lng]), `altitude` (m, 118.8–154.3), `heartrate` (int bpm, 85–160), `velocity_smooth` (m/s), `grade_smooth` (%), `moving` (bool). Expected derivations for that run: moving 62:13, pace 7:43 /km, avg HR 113, max HR 160.

## Acceptance criteria

1. Fixture with the shape above (synthetic values): normalization yields `avg_hr` (time-weighted), `max_hr`, `moving_time_s`, `avg_pace_s_per_km`; values match hand-computed expectations.
2. `moving_time_s` for such an activity comes from streams, not `moving_time_raw`; `duration_s` is unchanged.
3. Absent stream keys (no `heartrate`, `cadence`, `watts`) leave the matching fields NULL; nothing is estimated or fabricated.
4. An activity with no streams (empty or no GPS/indoor) stores no track row and does not fail the sync.
5. The GPS track is stored in a separate table, one row per activity, not in the activity row; re-fetching does not create duplicates.
6. Activities linked to a Peloton workout in `dedup_links` trigger no streams request (test asserts zero calls).
7. Each Strava-only activity is fetched at most once; an already-populated activity is skipped on later syncs. New activities get 1 request per sync.
8. Backfill is rate-limited, resumable after interruption (picks up unfetched activities) and idempotent.
9. On 401/403/redirect-to-login the backfill stops cleanly and follows the existing lifecycle rule (RecoveryRequired, per CONVENTIONS); no partial/corrupt rows are written. On throttling (429 with `Retry-After`) it raises `TransientError(retry_after_s=...)` and stops; 5xx backs off without escalating. Test uses a throttled fixture.
10. Peloton rides expose `total_output_kj` = `total_work` / 1000; NULL if `total_work` absent.
11. No live Strava/Peloton calls in CI; all tests use fixtures/mocks. No credentials in code or fixtures; personal data (coordinates) in fixtures is synthetic.

## Open questions (none block the Architect)

- **Calories for runs/walks** exist only in the activity HTML page (858 kcal in the sample). BO delegated go/no-go to the Architect. BA flag: HTML parsing is fragile and, with session-cookie scraping, sits in a gray ToS area; if built, must return NULL when not found and never guess.
- **ToS/ban risk:** the cookie approach was accepted in #18; ~190 extra requests (backfill) plus 1 per new activity increases exposure. Architect/BO set the request rate and pacing; no safe limit is known (the spike didn't measure one).
- HR-zone durations (original issue wording) were not in the BO's spike scope; excluded unless the BO asks.
