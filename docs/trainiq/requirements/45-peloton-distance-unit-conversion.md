# Requirements: Peloton distance stored ~38% too short — connector assumes km, API returns account's display unit

Issue: #45

## Summary

`PelotonConnector.normalize()` (`trainiq/connectors/peloton.py:142`) always
converts the raw `distance` field to metres by multiplying by 1000
(`DISTANCE_KM_TO_M_MULTIPLIER`), an assumption made in issue #5 from the CSV
importer's own `Distance (km)` column and a plausibility check. Live
evidence captured today from the BO's real account and database shows that
assumption is wrong for this account: Peloton's API returns `distance` in
whatever unit the account's own profile/display settings use (miles here,
not km), and the CSV importer's unit handling is a separate code path that
doesn't tell us anything about what the live API returns. Every Peloton
activity synced through this connector has its `distance_m` stored about
38% short of the true distance (a mile is ~1.609 km, so treating miles as
km under-reports by a factor of 1/1.609 ≈ 0.62).

The BO proved this by comparing the same physical rides as recorded by both
the Peloton connector and the (independently correct) Strava connector,
linked via issue #37's cross-provider dedup pairs: multiplying the raw
Peloton `distance` by the mile-to-km factor (1.609344) instead of 1000
reproduces the Strava distance for the same ride to within the pairs'
normal agreement tolerance, across six independent ride pairs including the
one with full raw evidence (`distance = 13.1816`, Peloton-stored
`13181.6 m`, true distance confirmed by Strava as `21213.7 m`).

The fix must determine the unit from the account itself — per-account
unit, verified per-record where possible — rather than hard-coding either
unit, since the BO has already seen one wrong hard-coded assumption
(issue #5's "km") replaced here by a different hard-coded assumption
("miles") would just repeat the same mistake for any account that actually
is in km.

## Scope

- `PelotonConnector`'s live-sync path (`download()` / `normalize()`):
  determine the unit Peloton is actually reporting `distance` in for the
  connected account, and convert to metres accordingly for both `mi` and
  `km` accounts. The exact source of the unit (an account-level field from
  `GET /api/me`, a field on the workout/summary record itself, or
  something else) is for the Architect to determine from what Peloton's
  real API actually exposes — this requirement is about the outcome
  (correct metres regardless of account unit), not the specific field.
- Unknown/missing unit: distance is logged and stored as `NULL`, never
  guessed at (the project's existing never-fabricate standard, same as
  `avg_hr`/`max_hr`/`max_power` in issue #5).
- One-off correction of existing Peloton rows already stored with the
  wrong (always-km) conversion, re-derived from the stored
  `raw_activities` payload — following the same pattern already
  established by `scripts/renormalize_strava_unofficial.py` /
  `trainiq.normalization.renormalize.renormalize_provider()` for issue #36.
  Raw payloads themselves are not touched or modified.
- Tests covering: a `mi`-unit account, a `km`-unit account, and an
  unknown/missing-unit account (expect `NULL`, logged).
- Documentation: record the live-verified finding (account unit varies,
  API does not follow the CSV export's km assumption) in the same places
  issue #5's finding was recorded — `docs/trainiq/verification/` and
  `BACKLOG.md` as applicable — so this isn't lost the way issue #5's own
  "verify distance unit" open item apparently was.

### Out of scope

- `peloton_csv` import (`trainiq/csv_import/peloton_csv.py`) — its own
  `Distance (km)`-column handling is already correct for the CSV export
  format and is explicitly unaffected by this issue. Do not change it.
- Re-fetching from Peloton's live API to re-verify or backfill — the
  one-off correction re-derives from already-stored `raw_activities`
  payloads only, per the project's live-verification constraint (CI has no
  access to real BO accounts).
- Any change to authentication, OAuth, or the manual-recovery path.
- Canonical discipline taxonomy, resume-cursor handling, or anything else
  in `normalize()`/`download()` not related to `distance`/`distance_m`.
- Fixing or re-opening issue #5's endpoint/pagination/field-mapping work —
  already shipped; this issue only concerns the distance-unit assumption
  within that same `normalize()` method.

## Acceptance criteria

1. For an account whose Peloton unit is miles, `normalize()` produces
   `distance_m` within 1% of the true metric distance (verified via the
   BO's linked Peloton↔Strava pairs from issue #37 agreeing within 1% after
   the fix — this is the real-data verification check, run by the BO since
   CI has no live-account access).
2. For an account whose Peloton unit is km, `normalize()` continues to
   produce correct `distance_m` (no regression for km accounts — the BO's
   own account happens to be miles, but the fix must not break a
   hypothetical/tested km account).
3. If the unit cannot be determined (missing, unrecognized, or the
   relevant field absent from the response), `normalize()` logs this
   condition and returns `distance_m = None` — it never guesses or falls
   back to a default unit.
4. A one-off script (or equivalent mechanism, following the
   `renormalize_strava_unofficial.py` pattern) re-derives `distance_m` for
   every already-stored Peloton row from its stored `raw_activities`
   payload, without re-fetching from Peloton and without modifying the raw
   payload. Running it is safe to repeat (idempotent).
5. After running the one-off correction against the BO's real database,
   every linked Peloton↔Strava pair from issue #37 agrees on distance
   within 1% (the BO's own verification check against real data).
6. `peloton_csv`'s existing km handling is unchanged and its tests
   continue to pass.
7. New/updated tests in `tests/test_peloton_connector.py` cover: a
   mi-unit account (correct metre conversion), a km-unit account (correct
   metre conversion, no regression), and a missing/unknown-unit account
   (`distance_m is None`, a warning logged).
8. The live-verified finding (account-level unit varies and must be read
   from the API, not assumed) is documented in
   `docs/trainiq/verification/` and/or `BACKLOG.md`.

## Open questions

None that block the Architect. The BO has already supplied, as live
evidence: the exact raw/stored/Strava-truth numbers for one ride, five
further corroborating Peloton↔Strava pairs from issue #37, and the specific
code location and constant (`DISTANCE_KM_TO_M_MULTIPLIER`,
`connectors/peloton.py:142`) responsible for the bug. What is *not* yet
established — and is explicitly left to the Architect, since it requires
inspecting what Peloton's real API responses actually contain (`GET
/api/me`, the workout/summary record, or elsewhere) — is exactly which
field carries the account's distance unit, and whether it is reliably
present on every account. The issue's own suggestion (`GET /api/me` →
`distance_unit`) is a starting point, not a confirmed field; per this
project's evidence-based principle, the Architect/Developer should
live-verify it (or find the real equivalent) rather than assume it's named
exactly that, and the "unknown unit → NULL + log" acceptance criterion
above exists specifically to make that an explicit, tested outcome rather
than a silent assumption either way.
