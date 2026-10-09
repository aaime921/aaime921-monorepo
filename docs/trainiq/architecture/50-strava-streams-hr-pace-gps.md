# Architecture: Strava streams (HR, pace, GPS) and ride total output

**Issue:** #50 · **Requirements:** `docs/trainiq/requirements/50-strava-streams-hr-pace-gps.md`
**Related:** #30/#48 (`strava_unofficial.py`), #37 (`dedup/detector.py`), #47/#57 (Peloton narrow-UPDATE pattern, `apply_performance_update()` in `connectors/peloton.py`), #36 (`normalization/renormalize.py`), ADR-037, ADR-038.

## Approach

Streams enrichment is a **separate step after sync**, not part of `download()`. Reason: dedup runs offline (`run_backfill`), so at download time nobody knows which activities are Peloton-linked. Enrichment is driven by a per-row status column, so **the sync checkpoint is not touched** (AC cursor concern vanishes; incremental sync is unchanged).

- New module `trainiq/connectors/strava_streams.py`: `enrich_strava_streams(conn, connector, *, limit, delay_s, jitter_s)`.
- Eligible rows: `provider='strava_unofficial' AND streams_fetch_status IS NULL AND id IN primary_activity_ids(conn)` (reuses `dedup/detector.py`; excludes the secondary side of auto-linked pairs; flagged-ambiguous pairs stay eligible).
- Order newest first, one request per row, then stop at `limit`.
- Entry points: `scripts/backfill_strava_streams.py` (BO, default `limit=None`, resumable by re-running) and a post-sync step in `app.py main()` after `run_once`: `run_backfill()` (idempotent) then `enrich_strava_streams(limit=NEW_PER_SYNC_CAP=10)`. The cap bounds exposure; the full ~190 backfill is the script.
- **Pacing (decision, no measured limit exists):** `delay_s=3.0` + `jitter_s=2.0` uniform between requests (≈10 min for 190), constants in the module, sleep injectable for tests. We accept the cookie-scraping ToS risk (as in #18) to get HR/GPS the official API gives us no cheaper path to; the cap and pacing minimise added exposure.
- **Calories for runs/walks: NO-GO.** HTML scraping is the most fragile and most bot-like part (extra request per activity, markup parsing). Revisit only if the BO asks.
- HR zones: out of scope (no change).

### Where derived values live (survives renormalize)
`normalize()` of `strava_unofficial` must keep working from `raw_activities` (#36). So enrichment merges derived keys into the stored raw payload (`raw_activities.payload_json`): `_streams_status`, `_streams_avg_hr`, `_streams_max_hr`, `_streams_moving_time_s`, `_streams_avg_pace_s_per_km` (same underscore pattern as Peloton `_avg_hr`), then narrow-UPDATEs `normalized_activities` (same invariant exception as `apply_performance_update()`). `normalize()` reads them with `.get()`; `_streams_moving_time_s` **overrides** `moving_time_raw` when present. The list feed only returns new activities, so a re-sync never overwrites enriched rows; renormalize reproduces them from raw.

## Schema: migration 11 (check `CURRENT_SCHEMA_VERSION` = 10 on `main` first; renumber if another PR landed)
```sql
ALTER TABLE normalized_activities ADD COLUMN avg_pace_s_per_km REAL;
ALTER TABLE normalized_activities ADD COLUMN streams_fetch_status TEXT;  -- NULL pending | ok | no_streams | unavailable
ALTER TABLE normalized_activities ADD COLUMN total_output_kj REAL;
CREATE TABLE activity_tracks (
    activity_id INTEGER PRIMARY KEY REFERENCES normalized_activities(id),
    point_count INTEGER NOT NULL,
    encoding TEXT NOT NULL,        -- 'zlib-json-v1'
    track BLOB NOT NULL
);
```
Nullable, no defaults (never fabricate). `avg_hr`, `max_hr`, `moving_time_s` already exist. `upsert_normalized_activity()` (`sync/engine.py`) and `_build_activity_record()` (`normalization/engine.py`) gain `avg_pace_s_per_km`, `total_output_kj` pass-throughs (both INSERT and UPDATE branches). `streams_fetch_status` is written only by the enrichment UPDATE, never by upsert.

**Track format:** `zlib.compress(json.dumps({"t": [...], "lat": [...], "lng": [...], "alt": [...]}, separators=(",",":")))`, full resolution (no decimation), 6 decimal places for lat/lng, `alt` null-padded if absent. ~4400 points ≈ 25–35 KB/activity, ≈6 MB for 190. `INSERT OR REPLACE` keyed on `activity_id` (no duplicates, AC5). No `latlng` stream, no row.

## Interfaces
```python
# StravaUnofficialConnector
STREAM_TYPES = ["time","distance","latlng","altitude","heartrate","velocity_smooth","grade_smooth","moving"]
def fetch_activity_streams(self, activity_id: str) -> dict[str, list] | None:
    """GET /activities/{id}/streams via _authenticated_get. 404 -> None (activity gone/private).
    401/403/3xx/non-JSON -> AuthenticationError (existing behaviour clears the cookie; keep it);
    429 -> TransientError(retry_after_s); 5xx -> TransientError."""
# strava_streams.py (pure, fixture-testable)
def derive_stream_metrics(streams: dict) -> dict   # avg_hr, max_hr, moving_time_s, avg_pace_s_per_km (None per missing input)
def encode_track(streams: dict) -> tuple[int, bytes] | None
```
`_authenticated_get` currently raises `TransientError` on 404; add a `not_found_ok` flag returning `None` rather than changing existing callers.

**Derivations** (dt[i] = time[i]−time[i−1], i≥1): `avg_hr = Σ hr[i]·dt[i] / Σ dt[i]` over samples with HR; `max_hr = max(hr)`; `moving_time_s = round(Σ dt[i] where moving[i])`; `avg_pace_s_per_km = moving_time_s / (distance[-1]/1000)`, None if distance < 100 m or `moving`/`distance` absent. Absent key → matching field None. Cadence/watts ignored. `duration_s` unchanged.

**Per-row outcomes:** streams with ≥1 usable key → `ok`; empty dict/no usable keys → `no_streams`; 404 → `unavailable`. These are final (AC7). `AuthenticationError`/`TransientError` → commit what is done, **leave the row NULL**, stop the run and re-raise to the caller. Script prints a summary and exits non-zero; `app.py` reports it through the existing lifecycle (`AuthenticationError` → RecoveryRequired path in ADR-038, 429 → ADR-037 `retry_after_s`, 5xx no escalation). Each row is one transaction (raw merge + normalized UPDATE + track), so no partial rows.

## Ride total output
`peloton.py normalize()`: `"total_output_kj": raw["total_work"]/1000 if raw.get("total_work") is not None else None`. Backfill = existing renormalize for peloton (BO runs `renormalize_provider`, no new code; add a one-line script only if none exists for peloton).

## Task breakdown
1. Migration 11 + `CURRENT_SCHEMA_VERSION`; `activity_tracks`; test in `test_storage.py` (pre-existing rows read NULL).
2. Pass-through columns in `_build_activity_record()` and both `upsert_normalized_activity()` statements.
3. `peloton.normalize()` `total_output_kj`.
4. `fetch_activity_streams()` + 404 flag; `normalize()` reads `_streams_*`.
5. `strava_streams.py`: `derive_stream_metrics`, `encode_track`, `enrich_strava_streams`.
6. `scripts/backfill_strava_streams.py` and `app.py` post-sync hook.
7. Tests, then full `pytest`.

## Test strategy
Synthetic fixture matching the captured shape (equal-length lists, `moving` bools, a time gap): hand-computed avg/max HR, moving time, pace (AC1–2); missing `heartrate`/`moving`/`distance` → None (AC3); `{}` → `no_streams`, no track row (AC4); run twice → one track row, zero second requests (AC5, AC7); Peloton-linked row → mock session asserts zero calls (AC6); 401, redirect, HTML-200 → stops, row NULL, cookie handling per existing tests (AC9); 429 with `Retry-After` → `TransientError(retry_after_s)`; 5xx; interrupted run then rerun picks up the rest (AC8); renormalize keeps enriched values; `total_work` present/absent (AC10). Inject the sleep function, so tests don't wait. No live calls. Coordinates synthetic.

## Risks
- **Live verification gaps** (BO): real 429/ban behaviour is unmeasured; start with `limit=20` and watch. `moving` semantics and pace match the spike (62:13, ~7:43/km) must be checked on the real run; the doc's formula gives ≈7:44, so accept ±1 s/km.
- Dedup must have run before the backfill, or a Peloton-copy ride is fetched needlessly (harmless, one wasted request); the script runs `run_backfill` first for this reason.
- The raw payload is mutated (enrichment keys); the list-feed fields in it stay untouched.
