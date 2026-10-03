# Requirements: Peloton connector uses wrong endpoint and wrong field mapping (live-verified)

Issue: #5

## Summary

`PelotonConnector.download()` (`trainiq/connectors/peloton.py`) calls `GET
/api/me/workouts`, which a live-verified test against a real account
confirms does not exist (404, `Not found: '/me/workouts'`). The real,
working endpoint is `GET /api/user/{user_id}/workouts`, where `user_id`
must first be obtained from `GET /api/me`, and whose response is genuinely
paginated (129 workouts across 7 pages at `limit=20` on the tested
account) — `download()` today has no pagination handling at all, so even
once pointed at the right endpoint it would only ever see the newest page.
Separately, `PelotonConnector.normalize()` reads several fields
(`duration`, `avg_heart_rate`, `max_heart_rate`, `avg_power`) that do not
exist anywhere in the real API response shape; today this either silently
produces `None` for data that could have been derived (`duration`,
`avg_power`) or would need to keep producing `None` for data that
genuinely isn't available (`avg_heart_rate`/`max_heart_rate`/`max_power`).
Net effect: this connector, once its (separately tracked, still-broken)
authentication path is usable via the manual-recovery flow, would
currently fail to retrieve any real workout data at all, and would
silently drop derivable duration/power data even if pointed at the right
endpoint. This directly undermines the project's evidence-based-
recommendation principle for every real Peloton workout this connector
ever processes.

This issue does **not** cover the automated-login breakage itself
(tracked separately, still confirmed broken as of 2026-09-28) — only the
connector's assumptions about the endpoint and response shape for whenever
an auth path (automated or manual-recovery) is actually usable.

## Scope

- Change `download()` to use the real, live-verified endpoint sequence:
  obtain the account's `user_id` (via `GET /api/me`), then retrieve
  workouts from `GET /api/user/{user_id}/workouts`.
- Add pagination handling to `download()` so it retrieves the account's
  full workout history across all pages, not just the first page's
  results — using the pagination fields the real response includes
  (`limit`, `page`, `total`, `count`, `page_count`, `show_previous`,
  `show_next`, `next`).
- Fix `normalize()`'s field mapping per the live-verified reality:
  - `duration_s` must be derived as `end_time - start_time` (both fields
    are present and required in the real response) — the current
    `duration` field does not exist.
  - `avg_power` must be derived as `total_work / duration_s` (joules ÷
    seconds = watts) when both inputs are available — the current
    `avg_power` field does not exist directly, but this is a legitimate,
    non-fabricated derivation, not an approximation.
  - `max_power`, `avg_hr`, and `max_hr` must remain `None` — none of these
    have any real or legitimately-derivable equivalent in the live
    response (`effort_zones` provides only per-heart-rate-zone durations
    and a total effort-points score, not a plain average/max bpm value).
    This must be explicit, tested behavior, not an accidental byproduct of
    reading a key that happens not to exist.
  - `effort_zones` being `null` (a real, observed condition — occurs when
    a workout has no HR data) must be handled as an absent-data case, not
    treated as a parsing error.
  - `id`, `start_time`, `distance` (→ `distance_m`), and `calories`
    mappings are already correct and must not change.
- Verify the assumption that raw `distance` is in kilometers (consistent
  with the CSV importer's own `distance_m = distance_km * 1000`
  conversion) against the live-captured evidence, and document the
  outcome either way — confirmed-correct-as-is, or corrected — rather than
  leaving it an unverified assumption.
- Add regression tests to `tests/test_peloton_connector.py` using the
  exact real captured record shapes from this issue (both the record with
  `effort_zones: null` and the one with `effort_zones` populated), so this
  cannot silently regress. Existing tests in that file currently script
  fixture payloads shaped like the old, wrong assumption (`duration`,
  `avg_heart_rate`, `max_heart_rate` keys, and a single-page `GET .../me/
  workouts` call with no `user_id` step) — these need to be brought in
  line with the real shape as part of this fix.
- Update `BACKLOG.md` (BL-008) and `docs/verification/peloton-2026-09-28.md`
  to reflect this live-verified endpoint/pagination/field-mapping finding.
  Neither currently documents it (both currently cover only the
  separately-tracked auth-403 reconfirmation), even though this issue's
  own text describes the evidence as already captured there — that
  documentation gap should be closed as part of this fix so the record is
  accurate.

### Out of scope

- Fixing the automated-login breakage itself (403 on `POST /auth/login`)
  — separately tracked, unrelated to this issue.
- Any change to the manual-recovery bearer-token flow
  (`submit_manual_recovery()` / `request_manual_recovery()`).
- Mapping `effort_zones.heart_rate_zone_durations` or
  `effort_zones.total_effort_points` into new normalized output fields.
  This issue only asks that `avg_hr`/`max_hr` not be fabricated from that
  data — it does not ask for that data to be surfaced under new field
  names. Flagged below as a possible future-epic item.
- Canonical discipline taxonomy mapping (Epic 6's job, per the module's
  existing docstring) — this issue does not touch `discipline_raw`
  handling.
- GraphQL-only Peloton features — already out of scope per the module
  docstring (Feature 3.3 / R-PELOTON-05) and unrelated to this fix.

## Acceptance criteria

1. `download()` no longer requests `GET /api/me/workouts`. It first
   obtains the account's `user_id`, then retrieves workouts from `GET
   /api/user/{user_id}/workouts`.
2. `download()` retrieves the account's complete workout history across
   all available pages, not only the first page's results, when the
   response indicates more pages remain.
3. Given a raw record with `start_time = 1790014244` and `end_time =
   1790014543`, `normalize()` returns `duration_s == 299`.
4. Given a raw record with `total_work = 23646.98` and a derived
   `duration_s = 299`, `normalize()` returns `avg_power` equal to
   `total_work / duration_s` (≈ `79.1`), not `None` and not read from a
   nonexistent `avg_power` field.
5. Given a raw record where `effort_zones` is `null`, `normalize()` does
   not raise an exception, and `avg_power` is still computed from
   `total_work`/`duration_s` (i.e., `effort_zones` being absent has no
   bearing on the `avg_power` derivation).
6. `normalize()` always returns `max_power is None` — no direct or
   derived value for it exists in the real API response.
7. `normalize()` always returns `avg_hr is None` and `max_hr is None` —
   no plain average/max heart-rate value exists anywhere in the real API
   response, whether or not `effort_zones` is populated.
8. `normalize()`'s `id`, `start_time`, `distance_m`, and `calories`
   mappings are unchanged by this fix — verified by existing/updated
   tests continuing to pass for these fields.
9. The distance-unit assumption (raw `distance` in kilometers) is
   explicitly verified against the live-captured evidence and the outcome
   is documented (in the handoff comment, a code comment, or both) —
   either confirmed correct or corrected.
10. `tests/test_peloton_connector.py` contains regression tests built from
    the exact real record shapes captured in this issue (the
    `effort_zones: null` record and the `effort_zones`-populated record),
    and the full existing test suite for this connector passes.
11. `BACKLOG.md` (BL-008) and `docs/verification/peloton-2026-09-28.md`
    are updated to reflect this endpoint/pagination/field-mapping finding.

## Open questions

None that block the Architect/Developer. The reporter (the BO, live-tested
against a real account today) provided exact real record shapes, the
correct endpoint sequence, the pagination shape, and an explicit,
reasoned field-mapping table — enough to implement and test this fix
without further clarification.

One non-blocking note for a future epic, not requested by this issue and
not acted on here: `effort_zones.heart_rate_zone_durations` (seconds per
heart-rate zone) and `effort_zones.total_effort_points` are real signal
this connector currently has no field for. Whether to add dedicated
normalized fields for per-zone HR duration data is a product decision for
a later issue, not this one.
