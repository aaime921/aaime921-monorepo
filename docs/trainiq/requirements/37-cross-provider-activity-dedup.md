# Requirements: Cross-Provider Activity Deduplication (Peloton ↔ Strava)

**Issue:** #37

## Summary

The same real-world ride is stored twice when it's uploaded to both Peloton
and Strava (official `strava` or `strava_unofficial`): once from each
provider, with nothing linking the two rows. The BO's database currently has
**54** Peloton activities with a `strava`/`strava_unofficial` activity
starting within ±5 minutes of them, and `dedup_links` — a table that already
exists in the schema specifically for "merge/flag decisions with confidence
scores" — has **0 rows**. Every total that sums across `normalized_activities`
(time, distance, training load) is currently double-counting these 54+
workouts. The BO wants cross-provider duplicates detected, scored, and
recorded with an explicit winner rule, including a one-time pass over the
data that already exists today — not just protection against new
duplicates going forward.

This is a missing feature, not a regression: the table was designed for this
purpose (referenced in `synthetic_dataset.py` as "Deduplication confidence
scoring, Milestone 4 §4", which already ships a
`scenario_cross_provider_duplicates()` fixture) but no code has ever written
to it.

## Scope

- Detect duplicate **ACTIVITY** records across providers — at minimum every
  pairing among `peloton`, `strava`, and `strava_unofficial` (all three
  write to `normalized_activities`; the issue explicitly calls out "both
  [Strava] connectors").
- Matching must use start-time proximity as the primary signal, **plus at
  least one other signal** (duration proximity and/or matching canonical
  `discipline`), with a numeric confidence score per candidate pair.
  - Start-time comparison must account for the existing format mismatch
    confirmed in code today: `PelotonConnector.normalize()`
    (`trainiq/connectors/peloton.py`) passes Peloton's raw epoch-integer
    `start_time` straight through into `normalized_activities.start_time`,
    while `StravaConnector`/`StravaUnofficialConnector` store an ISO 8601
    string there. Both land in the same `TEXT` column today. Any comparison
    has to normalize both sides to the same representation before comparing
    — don't assume the column already holds one consistent format.
  - The BO's own data gives the working proximity window: real duplicate
    pairs in production start within **±5 minutes** of each other. Use that
    as the starting point for the time-proximity signal; the Architect can
    tune it if evidence (e.g. the fixture scenario's ~3 minute clock skew)
    warrants.
- Record every evaluated pair's outcome in `dedup_links`
  (`activity_id_a`, `activity_id_b`, `confidence_score`, `resolution`) using
  the table's existing columns — no schema change should be needed for this.
- **Winner rule** (already settled by the BO, not an open design question):
  Peloton is primary for rides because it carries power/HR; Strava
  (official or unofficial) contributes GPS/distance. `resolution` must
  record which side is primary according to this rule. Document the rule
  itself in the code (e.g. a module docstring) so it doesn't have to be
  re-derived from this issue later.
- State and document an explicit confidence threshold that separates
  **automatic linking** from **flagged-as-ambiguous**. Below the threshold,
  record the pair as needing review in `resolution` — never silently merge
  it. The BO did not hand down a specific number, so picking and documenting
  one is Architect/Developer work; this requirement is that *some* stated,
  documented threshold exists and is enforced, not what its value is.
- The detector must be runnable as a one-time pass over data that **already
  exists** in `normalized_activities` (the 54 current pairs), not only
  wired into the live sync path for activities ingested from now on. How
  it's invoked (script, CLI flag, one-off migration-style step) is an
  Architect decision.
- Never delete or mutate `raw_activities` or `normalized_activities` rows as
  part of resolving a dedup pair — both sides stay in storage as evidence;
  only `dedup_links` records the merge/flag decision. (This follows the
  project's existing evidence-based principle: data already captured is
  never discarded, only annotated.)
- Tests, at minimum:
  1. An exact start-time match.
  2. A near match within the tolerance window (not identical, still linked).
  3. A non-match: two different activities on the same day that don't
     satisfy the matching rule (must not be linked).
  4. A pair where one side's stored `start_time` is Peloton's epoch int and
     the other is Strava's/strava_unofficial's ISO 8601 string, proving the
     comparison handles today's actual stored formats correctly.

### Out of scope

- Any connector/provider pairing beyond `peloton` ↔ `strava` ↔
  `strava_unofficial`. If the Architect finds the same overlap risk applies
  elsewhere (e.g. a future Garmin connector), that's a separate issue.
- Building a new analytics/totals/summary layer. **As of this issue, no such
  layer exists in the codebase** — `app.py` only logs a per-run sync summary;
  nothing currently sums time, distance, or training load across
  `normalized_activities`. The issue's acceptance criterion "analytics and
  summaries count each linked pair once" is satisfied here by making
  `dedup_links` queryable to resolve a pair to its primary record (e.g. a
  helper/view the Architect designs); actually building totals/analytics
  that consume it is future work, not this issue's deliverable.
- Running the detector against the BO's live production database as part
  of this pipeline delivery. Per this project's live-verification
  constraint, the CI pipeline has no access to the BO's real accounts or
  database — the deliverable must be verified via unit tests / fixture
  data (the existing `scenario_cross_provider_duplicates()` fixture is
  directly usable for this). The BO running the one-time pass against their
  own database once it ships is an ops task, same as any other
  real-account backfill.
- Any UI/CLI surfacing of flagged-ambiguous pairs beyond what's needed to
  record them in `dedup_links`. Not requested by the BO; add only if the
  Architect finds it's required to satisfy "ambiguous matches are flagged,
  not merged silently" in a verifiable way.
- Changing `normalized_activities.start_time`'s storage format for existing
  or future rows. The issue notes "storing both in one format may itself be
  worth doing" as a possibility, not a requirement — leave that decision to
  the Architect; this issue's own matching logic must work with the current
  mixed-format reality regardless.

## Acceptance criteria

1. For every pair of `normalized_activities` rows from two different
   providers among `peloton`, `strava`, `strava_unofficial`, whose start
   times — after normalizing Peloton's epoch-int and Strava's/
   strava_unofficial's ISO-8601 `start_time` to a common comparable form —
   fall within the proximity window, **and** at least one of (duration
   within a stated tolerance, matching canonical `discipline`) also agrees,
   the pair is scored for a dedup link.
2. Every scored pair gets a numeric `confidence_score`, and a stated,
   documented threshold decides whether it's auto-linked or flagged as
   ambiguous. A pair below the threshold is never silently merged.
3. Linked and flagged pairs are both written to `dedup_links`
   (`activity_id_a`, `activity_id_b`, `confidence_score`, `resolution`);
   `raw_activities` and `normalized_activities` rows are never deleted or
   modified by this process.
4. For an auto-linked pair, `resolution` records which side is primary
   following the BO's rule: Peloton primary for rides (power/HR), Strava
   (official or unofficial) contributing GPS/distance. This rule is
   documented in the code, not only in this doc.
5. The detector can be run over data already stored in
   `normalized_activities` today (not only over newly-synced activities),
   and doing so against a fixture reproducing the BO's reported 54
   Peloton/Strava pairs (±5 minutes apart) produces 54 recorded links (or
   fewer, only if the Architect's chosen secondary signal correctly and
   intentionally excludes some of them — any such exclusion must be
   explainable, not silent data loss).
6. A pair scored below the auto-link threshold is recorded in `dedup_links`
   with a `resolution` value that distinguishes it from an auto-linked
   pair, so it can be queried separately for human review.
7. Tests cover: an exact start-time match, a near match within tolerance, a
   non-match (two different activities on the same day), and a pair mixing
   Peloton's epoch-int and Strava's ISO-8601 `start_time` formats.
8. All existing tests continue to pass.

## Open questions

None blocking. The BO's issue already supplies the matching evidence (the
±5 minute window observed in production), the winner rule (Peloton
primary for power/HR, Strava for GPS/distance), and the constraint that this
must work on existing data, not just new syncs — the remaining decisions
(exact threshold value, tolerance for the secondary signal, how the backfill
pass is invoked, where `dedup_links` is queried from) are normal Architect
/Developer judgment calls within the stated rules, not business decisions
that need to come back to the BO.
