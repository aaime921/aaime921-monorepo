# Verification: Peloton distance_m from `performance_graph`, not `/api/me` (BL-010 disproved)

**Issue:** #57
**PR:** [#62](https://github.com/aaime921/aaime921-monorepo/pull/62) — `issue-57-peloton-distance-rebuild-on-47`
**Requirements:** [`docs/trainiq/requirements/57-peloton-distance-null-no-distance-unit-field.md`](../requirements/57-peloton-distance-null-no-distance-unit-field.md)
**Architecture:** [`docs/trainiq/architecture/57-peloton-distance-performance-graph-source.md`](../architecture/57-peloton-distance-performance-graph-source.md)
**PR head commit verified:** `04a626d42b677b1f8258e85a7ca4114475bc6ddf`

Note: PR #61 (the prior attempt at this issue, built on a stale base) is
superseded by PR #62 per the BO's decision recorded on the issue, and was
not re-verified here.

## Mergeability check

- `pull_request_read` reports `mergeable_state: clean` for PR #62 against
  `main` at the time of this review.
- PR base SHA `3d1c773` is on `origin/main`'s current history (includes
  PR #59/#47's merged `fetch_workout_performance()`/
  `_parse_performance_response()`/`performance_fetch_status` infrastructure
  and schema v10, as PR #62's description claims). No rebuild-on-stale-base
  issue this time.
- **Verdict: mergeable, clean.**

## Test suite

Checked out PR branch `issue-57-peloton-distance-rebuild-on-47` at
`04a626d`, installed `pip install -e ".[dev]"` into a clean venv, ran the
full suite from `projects/trainiq/`:

```
PYTHON_KEYRING_BACKEND=keyring.backends.null.Keyring python3 -m pytest -q
572 passed, 2 failed in 46.10s
```

The 2 failures (`test_real_csv_import_is_idempotent`,
`test_real_csv_full_regression` in `tests/test_peloton_csv_import.py`) are
a missing BO-local fixture file (`/home/claude/peloton_work/aimea75_workouts.csv`),
not present in this sandbox. Independently reproduced identically against
`origin/main` (same 2 failures, same error) — confirmed pre-existing and
unrelated to this PR's change, not just trusting the PR description's claim.

Also re-ran this issue's own new/changed suites plus the ones the
architecture doc's Task 12 calls out, in isolation:
`tests/test_peloton_connector.py`, `tests/test_renormalize.py`,
`tests/test_upsert_normalized_activity.py`,
`tests/test_backfill_peloton_workout_details.py`,
`tests/test_sync_engine.py`, `tests/test_architecture_invariants.py` →
**184 passed, 0 failed.**

## Acceptance criteria

| # | Criterion | Verdict | Evidence |
|---|---|---|---|
| 1 | `PelotonConnector` no longer reads `ACCOUNT_DISTANCE_UNIT_FIELD`/`distance_unit` off `GET /api/me`; `BACKLOG.md`'s BL-010 updated to "Closed as disproved," not "unconfirmed" | ✅ PASS | `ACCOUNT_DISTANCE_UNIT_FIELD` and `_resolve_account_distance_unit()` are deleted from `trainiq/connectors/peloton.py` (confirmed by diff and by `grep -rn` across `projects/trainiq/` — zero remaining code references, only historical comments/docstrings explaining the removal). `download()`'s `me = self._authenticated_get(...)` call no longer reads any unit field off it. `BACKLOG.md`'s BL-010 entry now reads **"Status: Closed as disproved (2026-10-09, issue #57)"** with the BO's live `/api/me` capture cited as evidence the field doesn't exist, not left as "Open"/"unconfirmed." |
| 2 | `normalize()` derives `distance_m` from a source carrying its own per-workout unit, not an account-level guess | ✅ PASS | `_parse_performance_response()` is extended with a `summaries[slug="distance"]` lookup (`value`/`display_unit`) against the *same* already-fetched `fetch_workout_performance()` body #47 uses for HR/power — no second network call (confirmed by reading `download()`'s loop: one `fetch_workout_performance()` call per workout, its result read for both HR/power and distance). `normalize()` resolves `distance_m` via `_resolve_distance_m(raw.get("_distance_value"), raw.get("_distance_unit"))`, both attached by `download()` from that one fetch. `test_download_resolves_distance_from_performance_graph_not_account_unit` confirms a `distance_unit` key on `/api/me` is now simply ignored, and the resolved value/unit come from `performance_graph` instead (`21.21367`/`"km"`, matching the issue's own captured evidence). `test_normalize_mi_unit_converts_distance_correctly`/`test_normalize_km_unit_converts_distance_correctly` both pass, within 1% of the Strava-confirmed figure (21213.7 m). |
| 3 | A missing/unresolvable unit still yields `distance_m = None` plus a logged warning — #45's never-guess fail-safe unchanged | ✅ PASS | `normalize()` distinguishes three no-value cases exactly as the architecture doc specifies: fetch not attempted (`_performance_fetch_status` absent) → `None`, silent; fetch attempted but failed → `None`, silent (already logged once at fetch time by `fetch_workout_performance()`); fetch succeeded but no usable unit/value → `None` **with** a logged warning. Verified by reading `normalize()`'s three-branch `if/elif/else` and by `test_normalize_ok_fetch_unresolved_unit_with_real_distance_yields_none_and_warns` (warns), `test_normalize_performance_fetch_failed_with_real_distance_yields_none_no_warning` (silent), `test_normalize_no_performance_fetch_status_at_all_with_real_distance_yields_none_no_warning` (silent) — all three pass, each checking the log stream content explicitly, not just the return value. A workout with no distance at all (meditation) never warns regardless of fetch status (`test_normalize_no_distance_at_all_yields_none_without_warning`, passes). |
| 4 | A mechanism re-derives `distance_m` for the 136 currently-NULL rows, safe to re-run (idempotent) | ✅ PASS | `scripts/backfill_peloton_workout_details.py`'s existing performance branch (`_process_performance()`) now also derives and writes `distance_m` via `apply_performance_update()`, from the same `fetch_workout_performance()` call already used for HR/power — no second script, no second fetch. Idempotency comes from the existing `_needs_attempt()` gate on `performance_fetch_status`: a plain re-run skips rows already `"ok"`, and `--retry-failed` re-attempts `"failed"` rows only — confirmed by reading `_process_performance()` and by `tests/test_backfill_peloton_workout_details.py`'s passing `test_performance_backfill_populates_hr_and_power`, `test_performance_fetch_returning_none_is_recorded_as_failed`, `test_performance_backfill_retry_failed_flag_reattempts`, `test_resume_after_interruption_does_not_reprocess_completed_performance_rows`. |
| 5 | BO-run: after the correction mechanism runs against the real DB, a normal sync produces no NULL distances and Peloton↔Strava pairs agree within 1% (#37) | ⏳ OUT OF SCOPE (BO-run) | Per the requirements doc and this project's "Live-account testing boundaries," this is explicitly a BO-run verification against the real database, not a CI-testable criterion — same treatment as #45's original AC5. Not run from this sandbox (no live Peloton/Strava credentials or network path here); the PR description correctly marks it ⏳ rather than claiming it done. |
| 6 | New/updated tests in `test_peloton_connector.py` cover a resolvable per-record unit and a missing/unresolvable one, from real captured shapes | ✅ PASS | `_REAL_PERFORMANCE_RESPONSE`'s `summaries` entry uses the issue's own live-captured values (`value: 21.21367`, `display_unit: "km"`). `test_parse_performance_response_extracts_distance_by_slug_not_position`, `test_parse_performance_response_no_distance_entry_yields_none_not_zero`, `test_parse_performance_response_no_summaries_key_at_all_yields_distance_none`, `test_parse_performance_response_malformed_summaries_entries_are_skipped_not_raised` all pass — distance extracted by `slug`, not list position, and degrades safely (never fabricates a value) for every malformed/missing shape. |
| 7 | No regression to `peloton_csv` and no regression to any account/workout scenario #45 already covered | ✅ PASS | `peloton_csv` import code and tests are untouched by this diff (confirmed by `git diff --stat` — no `csv_import/` files in the changeset); its 2 failures are the pre-existing, unrelated fixture-file issue above. Both the mi-unit and km-unit conversion cases #45 originally covered are re-verified under the new per-workout source (`test_normalize_mi_unit_converts_distance_correctly`, `test_normalize_km_unit_converts_distance_correctly`, both passing within 1% tolerance). Full-suite 572/2 pass/fail count matches pre-existing baseline exactly. |

## Additional checks performed (not just re-running the Developer's tests)

- Confirmed `distance_m`'s COALESCE change in `trainiq/sync/engine.py`'s
  `upsert_normalized_activity()` is wired correctly, not inverted: the
  `UPDATE` branch's SQL (`distance_m = COALESCE(?, distance_m)`) and its
  bound-parameter tuple are in the same order and position as every other
  COALESCE column in the same statement — counted the `UPDATE` SQL's
  placeholder list against the bound-params tuple by hand (31 total,
  matching the `INSERT` branch's column count) rather than trusting that
  the suite passing implies correct wiring. `tests/test_upsert_normalized_activity.py`'s
  `test_update_with_distance_m_none_preserves_existing_value` and
  `test_update_with_real_distance_m_overwrites` both pass and exercise this
  column specifically, not just the pre-existing HR/power columns.
- Confirmed `apply_performance_update()` (renamed from
  `apply_hr_performance_update()`) writes exactly five columns — `avg_hr`,
  `max_hr`, `max_power`, `distance_m`, `performance_fetch_status` — and
  nothing else, scoped to `provider = 'peloton'` only
  (`test_apply_performance_update_sets_only_the_five_columns`,
  `test_apply_performance_update_scoped_to_peloton_provider_only`, both
  pass).
- Confirmed `scripts/renormalize_peloton_distance.py` (issue #45) and its
  test file `tests/test_renormalize_peloton_distance_script.py` are
  actually deleted from disk, not just unreferenced (`ls scripts/` shows
  no such file). Confirmed `renormalize_provider()`'s `raw_transform`
  parameter is removed from its signature and its two associated tests are
  gone from `tests/test_renormalize.py`, and that function's one remaining
  real call site (`scripts/renormalize_strava_unofficial.py`, issue #36)
  is unaffected — `tests/test_renormalize.py` passes in full (6 tests).
- Confirmed, via `grep -rn` across `projects/trainiq/`, there are no
  remaining live-code references to `apply_hr_performance_update`,
  `ACCOUNT_DISTANCE_UNIT_FIELD`, `_resolve_account_distance_unit`, or
  `renormalize_peloton_distance` — every hit is a comment/docstring
  explaining the history, not a stale import or call.
- Confirmed `BACKLOG.md`'s BL-010 entry and
  `docs/trainiq/verification/peloton-2026-09-28.md`'s 2026-10-09 (issue
  #57) addendum are both present and consistent with each other and with
  the live evidence quoted in the issue body (`height_unit`/`weight_unit`/
  `locale` present, no distance-unit field; `performance_graph`'s
  `summaries[slug="distance"]` as the confirmed replacement source).
- Confirmed this PR does **not** reintroduce the schema/function-name
  collision that blocked PR #61: no new schema migration is added (`grep`
  for `CURRENT_SCHEMA_VERSION` / `ALTER TABLE` in the diff — none), and
  `_parse_performance_response()`/`fetch_workout_performance()`/
  `performance_fetch_status` are the single definitions already on `main`
  from PR #59, extended in place rather than duplicated.

## Result

**All 4 CI-verifiable acceptance criteria (1-4, 6-7) pass; AC5 is correctly
scoped as BO-run, not a CI gate, consistent with #45's original AC5
treatment.** PR #62 is approved and ready to merge (BO's call, per
pipeline protocol). PR #61 remains superseded and should be closed once
this merges.
