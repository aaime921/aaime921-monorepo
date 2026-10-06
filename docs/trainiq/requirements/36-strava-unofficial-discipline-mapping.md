# Requirements: Map `strava_unofficial` Activities to Canonical Discipline Taxonomy

**Issue:** #36
**Related:** #30, PR #31 (introduced `StravaUnofficialConnector`)

## Summary

Every one of the 352 activities imported so far by `StravaUnofficialConnector` is stored with `discipline = other`, even though the raw payload clearly identifies the sport (Run, Ride, Walk, Workout). The root cause is that `trainiq/normalization/taxonomy.py`'s `_PROVIDER_MAPS` has no entry for the provider key `strava_unofficial` — `map_discipline()` takes the "no mapping defined for this provider" branch and returns `OTHER` for every record, logging a warning each time (confirmed 352 times in the BO's `diagnostic.log`). This breaks discipline-dependent analytics (cycling TSS vs. TRIMP, per-sport trends) for every Strava-unofficial activity. The connector itself already extracts `discipline_raw` correctly (`activity_type_display_name`, falling back to `display_type`); this is purely a missing taxonomy registration, not an extraction bug.

## Scope

- Register `strava_unofficial` as a provider in `_PROVIDER_MAPS` so `map_discipline("strava_unofficial", ...)` no longer falls back to OTHER via the "unrecognized provider" path.
- Give `Walk` an explicit, deliberate mapping decision for `strava_unofficial` (OTHER is an acceptable outcome, but it must be a named entry in the map, not a side effect of the "unrecognized discipline_raw" warning-fallback path).
- Bump `MAPPING_VERSION` with a dated comment describing the `strava_unofficial` addition, per the module's existing convention.
- Provide a way to re-normalize the 352 already-stored `normalized_activities` rows for `provider='strava_unofficial'` from their existing `raw_activities.payload_json` (which is kept, per the connector's normal retention), so they stop reading as `other`. Document in code/handoff how this runs (e.g. automatic re-normalization on next sync, or an idempotent one-off script) — the exact mechanism is an Architect decision (see "Open questions").
- Unit tests covering the discipline values actually observed in the BO's database for this provider: `Run`, `Ride`, `Walk`, `Workout` (see table below). `Mountain Bike Ride` does not need a separate test fixture value for `discipline_raw` itself — per the connector's existing extraction order (`activity_type_display_name` preferred over `display_type`), a raw payload with `display_type="Mountain Bike Ride"` and `activity_type_display_name="Ride"` already produces `discipline_raw="Ride"`, not `"Mountain Bike Ride"`. A fixture-based test should confirm this end-to-end (raw payload → `normalize()` → `map_discipline()`) rather than assume it.

### Evidence (BO's DB, 2026-10-06; ground truth, not to be re-verified)

| `display_type` | `activity_type_display_name` | count | expected discipline |
|---|---|---|---|
| Run | Run | 164 | running |
| Ride | Ride | 152 | cycling |
| Walk | Walk | 26 | other (explicit) |
| Mountain Bike Ride | Ride | 8 | cycling |
| Workout | Workout | 2 | strength |

### Out of scope

- Actually executing the re-normalization against the BO's live production database. Per this project's live-verification constraint, the CI pipeline has no access to real BO accounts/data; the deliverable is the code/script and its tests against fixture data reproducing the shapes above. The BO runs the mechanism themselves against their own database once this ships — that is an ops task, not a pipeline deliverable (same pattern as "real-account backfill/migration" in this project's established out-of-scope list).
- Any change to discipline mapping for providers other than `strava_unofficial` (`strava`, `peloton`, `peloton_csv`), beyond whatever the Architect decides is the minimal consequence of adding the `Walk` entry (see open question below).
- Any change to `strava_unofficial.normalize()`'s extraction order/logic — the issue and the connector code agree it already extracts `discipline_raw` correctly.
- New discipline categories beyond the existing `Discipline` enum (`cycling`, `running`, `strength`, `yoga`, `other`).

## Acceptance criteria

1. `map_discipline("strava_unofficial", "Run")` returns `Discipline.RUNNING`.
2. `map_discipline("strava_unofficial", "Ride")` returns `Discipline.CYCLING`.
3. `map_discipline("strava_unofficial", "Workout")` returns `Discipline.STRENGTH`.
4. `map_discipline("strava_unofficial", "Walk")` returns `Discipline.OTHER` via an explicit map entry — i.e. this call does not log the "unrecognized {provider} discipline_raw" warning, because `Walk` is a known, deliberately-mapped key, not a miss.
5. An end-to-end fixture test (raw `strava_unofficial` payload with `display_type="Mountain Bike Ride"`, `activity_type_display_name="Ride"`, through `normalize()` and `map_discipline()`) resolves to `Discipline.CYCLING`.
6. `MAPPING_VERSION` is incremented from its current value of `1`, with a dated comment in the module documenting the `strava_unofficial` addition.
7. A re-normalization mechanism exists (automatic on next sync, or a standalone script) that, given existing `raw_activities` rows for `provider='strava_unofficial'`, recomputes `discipline` via the fixed mapping and updates the corresponding `normalized_activities` rows, without modifying or deleting the stored raw payloads. The mechanism is idempotent (safe to run more than once with no further changes after the first correct run) and is documented (README, docstring, or handoff comment) well enough for the BO to run it against their own database.
8. All existing `taxonomy.py` and `strava_unofficial` connector/normalization unit tests continue to pass.

## Open questions

Not blocking — these are implementation-shape decisions for the Architect, not business decisions:

- **Shared vs. separate map for `Walk`.** The official `strava` connector can also emit `sport_type`/`type` = `"Walk"` (it's a valid REST taxonomy value) and today it falls to `OTHER` via the same unrecognized-value warning path this issue is fixing for `strava_unofficial`. The Architect should decide whether `strava_unofficial` reuses `_STRAVA_MAP` directly (same pattern `peloton_csv` already uses for `_PELOTON_MAP`), in which case adding a `Walk` entry there also makes official Strava's `Walk` handling explicit as a side effect — or whether `strava_unofficial` gets its own map so official Strava's behavior is untouched by this issue. Either satisfies the acceptance criteria above; this doc does not mandate one or the other.
- **Re-normalization mechanism.** "Automatic on next sync" vs. "one-off script" (AC 7) is left to the Architect. Whichever is chosen must be idempotent and must not require the BO to touch `raw_activities` manually.
