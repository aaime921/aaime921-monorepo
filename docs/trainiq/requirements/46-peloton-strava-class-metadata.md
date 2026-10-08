# Requirements: Record Class Title, Instructor, Class Type and Planned Length for Every Workout (Peloton + Strava)

**Issue:** #46
**Priority:** high (BO: "critical to record what was done before, to establish the training in the future")

## Summary

TrainIQ currently discards the single most useful piece of context about a
workout: *what was actually trained*. For a Peloton class ride, today's
pipeline stores only the mapped discipline and a duration (e.g. "cycling, 45
min"); the class title, the instructor, the class type (Power Zone Max,
Endurance, Climb, Low Impact, etc.) and the planned/scheduled length are
never fetched or persisted, even though Peloton exposes them on the
ride/class object. For Strava (official and unofficial), the activity's
`name` field — which for a Peloton-synced ride already contains the class
title and instructor, e.g. "45 min Power Zone Max Ride with Matt Wilpers" —
and the raw `sport_type` string are read only transiently (to compute the
canonical `discipline`) and then thrown away; neither is ever persisted.
Confirmed against the actual connector code: `PelotonConnector.download()`
calls only `GET /api/me` and `GET /api/user/{user_id}/workouts`, neither of
which returns class/ride detail; `StravaConnector._activity_to_raw_dict()`
and `StravaUnofficialConnector.normalize()` both drop `name` entirely —
it is never read out of the raw payload or stravalib object at all, let
alone normalized or stored. The BO wants this fixed going forward **and**
backfilled for the 136 Peloton workouts already in the database, so that
"all Power Zone rides with Matt Wilpers in the last 90 days" (and similar
instructor/class-type queries) becomes answerable.

## Scope

- **Peloton class metadata.** For every Peloton workout whose
  `workout_type` is `"class"`, fetch and persist the class title,
  instructor name, class type (Peloton's own category/ride-type value —
  whatever taxonomy the live-captured class/ride response actually
  provides), the planned/scheduled class length (seconds), and the
  Peloton class id (`peloton_id` on the raw workout). The call used to get
  this (e.g. the workouts list with `joins=ride,ride.instructor`, vs.
  `GET /api/ride/{peloton_id}/details` per workout) is an Architect
  decision, made against a **live capture** — per this project's
  evidence-based principle, this endpoint/response shape has not yet been
  verified against a real account (no capture exists today under
  `docs/trainiq/verification/`, unlike the workouts-list endpoint itself,
  which issue #5 already verified). Today's `duration_s` (derived from
  actual start/end time) is unchanged — this is new, additional data, not
  a replacement.
- **Non-class Peloton workouts.** A Peloton workout that isn't a class
  (just-ride / scenic / free mode, or any class workout whose ride/class
  lookup genuinely fails) gets an explicit, non-NULL type/category value
  that distinguishes "not a class" or "lookup failed" from "class, but no
  instructor" — never silently left blank in a way indistinguishable from
  an unattempted lookup.
- **Strava `name` and raw `sport_type`.** For every activity from
  `StravaConnector` **and** `StravaUnofficialConnector` (both currently
  drop `name` identically — confirmed in code, not assumed; `sport_type`
  is read only transiently today to compute canonical `discipline` via
  `map_discipline()` and never persisted), store the raw `name` string and
  the raw `sport_type`/`activity_type_display_name` string as given by the
  provider.
- **Linked-pair precedence.** For a Peloton workout linked to its Strava
  copy via `dedup_links` (issue #37, shipped), Peloton's class metadata
  (title/instructor/class type/planned length) is the data to use as the
  authoritative record for that workout. Strava's `name` remains stored on
  the Strava-side row regardless (per the "never fabricate / never
  discard" principle — both sides keep their own data), and is the
  fallback source for Strava-only activities (runs, walks) that have no
  Peloton counterpart.
- **Backfill tool.** A resumable, rate-limited one-off tool/script that
  walks existing Peloton workouts already in the database and fetches
  their class metadata via the same lookup added above, writing results
  incrementally so an interrupted run can resume rather than restart.
  Must be exercisable in tests via fixtures (simulated pagination,
  simulated rate-limit/429 response, simulated resume-after-interruption)
  — see "Live-verification constraint" below for why actually running it
  against the BO's live account is out of scope here.
- **Missing/failed lookups.** If a class lookup fails (deleted class,
  Peloton API change, not found) for either the live sync path or the
  backfill tool, that failure is logged and the record's class-metadata
  fields are stored as NULL — never fabricated or guessed.
- **Tests from fixtures**, at minimum: a Peloton class workout with full
  metadata, a Peloton just-ride/non-class workout, a Peloton class workout
  whose class lookup fails, a Strava (official) activity with `name` and
  `sport_type`, and a Strava-unofficial activity with the same.

### Out of scope

- **Running the backfill against the BO's live Peloton account/production
  database.** Per this project's live-verification constraint, CI has no
  access to real BO accounts, so the pipeline can only deliver and
  unit-test the backfill tool itself (see "Backfill tool" above) — the BO
  actually invoking it against their 136 real records once this ships is
  an ops task, same as any other real-account backfill/migration.
- **Exact schema design** (new columns on `normalized_activities` vs. a
  separate table, e.g. `activity_details`) — the Architect's decision, as
  the issue itself already frames it. This doc states *what* must be
  queryable and persisted, not the column/table layout.
- **Deciding the exact Peloton call** (joins-on-list vs. per-workout
  details endpoint) ahead of a live capture — see "Peloton class
  metadata" above. This doc states the outcome needed (cheapest call that
  gets the data), not which call, since that requires evidence this repo
  doesn't have yet.
- **Any Peloton GraphQL-only features.** Per the connector's existing
  module docstring (R-PELOTON-05), this connector is REST-only; nothing
  here should reopen that boundary.
- **A separate "heart-rate issue"** the BO's issue text references as
  possibly sharing the same per-workout Peloton call — no open issue by
  that description was found in this repo at the time of writing. If one
  exists, the Architect should check whether its call can be shared with
  this one's, but its absence does not block this issue.
- **Any new analytics/summary UI** beyond making instructor and class
  type queryable (e.g. via a documented query against the new
  columns/table). Building a dashboard or report consuming it is future
  work, not this issue's deliverable.

## Acceptance criteria

1. For every synced Peloton workout with `workout_type == "class"`, the
   stored record includes: class title, instructor name, class type, the
   planned/scheduled class length in seconds, and the Peloton class id.
   `duration_s` (actual duration) is unaffected.
2. For every synced Peloton workout that is not a class (or whose class
   lookup fails), the stored record's type/category field explicitly
   distinguishes that case from a successful class lookup — it is never
   NULL/blank in a way that could be confused with "lookup not attempted."
   Instructor is NULL for these (there genuinely isn't one), but the
   failure/non-class reason itself is captured, not silently absent.
3. For every synced Strava (official) and Strava-unofficial activity, the
   stored record includes the provider's raw `name` and raw `sport_type`
   (or `activity_type_display_name`/`display_type` fallback, matching the
   existing `discipline_raw` fallback order for strava_unofficial) values,
   independent of whether that activity is later linked to a Peloton
   workout.
4. For a Peloton↔Strava pair already linked in `dedup_links` (#37), a
   query against the stored data returns the Peloton side's class title,
   instructor, and class type as the record for that workout; Strava's
   `name` for that same workout remains stored on its own row and is not
   lost, but is not what such a query surfaces when a Peloton class match
   exists.
5. Instructor name and class type are queryable — e.g., "all Power Zone
   rides with Matt Wilpers in the last 90 days" can be expressed as a
   query against the stored data. The exact query is documented in the
   architecture/design doc.
6. A resumable, rate-limited backfill tool exists that, run against the
   existing Peloton workouts in the database, fetches and persists class
   metadata for each. Tested via fixtures covering: a successful run,
   simulated rate-limiting (the tool backs off/retries rather than
   failing the whole run), and resuming after a simulated interruption
   partway through (already-processed workouts are not re-fetched or
   duplicated).
7. A class lookup that fails for any reason (deleted class, API error,
   not found) results in a logged warning and NULL class-metadata fields
   for that workout — never a fabricated or guessed value — for both the
   live sync path and the backfill tool.
8. Tests cover, at minimum: a Peloton class workout with full metadata, a
   Peloton non-class workout, a Peloton class workout with a failed
   lookup, a Strava (official) activity with `name`/`sport_type`, and a
   Strava-unofficial activity with the same fields from its own raw
   payload shape.
9. All existing tests continue to pass.

## Open questions

None blocking. The remaining decisions — the exact Peloton call to use
(pending a live capture, which is itself part of the Architect's job per
the issue), the schema/table layout, the exact class-type taxonomy value
set, and how the backfill tool is invoked (CLI flag, standalone script) —
are normal Architect/Developer judgment calls within the rules stated
above, not business decisions that need to come back to the BO. The BO has
already supplied the ground truth needed to validate correctness (the
2026-10-07 ride example: Peloton stores "cycling, 45 min" today; the real
record is "Power Zone Max", Matt Wilpers, 45 min class) and the exact
backfill count (136 existing Peloton workouts).
