# Requirements: Keep Elevation Gain, Moving Time and Indoor/Outdoor Flag (Strava)

**Issue:** #48
**Priority:** low (as labeled on the issue)

## Summary

The Strava (unofficial) connector's web payload carries several fields that
are currently read into `raw_activities` but then dropped during
normalization: elevation gain, moving time (as distinct from elapsed time),
and whether the activity was indoors (trainer) or outdoors. These matter
most for the ~190 of 352 Strava-only activities (runs and walks) that have
no Peloton copy to fall back on for this context — hill load on a run,
true pace excluding stops, and indoor-vs-outdoor are otherwise invisible for
those records. `name` and `sport_type` are already covered by issue #46 and
are explicitly out of scope here.

## Scope

- **`strava_unofficial` connector (`trainiq/connectors/strava_unofficial.py`,
  `normalize()`).** Confirmed in code: `normalize()` already receives the
  full per-activity web payload (the raw dict from
  `/athlete/training_activities`'s `models` array is stored into
  `raw_activities` unchanged — unlike the official connector, see below),
  but reads nothing from `elevation_gain_raw`, `moving_time_raw`, or
  `trainer` today. Add three canonical fields to its output:
  - `elevation_gain_m` — from `elevation_gain_raw` (metres).
  - `moving_time_s` — from `moving_time_raw` (seconds). This is distinct
    from the existing `duration_s`, which uses `elapsed_time_raw` (total
    elapsed, including stops) and is unchanged by this issue.
  - `is_indoor` — boolean, from `trainer`.
- **Official `strava` connector (`trainiq/connectors/strava.py`) — "where
  the same data exists."** Confirmed in code: stravalib's activity object
  exposes the equivalent fields (`total_elevation_gain`, `moving_time`,
  `trainer`), but `_activity_to_raw_dict()` — the method that defines what
  actually gets persisted to `raw_activities` for this connector — does not
  currently capture any of the three. They must be added there (not only in
  `normalize()`), or they're discarded before normalization ever sees them.
  Flagging this as a scope item since it's easy to miss: fixing only
  `normalize()` for the official connector would be a silent no-op.
- **NULL on missing data.** If a source field is absent, `None`/`null`, or
  the activity type doesn't report it, the corresponding canonical field
  (`elevation_gain_m`, `moving_time_s`, `is_indoor`) stays `None`. Never
  fabricated, approximated, or defaulted (e.g. `is_indoor` must not default
  to `False` when `trainer` is simply missing from the payload — that is a
  different thing from a confirmed-outdoor activity).
- **Backfill of existing rows**, via the existing re-normalization
  machinery (`trainiq/normalization/renormalize.py`'s
  `renormalize_provider()`, as already invoked by
  `scripts/renormalize_strava_unofficial.py` for this provider — see
  "Backfill note" below for a constraint on the official-connector side).

### Backfill note (official `strava` connector)

`renormalize_provider()` re-derives `normalized_activities` purely from
whatever is already sitting in `raw_activities` — it never re-fetches from
the provider. For `strava_unofficial`, that's sufficient: the full payload
(including `elevation_gain_raw`, `moving_time_raw`, `trainer`) is already in
every stored raw row, so re-normalizing existing rows will pick up the new
fields immediately. For the official `strava` connector, this is **not**
guaranteed to be sufficient: today's `_activity_to_raw_dict()` never wrote
`total_elevation_gain`, `moving_time`, or `trainer` into `raw_activities` in
the first place, so rows synced before this fix may have `raw_activities`
payloads that genuinely lack this data — re-normalization alone can't
produce it for those rows no matter what `normalize()` does. This is a
factual constraint on what re-normalization can achieve, not a business
decision; the Architect should confirm it against the actual stored
`strava` raw payloads and document whether existing official-Strava rows
need a fresh sync instead of (or in addition to) re-normalization.

### Out of scope

- **`has_latlng` (GPS-present flag).** The issue's field table lists it
  alongside the other three fields and explains why it's useful, but it is
  **not** in the issue's acceptance criteria. Treating that as a deliberate
  scope cut rather than an oversight: this doc's acceptance criteria below
  cover only `elevation_gain_m`, `moving_time_s`, and `is_indoor`. If the BO
  wants `has_latlng` captured too, that's a follow-up issue, not implied
  scope here.
- **`name` and `sport_type`.** Explicitly called out in the issue itself as
  covered by the class-metadata issue (#46).
- **Any new analytics, UI, or query surface** built on top of these fields.
  This issue is about capturing and persisting the data, not presenting it.
- **Re-fetching data from Strava.** If the backfill note above means some
  official-Strava rows need a fresh sync rather than re-normalization,
  actually running that sync against the BO's live account is an ops task
  (per this project's live-verification constraint), not a pipeline
  deliverable — same treatment as every other real-account backfill in this
  repo.

## Acceptance criteria

1. For every synced `strava_unofficial` activity, the stored record
   includes `elevation_gain_m` (from `elevation_gain_raw`), `moving_time_s`
   (from `moving_time_raw`, distinct from `duration_s`), and `is_indoor`
   (from `trainer`).
2. For every synced official `strava` activity, the same three fields are
   populated from the stravalib activity object's equivalent attributes
   (`total_elevation_gain`, `moving_time`, `trainer`), which requires
   capturing them in `_activity_to_raw_dict()` as well as `normalize()`.
3. When the underlying source field is missing or null, the corresponding
   canonical field is stored as `None` — never defaulted (in particular,
   `is_indoor` is `None`, not `False`, when `trainer` is absent from the
   payload).
4. Existing `strava_unofficial` rows are corrected by re-running the
   existing re-normalization script/function
   (`renormalize_provider()` / `scripts/renormalize_strava_unofficial.py`)
   against already-stored `raw_activities` payloads — no re-fetch needed
   for this provider.
5. For existing official-`strava` rows, the Architect/Developer document
   (per the "Backfill note" above) whether re-normalization alone is
   sufficient or whether rows synced before this fix need a fresh sync to
   populate `raw_activities` with the newly-captured fields first.
6. Tests cover, at minimum: an outdoor run or ride with elevation gain
   present and `trainer=false`, an indoor trainer ride (`trainer=true`,
   elevation gain typically absent or zero), and a payload missing all
   three source fields entirely (confirms `None`, not a fabricated
   default).
7. All existing tests continue to pass.

## Open questions

None blocking. `has_latlng` is addressed above as a deliberate scope cut
(not an open question needing BO input), and the official-connector backfill
mechanics are a technical finding for the Architect to confirm, not a
business decision. The BO's evidence (the field table and the ~190/352
Strava-only activity count) is sufficient ground truth to proceed.
