# Requirements: Capture Peloton Heart-Rate Data (avg/max HR, HR Zones, Max Power)

**Issue:** #47
**Priority:** medium
**Related:** #46 (shares the per-workout Peloton fetch/backfill mechanism — explicit BO directive, see "Scope" below), #37 (dedup linked pairs), Epic 7 (training load / TRIMP, blocked on this)

## Summary

TrainIQ stores no heart-rate data for any of the BO's 136 Peloton workouts
(`avg_hr`/`max_hr` NULL on all of them), even though the BO trains with a
heart-rate monitor and Peloton's own payload already carries HR-zone
durations and effort points per workout. The planned TRIMP-based
training-load calculation for non-cycling disciplines (Epic 7) cannot be
computed for Peloton without this data. Two different gaps are involved,
confirmed to different degrees:

1. **Already available, just not stored.** The 2026-10-07 ride evidence the
   BO captured, and the existing live verification already on file
   (`docs/trainiq/verification/peloton-2026-09-28.md`, its second example
   record), both show `effort_zones.heart_rate_zone_durations` (z1–z5,
   seconds) and `effort_zones.total_effort_points` already present on the
   **same** `GET /api/user/{user_id}/workouts` record `download()` already
   fetches — no new endpoint call is needed for this part. These fields are
   simply never read by `normalize()` today.
2. **Not yet live-verified in this repo.** The issue's claim that `avg_hr`,
   `max_hr`, and `max_power` (and avg cadence) come from a separate
   per-workout performance endpoint (`GET
   /api/workout/{id}/performance_graph?every_n=...`) has not been confirmed
   against a live capture anywhere under `docs/trainiq/verification/` —
   unlike the zone-duration fields above. Flagging this per the
   evidence-based principle: this is the issue author's own investigation
   note, not a captured record. **This is analogous to the still-open Task 1
   verification gap in #46's architecture doc** (same connector, same
   "per-workout detail endpoint, shape unconfirmed" situation) — the
   Architect should verify it the same way (a manual-bearer-token diagnostic
   against the real account), not design against the assumed
   endpoint/parameter shape.

## Scope

- **HR-zone durations and effort points from the existing list endpoint.**
  Store `heart_rate_zone_durations` (z1–z5, seconds) and
  `total_effort_points` for every Peloton workout that has them, sourced
  from the existing `GET /api/user/{user_id}/workouts` call. Confirm
  against a live record whether the issue's `hr_total_points` is the same
  value as `total_effort_points` under a different key, or genuinely
  distinct (Architect to verify). A workout with `effort_zones: null`
  (already confirmed to occur — see the first example record in the
  verification file) stores all of these as NULL, never zero.
- **Avg/max HR, max power, avg cadence from the per-workout performance
  endpoint.** For every Peloton workout, fetch and store `avg_hr`,
  `max_hr`, and `max_power`. Store avg cadence too only if it comes back
  "for free" in the same response (no extra parsing/storage cost beyond
  what's already being added) — otherwise avg cadence is out of scope for
  this issue. The exact endpoint/parameters are pending the Architect's
  live verification (see Summary, gap 2).
- **Shared per-workout fetch mechanism with #46 (explicit BO directive —
  see the issue's sequencing comments).** #46 is already adding a
  per-workout Peloton detail fetch (ride/class metadata) with a
  skip-if-already-synced-since-checkpoint optimization, an in-run cache, a
  resumable rate-limited backfill script, and a shared
  `retry_with_backoff()` helper (see
  `docs/trainiq/architecture/46-peloton-strava-class-metadata.md`, now on
  `main`; implementation in progress at `stage:dev`). This issue's
  per-workout performance-endpoint fetch must reuse that same mechanism
  (the skip/cache pattern, the backfill script's structure,
  `retry_with_backoff()`) rather than building a second, parallel one — the
  BO was explicit about this ("should share one fetch/backfill mechanism
  instead of building it twice"). Whether that means extending #46's same
  backfill script with added columns, or a second script sharing the same
  helpers, is an Architect/Developer call.
- **Workouts without HR data.** A Peloton workout with no heart-rate data
  at all (no monitor paired, or Peloton simply didn't record it) stores all
  HR-related fields as NULL. Never fabricated or estimated from other
  fields (e.g. never derive an "average" from the zone durations).
- **Backfill for existing 136 workouts.** Same resumable, rate-limited
  requirement as #46's backfill, and per the BO directive above, should
  share that mechanism rather than duplicate it.
- **Tests from a captured performance-endpoint fixture.** Per the
  live-verification constraint (CI has no access to real BO accounts), the
  fixture itself must come from a real captured response once the
  Architect's verification step runs — tests cannot be written against a
  guessed shape.

### Out of scope

- **Exact schema design** (new columns on `normalized_activities` vs.
  extending #46's new columns vs. a separate table) — Architect's decision,
  consistent with #46's precedent.
- **Deciding the exact performance endpoint/parameters** ahead of a live
  capture — Architect's decision (see Summary, gap 2), the same pattern as
  #46's Task 1.
- **TRIMP-based training load itself (Epic 7).** This issue only makes the
  HR data available for that future calculation; computing training load
  from it is separate work.
- **Running the backfill against the BO's live Peloton account/production
  database** — an ops task, same as #46.
- **Avg/max cadence and resistance** beyond "if it's free in the same
  response" (see Scope) — a dedicated cadence/resistance issue is future
  work if the BO wants it guaranteed, not assumed here.

## Acceptance criteria

1. For every Peloton workout, `heart_rate_zone_durations` (z1–z5, seconds)
   and `total_effort_points` are stored when `effort_zones` is present on
   the raw record, and NULL when `effort_zones` is null — sourced from the
   existing `GET /api/user/{user_id}/workouts` call, no new endpoint
   required for this part.
2. For every Peloton workout, `avg_hr`, `max_hr`, and `max_power` are
   stored when available from the per-workout performance endpoint, and
   NULL when a workout has no HR/power data recorded (e.g. no monitor
   paired) — never fabricated or derived from the zone durations.
3. The per-workout fetch added for this issue reuses the
   skip-if-already-synced-since-checkpoint optimization, in-run cache, and
   `retry_with_backoff()` helper introduced in #46, rather than a second,
   independent implementation of the same pattern.
4. A resumable, rate-limited backfill mechanism covers all existing
   Peloton workouts (136 in the BO's DB) for the fields in ACs 1–2, sharing
   the backfill mechanism built for #46 per the BO's explicit directive.
5. A performance-endpoint fetch failure for any workout is logged and
   results in NULL for that workout's HR/power fields — never a fabricated
   or guessed value — for both the live sync path and the backfill tool.
6. Tests exist from a real captured performance-endpoint fixture (not a
   guessed shape) covering: a workout with full HR/power data, a workout
   with `effort_zones: null`, and a workout where the performance-endpoint
   fetch fails.
7. All existing tests continue to pass.

## Open questions

None blocking. The exact performance-endpoint shape/parameters (Summary gap
2) is a live-verification task for the Architect, the same pattern #46
already used for its own unconfirmed ride/class-detail endpoint — it
doesn't need to come back to the BO, and per #46's precedent should not
block requirements from being handed off. The only genuine dependency is
sequencing: this issue's design and implementation should follow (or at
minimum coordinate tightly with) #46's, since both touch
`connectors/peloton.py`'s per-workout fetch pattern and the BO wants one
shared mechanism, not two. #46 is currently at `stage:dev`; the Architect
should read its current implementation state before designing #47 rather
than duplicating a mechanism #46 may already have built.
