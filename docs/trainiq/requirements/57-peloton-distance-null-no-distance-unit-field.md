# Requirements: Peloton distances all NULL after #45 — `GET /api/me` has no `distance_unit` field (BL-010 disproved)

Issue: #57

## Summary

Issue #45's fix (PR #54) resolved the "distance always assumed km"
regression by reading an account-level distance unit off `GET /api/me`
(`ACCOUNT_DISTANCE_UNIT_FIELD = "distance_unit"`, `connectors/peloton.py:180`),
explicitly flagged at the time as **unconfirmed** (`BL-010`) pending a live
diagnostic against the BO's real account. That diagnostic has now run. The
BO's live, read-only capture of their own real `GET /api/me` response shows
**no distance-unit field of any kind** — only `height_unit: "metric"`,
`weight_unit: "metric"`, and `locale: "en-US"`, none of which track the
workout list's own `distance` unit (the account's display settings are
metric, but a 2026-10-07 ride's raw `distance` value is `13.1816`, which is
miles, not km — confirmed against the same ride's independently-correct
Strava distance). The result: every one of the BO's 136 Peloton workouts
has `distance_m = NULL` (`diagnostic.log`: `peloton: unresolved distance
unit None ... — distance_m stored as NULL, not guessed`, ×136) — the
fail-safe from #45 is working exactly as designed, but on every single row,
which is a full data-loss regression, not an edge case.

This closes `BL-010` as **disproved** (the field does not exist), not
merely "still open/unconfirmed." It also surfaces a self-describing
replacement source, already captured live: `GET
/api/workout/{id}/performance_graph` → `summaries[slug="distance"]` returns
both a `value` and its own `display_unit` (e.g. `21.21367`, `"km"`) for the
same ride — a per-record, self-describing distance that doesn't depend on
any account-level setting at all. Issue #47 (currently `stage:dev`) already
needs to call this same per-workout `performance_graph` endpoint for
avg/max HR and max power, so there is a real opportunity (not a requirement
imposed here) to share that one fetch rather than add a second call to the
same endpoint — consistent with how #46 and #47 already share their own
per-workout fetch mechanism.

## Scope

- `PelotonConnector`'s live-sync path (`download()` / `normalize()` /
  `_resolve_account_distance_unit()`): stop reading
  `ACCOUNT_DISTANCE_UNIT_FIELD` (`"distance_unit"`) off `GET /api/me` — that
  field does not exist on the real account (live-verified, this issue).
- Replace it with a source that carries its own, per-record unit. Two
  options are on the table; which one ships is the Architect's call, not
  specified here, per the BA/Architect split of *what* vs. *how*:
  - **Preferred:** the `performance_graph` distance summary (`value` +
    `display_unit`) captured live in this issue's evidence. Since issue #47
    already needs a per-workout fetch to this same endpoint, the Architect
    should evaluate sharing that one fetch (same pattern already
    established between #46 and #47) rather than adding a second,
    independent call to `performance_graph`. #47 is currently at
    `stage:dev` — the Architect should read its current implementation
    state before designing this fix, the same precedent #47's own
    architecture doc set for reading #46's state first.
  - **Acceptable alternative:** treat the workout-list `distance` field as
    always being in miles, documented as a verified Peloton API invariant
    — but only if the Architect's design doc justifies it against the
    evidence in this issue (the single captured ride is consistent with
    "always miles," but that is one data point, not a proof across
    accounts/units).
- A missing, absent, or unrecognized distance source on a given workout
  still yields `distance_m = NULL` plus a logged warning — the #45 fail-safe
  (never guess a unit) is correct behavior and must be preserved exactly,
  regardless of which replacement source is chosen.
- Re-apply the correction to the 136 existing rows currently stored as
  `NULL` (re-running `scripts/renormalize_peloton_distance.py`, extending
  it, or an equivalent mechanism — Architect/Developer's call depending on
  which source is chosen), such that a later normal sync does not undo the
  correction again.
- Update `BACKLOG.md`'s `BL-010` entry and
  `docs/trainiq/verification/peloton-2026-09-28.md`'s existing addendum to
  record this issue's finding (field does not exist; disproved, not just
  unconfirmed) — same convention used to close `BL-008`.
- Fixture tests built from the real response shapes captured in this
  issue's evidence (the `/api/me` body showing the field is absent, and the
  `performance_graph` distance summary if that's the chosen source) — not
  guessed shapes.

### Out of scope

- `peloton_csv` import (`trainiq/csv_import/peloton_csv.py`) — unaffected,
  already confirmed out of scope by #45 and not touched by this issue.
- Any change to authentication, OAuth, or the manual-recovery path.
- The class/ride-metadata fetch from #46, or the HR/power fields from #47,
  beyond whatever fetch-sharing the Architect decides is warranted for
  distance specifically.
- Re-fetching from Peloton to re-verify anything beyond what this issue's
  evidence already captured live.
- Running the correction/backfill against the BO's live production
  database — an ops task for the BO, same as #45's own one-off script.

## Acceptance criteria

1. `PelotonConnector` no longer reads `ACCOUNT_DISTANCE_UNIT_FIELD` /
   `"distance_unit"` off `GET /api/me` for distance-unit resolution.
   `BACKLOG.md`'s `BL-010` is updated to record the field as confirmed
   absent (disproved), not left as "unconfirmed."
2. `normalize()` (or `download()`, depending on the chosen source) derives
   `distance_m` from a source that carries its own unit per workout, per
   the Scope section above — not from any account-level guess.
3. A missing/unresolvable per-workout unit still produces `distance_m =
   None` plus a logged warning — no behavior change here from #45's
   existing fail-safe.
4. A mechanism (script or extension of the existing one) re-derives
   `distance_m` for the 136 currently-NULL Peloton rows, safe to re-run
   (idempotent).
5. After the BO runs the correction mechanism against their real database,
   a normal `trainiq` sync produces no NULL distances for workouts that
   have a distance, and linked Peloton↔Strava pairs (#37) agree within 1%
   — this is the BO-run verification check, same as #45's AC5 (still
   unverified after #45 because of this regression; this issue is what
   finally lets it run clean).
6. New/updated tests in `tests/test_peloton_connector.py` cover: a workout
   with a resolvable per-record unit (correct `distance_m`), and a workout
   with a missing/unresolvable unit (`distance_m is None`, warning logged)
   — built from real captured response shapes, not guessed ones.
7. No regression to `peloton_csv`'s existing behavior/tests, and no
   regression for any account/workout scenario #45 already covered (e.g. a
   km-unit account, if the chosen fix still models accounts as having a
   unit at all).

## Open questions

None that block the Architect. The BO has supplied, as live evidence: the
exact `/api/me` response confirming the field's absence, the diagnostic
log proving all 136 rows are affected, the `performance_graph` distance
summary for one workout as a candidate replacement source, and five
corroborating Peloton↔Strava distance pairs from #45's own investigation
(now blocked on this fix to actually verify). What is explicitly left to
the Architect, per this project's evidence-based principle: choosing
between the `performance_graph` source and the "always miles" invariant,
and — if `performance_graph` is chosen — deciding how its fetch is shared
with #47's own need for the same endpoint, given #47 is already at
`stage:dev`. This is the same kind of fetch-sharing coordination already
established between #46 and #47; it does not need to come back to the BO.
