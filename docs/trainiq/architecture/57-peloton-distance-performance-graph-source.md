# Architecture: Peloton Distance from `performance_graph` (replaces the disproved `/api/me` account-unit source)

**Issue:** #57
**Requirements:** [`docs/trainiq/requirements/57-peloton-distance-null-no-distance-unit-field.md`](../requirements/57-peloton-distance-null-no-distance-unit-field.md)
**Related:** #45 / PR (the fix this replaces — `ACCOUNT_DISTANCE_UNIT_FIELD`/`_resolve_account_distance_unit()`, now disproved), #46 (merged — `fetch_class_details()`/`ride_details_cache`/checkpoint-gated per-workout fetch, the pattern this design reuses), #47 (`stage:dev`, **no PR/branch exists yet** — its architecture doc at `docs/trainiq/architecture/47-peloton-heart-rate-capture.md` already designs a `fetch_workout_performance()`/`PERFORMANCE_ENDPOINT_TEMPLATE`/`_parse_performance_response()` against this exact same endpoint; this design builds that shared infrastructure now, since #57 is `priority:high` and #47 is `priority:medium` with nothing implemented yet — see "Why this builds #47's planned infra now" below), #37 (dedup pairs, used for AC5's BO-run verification)

## Approach

### The chosen source: `performance_graph`'s self-describing distance summary, not "always miles"

Issue #57's own live evidence already captures the replacement signal directly, no further verification needed to design against it: `GET /api/workout/{id}/performance_graph` → `summaries` is a list of objects; the one with `slug == "distance"` carries `value` (`21.21367`) and its own `display_unit` (`"km"`) for that exact workout — self-contained, per-record, independent of any account-level setting. This is chosen over the requirements doc's "acceptable alternative" (treat workout-list `distance` as always miles) because:

- The "always miles" alternative rests on **one data point** (the requirements doc's own words) and would repeat the exact failure mode this issue exists to fix: a plausible-looking account-wide assumption that was never independently verified per record, exactly how `ACCOUNT_DISTANCE_UNIT_FIELD` and, before it, "distance is always km" (#5) both went wrong. It also degrades silently — there is no signal to warn against if "always miles" is ever wrong for one workout.
- `performance_graph`'s summary is self-describing per workout. If it's ever missing or malformed for one specific record, the existing never-guess fallback (log + `NULL`) still applies to that one record only — the failure mode is narrower and detectable, not a silent account-wide wrong conversion.

### Why this builds #47's planned infra now, not after #47

#47's own architecture doc (written for a not-yet-started issue) already designs `fetch_workout_performance()`, `PERFORMANCE_ENDPOINT_TEMPLATE`, and `_parse_performance_response()` against this identical endpoint, for avg/max HR and max power. As of this doc, **#47 has no PR and no branch** — it is `stage:dev` with zero implementation. Since #57 is `priority:high` (data-loss regression) and #47 is `priority:medium`, the Developer pipeline's own priority ordering (`docs/trainiq/roles/technical-architect.md`) means #57 is worked first. Rather than have #57 build a narrow, distance-only fetch and have #47 later duplicate the same endpoint call, this design **builds the shared fetch/parse infrastructure now**, using the exact function/constant names #47's doc already committed to, so #47's future implementation extends this code instead of replacing or duplicating it — the same one-fetch-two-concerns precedent #46 and #47 already established for the ride-detail fetch.

**One deliberate deviation from #47's draft, stated explicitly:** #47's doc names its status column `hr_fetch_status`. Since distance (this issue) and HR/power (#47) are extracted from the **same single HTTP call** (`fetch_workout_performance()` either succeeds or fails as one unit — there is no scenario where the fetch "succeeds for HR but fails for distance"), a per-concern status column would always move in lockstep with this one. This design introduces one shared column, `performance_fetch_status`, instead. **#47, when implemented, must read/write this column — it must not introduce a second `hr_fetch_status` column.**

### What changes in `download()`

The call is **unconditional per workout** (not gated on whether the workout has a raw `distance` value), gated only on the existing checkpoint-skip condition `is_new_since_checkpoint` (#46's pattern, already computed per workout in the loop for class-detail lookups — reused, not recomputed). This is deliberate, not scope creep: gating on "has raw distance" would require a `not_applicable` sentinel in `performance_fetch_status` for non-distance workouts, which would then **block #47's own need to fetch HR data for those same workouts** (a strength class has no distance but can have real HR data) — two different trigger conditions sharing one status column would conflict. Gating on checkpoint alone avoids that conflict entirely and is exactly the trigger #47 already needs, so no redesign when #47 lands. The cost is a modest number of extra per-workout HTTP calls for non-distance workout types (meditation, strength) on live syncs going forward — acceptable for a single-account, ~136-workout history, and already the direction #47's own doc was heading.

```python
# peloton.py — download(), extending the existing per-workout loop that
# already computes is_new_since_checkpoint for #46's class-detail lookup.
if is_new_since_checkpoint:
    performance = self.fetch_workout_performance(workout.get("id"))
    if performance is None:
        workout["_performance_fetch_status"] = PERFORMANCE_FETCH_STATUS_FAILED
    else:
        workout["_performance_fetch_status"] = PERFORMANCE_FETCH_STATUS_OK
        workout["_distance_value"] = performance.get("distance_value")
        workout["_distance_unit"] = _resolve_distance_unit_token(performance.get("distance_unit_raw"))
        # #47 will add here, reading the SAME already-fetched `performance`
        # dict (extended inside _parse_performance_response(), not via a
        # second call): workout["_avg_hr"] = performance.get("avg_hr"), etc.
# else: not new since checkpoint (already synced before this fix, or
# before #46 at all) — every _distance_*/_performance_fetch_status key
# stays ABSENT. This is the "not attempted this pass" case the COALESCE
# upsert (below) and normalize()'s silent-no-warning branch both depend
# on — it is why the 136 already-NULL rows need the backfill tool
# (below), not a plain resync.
```

### What changes in `normalize()`

```python
raw_distance = raw.get("distance")
if raw_distance is None:
    distance_m = None  # genuinely no distance on this workout (meditation,
                        # strength) — not a fetch/unit problem, never warn.
elif raw.get("_performance_fetch_status") != PERFORMANCE_FETCH_STATUS_OK:
    # Either not attempted this pass (absent key — skip-gated, silent,
    # same convention as #46's absent _class_*), or the fetch itself
    # failed (fetch_workout_performance() already logged that failure
    # once, at fetch time — do not log it again here).
    distance_m = None
else:
    distance_value = raw.get("_distance_value")
    unit_token = raw.get("_distance_unit")  # already alias-resolved by download()
    multiplier = _DISTANCE_UNIT_MULTIPLIERS.get(unit_token)
    if distance_value is not None and multiplier is not None:
        distance_m = distance_value * multiplier
    else:
        diagnostic_logger().warning(
            f"{PROVIDER}: performance fetch succeeded but yielded no usable "
            f"distance summary (external_id={raw.get('id')!r}) — distance_m "
            f"stored as NULL, not guessed"
        )
        distance_m = None
```

This preserves `normalize()` as a pure reader of already-resolved values — same invariant #45's original design stated and #46/#47 both followed: `normalize()` never itself does alias/string parsing or network I/O; `download()` resolves, `normalize()` reads.

### Removed: the disproved `/api/me` account-unit source

`ACCOUNT_DISTANCE_UNIT_FIELD`, `_resolve_account_distance_unit()`, and the extra `me.get(...)` read in `download()` are **removed**, not merely unused — issue #57's live evidence confirms the field does not exist on the real account (`height_unit`/`weight_unit`/`locale` are present, no distance-unit field of any kind). This closes `BL-010` as **disproved**, not "still unconfirmed" (see BACKLOG update below). `_DISTANCE_UNIT_ALIASES` and `_DISTANCE_UNIT_MULTIPLIERS` are **kept** — they're generic unit tables, still needed to interpret `performance_graph`'s `display_unit` string; only the function that fed them a value changes, replaced by `_resolve_distance_unit_token()` (same alias logic, now called on a bare per-record string instead of an account-level dict field).

### `distance_m`'s upsert semantics: unconditional-overwrite → COALESCE (a real, deliberate behavior change)

Once distance resolution is gated on `is_new_since_checkpoint`, `distance_m` gains a genuine "not attempted this pass" case for the first time (previously the whole account-unit resolution ran unconditionally every sync, so this case never existed). Without COALESCE, a resync that skips an already-synced workout would write `distance_m = NULL` through the unconditional `UPDATE`, erasing a previously-correct value — the exact bug class #47's doc already identified and fixed for `avg_hr`/`max_hr`/`max_power` for the same reason. This design makes the same change to `distance_m`.

**Tradeoff, stated explicitly because it's a bigger surface than #47's case:** unlike `avg_hr`/`max_hr`/`max_power` (new columns, Peloton-only data so far), `distance_m` already has real history from **Strava and Eufy**, not just Peloton. For those providers, `normalize()` always computes `distance_m` directly with no skip-gating, so `COALESCE(new_value, old_value)` overwrites identically to today whenever the new value is non-`None` — no regression for the normal case. The accepted edge-case risk: if a non-Peloton provider's source data ever legitimately transitions a specific activity's distance from a real value to `None` on a later sync (e.g., a GPS track is deleted upstream), COALESCE would now preserve the stale old value instead of clearing it, where today's unconditional overwrite would correctly null it out. This is judged acceptable (same class of tradeoff #47 already accepted) because it's a narrow, unlikely scenario, and the alternative — silently wiping Peloton distance data on every multi-day resync — is the regression this entire issue is about.

### The 136 existing NULL rows: a new network-calling backfill tool, not the old renormalize script

The existing `scripts/renormalize_peloton_distance.py` (issue #45) is now **obsolete and must be deleted**, not kept: its entire mechanism — inject an operator-confirmed `--unit` into a stored raw payload and re-run `normalize()` with zero network I/O — only worked because the old design treated the unit as one global, operator-supplied fact. That is precisely the "trust a global assumption instead of verifying per record" pattern this issue exists to retire; keeping the script around (even unused) would leave a misleading tool that still claims distance can be corrected without live data. `renormalize_provider()`'s `raw_transform` parameter was added *solely* to support this one script (confirmed: the only other call site, `scripts/renormalize_strava_unofficial.py`, never passes it) — it becomes dead code once the script is deleted and should be removed from `renormalize_provider()`'s signature along with its two tests in `tests/test_renormalize.py` (`test_renormalize_applies_raw_transform_before_normalize`, `test_renormalize_omitted_raw_transform_is_byte_identical_to_today`).

The replacement needs a real network call per row (the per-record unit can only come from `performance_graph`, not from anything already stored in `raw_activities`), so it follows **#46's `backfill_peloton_class_metadata.py` pattern** (resumable, rate-limited via `retry_with_backoff()`, commits per row, narrow UPDATE-only write), not #45's/#36's `renormalize_provider()` pattern (zero-network, bulk re-derivation).

**Decision: rename and extend `scripts/backfill_peloton_class_metadata.py` → `scripts/backfill_peloton_workout_details.py` now**, combining class-metadata backfill (#46, unchanged behavior) with distance backfill (this issue), rather than writing a second, separate backfill script. This is the same rename #47's own doc already anticipated it would need to make later for the same reason (one resumable per-row mechanism, multiple independently-missing concerns per row) — since #57 lands first, #57 does the rename. **#47, when implemented, must extend this already-renamed script further for HR/power — its own doc's reference to the original filename will be stale by then.**

## Affected components/files

- `trainiq/connectors/peloton.py`:
  - **Removed:** `ACCOUNT_DISTANCE_UNIT_FIELD`, `_resolve_account_distance_unit()`, the `me`-based unit read in `download()`.
  - **Added:** `PERFORMANCE_ENDPOINT_TEMPLATE`, `PERFORMANCE_ENDPOINT_PARAMS`, `SUMMARIES_FIELD`/`SUMMARY_SLUG_FIELD`/`SUMMARY_VALUE_FIELD`/`SUMMARY_DISPLAY_UNIT_FIELD`/`DISTANCE_SUMMARY_SLUG` constants, `PERFORMANCE_FETCH_STATUS_OK`/`PERFORMANCE_FETCH_STATUS_FAILED`, `_resolve_distance_unit_token()`, `_parse_performance_response()`, `fetch_workout_performance()`, `apply_distance_update()` (narrow UPDATE-only, same shape as `apply_class_metadata_update()`).
  - **Kept, reused:** `_DISTANCE_UNIT_ALIASES`, `_DISTANCE_UNIT_MULTIPLIERS`.
  - **Changed:** `download()`'s per-workout loop (new unconditional-on-checkpoint block, see above); `normalize()`'s `distance_m` derivation.
- `trainiq/storage/schema.py` — migration **v7** (current main is v6, from #46/PR #55; coordinate with PR #56/#48, whose still-open description claims "schema migration 6" against a now-stale base — verify the actual next-free number at implementation time, same caveat #47's own doc already carries): one nullable column, `performance_fetch_status TEXT`, on `normalized_activities`.
- `trainiq/sync/engine.py` — `upsert_normalized_activity()`: `distance_m` moves from unconditional-overwrite to `COALESCE(?, distance_m)` in the `UPDATE` branch (both `INSERT`/`UPDATE` column lists gain `performance_fetch_status`, COALESCE on `UPDATE`, same treatment as #46's 6 columns).
- `trainiq/normalization/renormalize.py` — remove the `raw_transform` parameter from `renormalize_provider()` (dead after the script deletion below; its one caller is gone).
- `trainiq/normalization/engine.py` — `_build_activity_record()`: pass through `performance_fetch_status`, same pattern as #46's 6 fields.
- **Deleted:** `scripts/renormalize_peloton_distance.py` (obsolete mechanism, see above).
- **Renamed + extended:** `scripts/backfill_peloton_class_metadata.py` → `scripts/backfill_peloton_workout_details.py` (adds distance backfill alongside the existing, unchanged class-metadata backfill).
- **Renamed + extended:** `tests/test_backfill_peloton_class_metadata.py` → `tests/test_backfill_peloton_workout_details.py`.
- `tests/test_peloton_connector.py` — new/updated cases (see Test strategy notes); several **existing** tests assert `_distance_unit` is attached unconditionally to every workout (e.g. `test_download_fetches_user_id_then_paginated_workouts`, `test_download_missing_account_unit_field_attaches_none_not_omitted`) — these must be rewritten, since `_distance_unit`/`_distance_value`/`_performance_fetch_status` are now only attached when `is_new_since_checkpoint`, not unconditionally.
- `tests/test_renormalize.py` — remove the two `raw_transform`-specific tests named above.
- `docs/trainiq/verification/peloton-2026-09-28.md` — dated addendum recording this issue's finding (field confirmed absent; disproved).
- `projects/trainiq/BACKLOG.md` — `BL-010` updated from "Open" to **"Closed as disproved"** (the field does not exist — a definitive negative, not an unresolved unknown; distinct from BL-008's "closed as verified," since the finding here is "the thing doesn't exist" rather than "the thing was confirmed as suggested"). No new BACKLOG item needed for the `performance_graph` distance summary itself: its shape is live-evidenced in issue #57's own body, not a flagged guess. If a new item is wanted for `PERFORMANCE_ENDPOINT_PARAMS` (this design uses `{}` — no query params — because issue #57's own capture got a working `summaries` list without documenting any special params; #47's Task 1 may still need to add a param like `every_n` for HR curve granularity, which is additive, not evidence this call is wrong), that's optional and low-risk either way since an unexpected response shape degrades safely to `NULL` + a logged warning per workout, never a wrong guess.
- **Not touched:** `peloton_csv` import (confirmed out of scope by #45, unaffected here), authentication/OAuth/manual-recovery paths, `_is_class_workout()`/class-metadata logic (unchanged, just sharing the same renamed script file).

## Interfaces/contracts

```python
# trainiq/connectors/peloton.py

# CONFIRMED (issue #57's own live evidence, BO's real account, 2026-10-09):
# GET this path returns a body with a top-level "summaries" list; the
# entry with slug == "distance" carries "value" (float) and its own
# "display_unit" (e.g. "km") for that workout — self-describing,
# independent of any account-level setting.
PERFORMANCE_ENDPOINT_TEMPLATE = "/api/workout/{workout_id}/performance_graph"
# No query params documented in issue #57's capture; this is what that
# capture actually used, not a guess. #47's own Task 1 (avg/max HR, max
# power — NOT yet live-verified anywhere in this repo) may find it needs
# to add a param here (e.g. every_n, for curve granularity) — additive,
# doesn't invalidate this dict's current contents.
PERFORMANCE_ENDPOINT_PARAMS: dict[str, Any] = {}

SUMMARIES_FIELD = "summaries"
SUMMARY_SLUG_FIELD = "slug"
SUMMARY_VALUE_FIELD = "value"
SUMMARY_DISPLAY_UNIT_FIELD = "display_unit"
DISTANCE_SUMMARY_SLUG = "distance"

# Attempted-this-pass outcome, shared by distance (this issue) and HR/power
# (#47) since both come from the one fetch_workout_performance() call.
# NULL (column default): never attempted this pass — COALESCE preserves
# prior state, same convention as #46's absent _class_* keys.
PERFORMANCE_FETCH_STATUS_OK = "ok"          # attempted, response parsed
                                             # (individual fields may still
                                             # be None/absent — a sparse but
                                             # well-formed response)
PERFORMANCE_FETCH_STATUS_FAILED = "failed"  # attempted, fetch itself failed


def _resolve_distance_unit_token(raw_unit: Any) -> str | None:
    """Maps a performance_graph display_unit value through
    _DISTANCE_UNIT_ALIASES to a canonical "mi"/"km" token. Returns None
    for a missing/non-string/unrecognized value — never guesses, never
    raises. Replaces _resolve_account_distance_unit() (issue #45/BL-010,
    removed — that function read an account-level /api/me field this
    issue's live evidence confirms does not exist); same alias table,
    now applied to a per-record value instead."""


def _parse_performance_response(body: dict[str, Any]) -> dict[str, Any]:
    """Issue #57: extracts the distance summary. Returns
    {"distance_value": float | None, "distance_unit_raw": str | None} —
    both None if `body` has no `summaries` list, or no entry with
    slug == DISTANCE_SUMMARY_SLUG. Never raises.

    Issue #47 (not yet implemented) extends this SAME function's
    returned dict with "avg_hr"/"max_hr"/"max_power" (and "avg_cadence"
    if free) read from the same already-parsed `body` — same endpoint,
    same fetch, one response. Do not add a second call to
    PERFORMANCE_ENDPOINT_TEMPLATE for that; extend this dict instead."""


def fetch_workout_performance(self, workout_id: str) -> dict[str, Any] | None:
    """Public (not `_`-prefixed) — also called directly by
    scripts/backfill_peloton_workout_details.py, so the HTTP/retry/parsing
    logic exists in exactly one place. Wraps its own call in
    retry_with_backoff() and degrades ANY non-auth failure (TransientError
    after retries exhausted, PelotonHTTPError, including a 404) to None,
    logged once — AC3 requires a per-workout distance-resolution failure
    to never abort the whole sync. This deliberately differs from
    fetch_class_details(), which lets TransientError propagate to the
    Synchronization Engine's own outer retry — distance/HR data is
    supplementary enrichment, a class lookup failure is not. AuthenticationError
    still propagates unchanged (connector-wide concern, ADR-009)."""


def apply_distance_update(conn: sqlite3.Connection, external_id: str, fields: dict[str, Any]) -> None:
    """Updates ONLY distance_m and performance_fetch_status on an existing
    normalized_activities row. Same narrow single-writer exception as
    apply_class_metadata_update(). Caller commits; this function does not.
    Used by scripts/backfill_peloton_workout_details.py."""
```

```python
# trainiq/storage/schema.py
# Migration v7 (verify next-free number at implementation time — see
# "Affected components/files"):
#   ALTER TABLE normalized_activities ADD COLUMN performance_fetch_status TEXT;
```

```python
# trainiq/sync/engine.py — upsert_normalized_activity(), extending #46's
# column lists:
#   INSERT: ... distance_m, ..., performance_fetch_status) VALUES (...)
#   UPDATE: distance_m = COALESCE(?, distance_m), ...,
#           performance_fetch_status = COALESCE(?, performance_fetch_status)
```

```python
# scripts/backfill_peloton_workout_details.py — renamed + extended from
# backfill_peloton_class_metadata.py. Same _select_candidates()/
# run_backfill()/main() shape; selection query gains an OR'd clause:
#   WHERE ra.provider = 'peloton' AND (
#     na.class_type IS NULL
#     OR (:retry_failed AND na.class_type = 'lookup_failed')
#     OR na.performance_fetch_status IS NULL
#     OR (:retry_failed AND na.performance_fetch_status = 'failed')
#   )
# _process_row() gains an independent second branch (both can run for
# the same row in the same pass, same as #47's doc already planned):
# if this row's performance_fetch_status is still missing/retryable, call
# connector.fetch_workout_performance(external_id) directly (already
# retry-wrapped internally — the script does not wrap it a second time),
# derive distance_m via the same multiplier logic normalize() uses, and
# write via apply_distance_update(). Commit after every row regardless of
# which branch(es) ran — same resumability guarantee as #46.
```

## Task breakdown

1. Remove `ACCOUNT_DISTANCE_UNIT_FIELD` and `_resolve_account_distance_unit()` from `trainiq/connectors/peloton.py`; remove the `me`-based unit read in `download()`.
2. Add the new constants, `_resolve_distance_unit_token()`, `_parse_performance_response()`, `fetch_workout_performance()`, `apply_distance_update()` to `trainiq/connectors/peloton.py`.
3. Update `download()`'s per-workout loop to call `fetch_workout_performance()` when `is_new_since_checkpoint` (reusing the existing variable, not recomputing it) and attach `_distance_value`/`_distance_unit`/`_performance_fetch_status`.
4. Update `normalize()`'s `distance_m` derivation per the Approach section above.
5. Schema migration v7 (confirm actual next-free version number against main at implementation time): `performance_fetch_status TEXT`.
6. `trainiq/sync/engine.py`: move `distance_m` to COALESCE in the `UPDATE` branch; add `performance_fetch_status` to both INSERT and UPDATE (COALESCE).
7. `trainiq/normalization/engine.py`: pass through `performance_fetch_status`.
8. Delete `scripts/renormalize_peloton_distance.py`. Remove `raw_transform` from `renormalize_provider()` (`trainiq/normalization/renormalize.py`) and its two tests in `tests/test_renormalize.py`.
9. Rename `scripts/backfill_peloton_class_metadata.py` → `scripts/backfill_peloton_workout_details.py`; extend `_select_candidates()`/`_process_row()`/`run_backfill()` per the Interfaces section. Rename and extend its test file.
10. Update the existing `tests/test_peloton_connector.py` cases that currently assert `_distance_unit` is attached unconditionally to every workout (see Affected components/files) to reflect checkpoint-gated attachment; add new cases (see Test strategy notes).
11. Add the dated verification addendum to `docs/trainiq/verification/peloton-2026-09-28.md` and update `BL-010` in `projects/trainiq/BACKLOG.md` to "Closed as disproved."
12. Run the full existing test suite, in particular `tests/test_sync_engine.py`, `tests/test_renormalize.py`, `tests/test_architecture_invariants.py`, and the existing Peloton class-metadata tests (zero regressions expected).
13. Handoff. The BO then runs `scripts/backfill_peloton_workout_details.py` against the real database and checks AC5 (Peloton↔Strava pairs within 1%, #37) — BO-run verification, same as #45's original AC5, explicitly out of scope for this pipeline.

## Test strategy notes

All in fixtures/mocks — no live Peloton access from CI (Testing scope boundary).

`tests/test_peloton_connector.py`:
- `_parse_performance_response()`: a fixture body with a `summaries` list containing the issue's own captured distance entry (`value: 21.21367, display_unit: "km"`) → correctly extracted; a body with `summaries` present but no `slug == "distance"` entry → both fields `None`; a body with no `summaries` key at all → both fields `None`; malformed entries in `summaries` (not dicts) → skipped, not raised.
- `fetch_workout_performance()`: a successful fixture response → parsed dict returned; simulated 404/5xx/429-exhausted-retries → `None`, logged, no exception; simulated 401/403 → `AuthenticationError` propagates.
- `normalize()` cases (AC1–AC3, AC7): `_performance_fetch_status="ok"` with a recognized `_distance_unit`/`_distance_value` → `distance_m` within float tolerance of the issue's own Strava-confirmed figure (21213.7, within 1%); `_performance_fetch_status="ok"` with `_distance_unit` unrecognized/missing but a real `raw["distance"]` → `distance_m is None` + warning logged exactly once; `_performance_fetch_status="failed"` with a real `raw["distance"]` → `distance_m is None`, **no** warning from `normalize()` itself (already logged at fetch time); `_performance_fetch_status` key absent entirely with a real `raw["distance"]` → `distance_m is None`, no warning (not-attempted-this-pass case); `raw["distance"]` absent entirely (meditation) → `distance_m is None`, no warning, regardless of `_performance_fetch_status`.
- `download()` cases: a workout older than `since` → `fetch_workout_performance()` not called, every `_distance_*`/`_performance_fetch_status` key absent; `since=None` (first sync) → always attempted, for every workout regardless of discipline; `fetch_workout_performance()` returns `None` → `_performance_fetch_status == "failed"`, `download()` does not raise.
- Rewrite the existing tests flagged in "Affected components/files" that assert unconditional `_distance_unit` attachment.

`tests/test_renormalize.py`: remove the two `raw_transform` tests; confirm `renormalize_provider()`'s remaining behavior (strava_unofficial's call site) is otherwise unaffected.

`tests/test_sync_engine.py` (`upsert_normalized_activity()`): an `UPDATE` where the new record's `distance_m` is `None` and the stored row already has a real value → unchanged (COALESCE preservation, fails loudly if a future edit reverts to unconditional overwrite); an `UPDATE` supplying a real `distance_m` → overwritten; same pair of cases for `performance_fetch_status`.

`tests/test_backfill_peloton_workout_details.py` (renamed/extended from the class-metadata test file): existing class-metadata cases unchanged; new cases — a row missing only `performance_fetch_status`, a row missing both class metadata and performance status, resolved independently in one pass; `--retry-failed` re-attempts both `lookup_failed` and `performance_fetch_status='failed'` rows; a plain run skips both; resume after simulated interruption (already-processed rows not re-fetched).

Manual/BO-run verification (not CI): AC5 (Peloton↔Strava pairs within 1%, #37), run against the real database after the backfill script executes.

## Risks/tradeoffs

- **`distance_m`'s COALESCE change has a wider blast radius than #47's planned HR/power COALESCE change**, because `distance_m` already has real history across three providers (Peloton, Strava, Strava-unofficial, Eufy where applicable), not just new Peloton-only columns. Accepted tradeoff, stated explicitly (see Approach): for non-Peloton providers, `normalize()` never skip-gates `distance_m`, so COALESCE is behaviorally identical to today's overwrite in the normal case; the edge case it changes is a legitimate transition from a real distance to `None` on a later sync for a non-Peloton activity, which would now be masked rather than applied. Judged acceptable against the alternative (unconditional overwrite would re-null every already-correct Peloton distance on each multi-day resync, the exact regression this issue fixes).
- **The `performance_fetch_status` column is shared between this issue and #47's not-yet-built HR/power feature.** This is a deliberate single-column design (see "Why this builds #47's planned infra now"), not an oversight — if #47's eventual implementation instead introduces its own separate `hr_fetch_status`, that would create two columns that always carry the same ok/failed state for the same fetch, which is the duplication this design is specifically avoiding. Flagged here so a future reviewer catches a drift from this plan early.
- **Calling `fetch_workout_performance()` unconditionally per new-since-checkpoint workout** (not gated on whether the workout has a raw `distance`) issues extra HTTP calls for non-distance workout types (meditation, strength) that #57 alone doesn't need. Accepted because the alternative (gating on raw distance presence) would require a sentinel that conflicts with #47's broader need to fetch HR data for every discipline — see "What changes in `download()`" above. Low absolute cost for a single-account, ~136-workout history.
- **`PERFORMANCE_ENDPOINT_PARAMS = {}` is based on what issue #57's own evidence capture actually used**, not an independently re-verified request shape — if Peloton's real web client always sends additional parameters that happen to not matter for the `summaries` list specifically, this is still correct; if they do matter in some case this evidence didn't cover, the result is the existing safe fallback (`NULL` + logged warning for that workout), never a silently wrong distance.
- **Deleting `scripts/renormalize_peloton_distance.py` and `raw_transform` removes the only one-off correction path that required no network access.** The replacement (`backfill_peloton_workout_details.py`) requires a live, authenticated session — a real but accepted cost, since the old script's core assumption (one global, operator-supplied unit) is the pattern this issue is retiring, and the project's `renormalize_provider()` contract (zero network I/O) was never meant to carry a feature that fundamentally needs a live fetch.
- **Unit could still vary within an account over time** if Peloton's reporting ever changes — but since the unit is now resolved fresh per workout on every attempt (not cached account-wide), this is self-correcting for every future sync without any special handling, an improvement over #45's account-level design (which the original architecture doc already flagged as a known limitation).
