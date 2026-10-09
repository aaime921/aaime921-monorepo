# Requirements: Fix Peloton Class Lookup — `peloton_id` Is a Session Id, Not a Ride Id (resolves BL-011)

**Issue:** #58
**Priority:** high (BO: "The #46 class metadata doesn't work live.")
**Related:** #46 / PR #55 (introduced the broken lookup), BL-011 (the
unverified-assumptions backlog item this issue disproves), #47 (shares the
per-workout fetch pattern)

## Summary

Issue #46 shipped Peloton class metadata (title, instructor, class type,
planned length) by calling `GET /api/ride/{peloton_id}/details` for every
class workout, using the workout's own `peloton_id` field directly as the
ride id (`RIDE_ID_FIELD = "peloton_id"`, `connectors/peloton.py:241`). That
assumption was flagged UNCONFIRMED at the time (BL-011) and has now been
disproved against the BO's real account: `backfill_peloton_class_metadata.py`
failed for **131 of 131** existing class workouts, every one landing on
`class_type = 'lookup_failed'`. Live, read-only testing against the BO's
account (2026-10-09, one ride dated 2026-10-07) shows why: a workout's
`peloton_id` is actually a **class session id**, not a ride id.
`GET /api/ride/{peloton_id}/details` and `GET /api/ride/{peloton_id}` both
404 on it. The session id must first be resolved through a different
endpoint, `GET /api/peloton/{peloton_id}`, which returns a session object
containing the real `ride_id`; **that** id is what `GET /api/ride/{ride_id}/details`
accepts, and it returns the actual class metadata (title, duration,
discipline, difficulty estimate, instructor, class types). This issue fixes
the resolution path so class metadata actually populates — for the 131
already-failed workouts via a backfill retry, and for every class workout
going forward.

## Scope

- **Two-step lookup.** Replace the current single-call lookup
  (`peloton_id` used directly as a ride id against
  `RIDE_DETAIL_ENDPOINT_TEMPLATE`) with the verified two-step path:
  1. `GET /api/peloton/{peloton_id}` → a class *session* object (confirmed
     live keys include `ride_id`, `scheduled_start_time`, `is_live`,
     `is_encore`) → extract `ride_id`.
  2. `GET /api/ride/{ride_id}/details` → the real ride/class object
     (confirmed live keys: `ride.title`, `ride.duration`,
     `ride.fitness_discipline`, `ride.difficulty_estimate`,
     `ride.instructor.name`, `ride.instructor_id`, `ride.class_type_ids`;
     top-level `class_types[].name`, `is_power_zone_class`).
  A 404 at either step is a failed lookup for that workout (see "Missing/
  failed lookups" below) — never treated as success with partial data.
- **Field mapping (supersedes #46's UNCONFIRMED mapping):**
  - `activity_title` ← `ride.title`
  - `instructor_name` ← `ride.instructor.name`
  - `class_type` ← `class_types[].name` (e.g. `"Power Zone"`) — the
    UNCONFIRMED `ride_type_id`-based mapping from #46 is replaced by this
    live-verified field. If a ride's `class_types` has more than one
    entry, how multiple values are represented (e.g. joined string, first
    value, or a schema change) is an Architect decision — this doc states
    only that the `name`s from `class_types` are the source, not the exact
    on-disk representation.
  - `planned_duration_s` ← `ride.duration`
  - `provider_class_id` ← the **resolved** `ride_id` from step 1 — not the
    workout's original `peloton_id`. This is a behavior change from #46:
    the stored class id must be the real ride id, since that's what's
    stable and shared across repeats of the same class, while `peloton_id`
    is per-session and unique to one attendance.
  - Optionally, also persist `ride.difficulty_estimate` (the BO
    specifically suggested this: "cheap, useful for training history").
    Since the ride-details response is already being fetched, capturing
    this field costs nothing extra on the wire — whether to add a new
    column for it (schema change) is the Architect's call, same as any
    other schema decision in this pipeline.
- **Caching.** Repeated classes must cost at most one ride-details lookup
  for the life of a single sync/backfill run:
  - Cache the session→ride mapping (`peloton_id` → `ride_id`) so a
    workout whose session id was already resolved this run isn't resolved
    twice.
  - Cache ride details by the resolved `ride_id` (as #46 already does) so
    two different sessions of the same class (e.g. the BO took "45 min
    Power Zone Max Ride with Matt Wilpers" twice) still cost only one
    `GET /api/ride/{ride_id}/details` call.
  - Net effect: an uncached class workout costs up to two calls (session
    lookup + ride-details lookup); a workout whose session or resolved
    ride id was already seen this run costs fewer.
- **Rate limiting.** The existing 429/`Retry-After` handling
  (ADR-037, already covering `fetch_class_details()` and the rest of this
  connector) applies unchanged to both calls in the new two-step lookup —
  this issue doubles per-uncached-workout call volume, it doesn't change
  the retry/backoff contract.
- **Backfill retry.** Re-running
  `scripts/backfill_peloton_class_metadata.py --retry-failed` against the
  BO's data must pick up all 131 rows currently stuck at
  `class_type = 'lookup_failed'` and resolve them via the fixed two-step
  lookup, writing real class metadata. Expected result: ~0 remaining
  failures (a workout whose class was genuinely deleted/unavailable on
  Peloton's side is the only expected exception).
- **Live sync.** New Peloton syncs must populate class metadata correctly
  for new class workouts going forward, using the fixed lookup — no
  separate fix needed beyond what the backfill script and `download()`
  share.
- **Tests from fixtures**, at minimum, using the exact response shapes
  given in the issue's live evidence table:
  - The 404 that results from mistakenly calling
    `GET /api/ride/{peloton_id}/details` (and/or `GET /api/ride/{peloton_id}`)
    with a session id — i.e., a regression test proving the old bug stays
    fixed.
  - A successful `GET /api/peloton/{peloton_id}` call returning a session
    object with `ride_id` (plus `scheduled_start_time`, `is_live`,
    `is_encore`).
  - A successful `GET /api/ride/{ride_id}/details` call returning the real
    ride object with the fields listed under "Field mapping" above.
  - A failed lookup at the session-resolution step (404 on
    `GET /api/peloton/{peloton_id}`) and a failed lookup at the
    ride-details step (404 on `GET /api/ride/{ride_id}/details}` using a
    valid resolved `ride_id`) — both must be distinguishable test cases,
    since they're two different failure points in the same chain.
  - The caching behavior: a second workout sharing either the same
    `peloton_id` or the same resolved `ride_id` within one run triggers no
    additional network call.

### Out of scope

- **Running the backfill against the BO's live Peloton account/production
  database.** Per this project's live-verification constraint, the
  pipeline delivers and fixture-tests the fix and the backfill tool; the
  BO running `--retry-failed` against their real 131 rows is an ops task,
  same as #46's backfill itself.
- **Exact representation of multiple `class_types` entries**, and whether
  `difficulty_estimate` gets a new schema column — both Architect
  decisions (see "Field mapping" above).
- **Any other Peloton connector behavior** (authentication, OAuth,
  pagination, non-class workout handling, distance-unit conversion) —
  unaffected by this fix and out of scope here.
- **Issue #47's heart-rate capture** — the issue text notes it "shares the
  per-workout fetch pattern." Whether the two can share the new
  session-resolution call is an Architect decision if/when #47 is worked;
  it does not block this issue.

## Acceptance criteria

1. The connector resolves a class workout's `peloton_id` via
   `GET /api/peloton/{peloton_id}` to obtain `ride_id`, then fetches class
   details via `GET /api/ride/{ride_id}/details` using that resolved id —
   replacing the direct `peloton_id`-as-ride-id call that caused 131/131
   lookup failures. This resolves BL-011.
2. Stored fields map as: `activity_title` ← `ride.title`,
   `instructor_name` ← `ride.instructor.name`, `class_type` ← derived from
   `class_types[].name`, `planned_duration_s` ← `ride.duration`,
   `provider_class_id` ← the resolved `ride_id` (not the original
   `peloton_id`). `duration_s` (actual duration) is unaffected.
3. Within a single sync or backfill run, a repeated class — whether via
   the same `peloton_id` seen twice or two different `peloton_id`s
   resolving to the same `ride_id` — triggers at most one
   `GET /api/peloton/{peloton_id}` call per distinct `peloton_id` and at
   most one `GET /api/ride/{ride_id}/details` call per distinct `ride_id`.
4. A lookup failure at either step (session resolution or ride-details
   fetch) is logged as a warning and recorded as the existing
   `lookup_failed` sentinel — never fabricated, and never confused with a
   successful partial result.
5. Running `scripts/backfill_peloton_class_metadata.py --retry-failed`
   against a fixture set shaped like the BO's 131 `lookup_failed` rows
   resolves them to real class metadata via the fixed two-step lookup.
6. New Peloton syncs populate class metadata for new class workouts
   using the same fixed lookup, with no regression to #46's existing
   non-class-workout (`not_a_class`) handling.
7. The connector's existing 429/`Retry-After` handling (ADR-037) applies
   unchanged to both calls in the two-step lookup.
8. Tests cover, at minimum, using the real response shapes from this
   issue's live evidence: the old-bug 404 (session id misused as ride
   id), a successful session resolution, a successful ride-details fetch,
   a failed session resolution, a failed ride-details fetch on an
   otherwise-valid resolved `ride_id`, and the caching behavior from AC3.
9. All existing tests continue to pass.

## Open questions

None blocking. Two items are flagged above as Architect decisions, not BO
questions, because the BO already gave clear direction on both: (a) how to
represent multiple `class_types[].name` values on disk, and (b) whether
`difficulty_estimate` gets a new column — the BO only said "consider"
storing it, so including it is optional/at the Architect's discretion, not
a hard requirement. The live evidence needed to validate correctness (the
2026-10-07 ride's exact request/response shapes for all three endpoints
involved) is already supplied in the issue and quoted verbatim above.
