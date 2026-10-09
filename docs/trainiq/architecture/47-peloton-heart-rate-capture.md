# Architecture: Capture Peloton Heart-Rate Data (avg/max HR, HR Zones, Max Power)

**Issue:** #47
**Requirements:** [`docs/trainiq/requirements/47-peloton-heart-rate-capture.md`](../requirements/47-peloton-heart-rate-capture.md)
**Related:** #46 (shares the per-workout Peloton fetch/backfill mechanism — explicit BO directive; design at [`docs/trainiq/architecture/46-peloton-strava-class-metadata.md`](46-peloton-strava-class-metadata.md), implementation at `stage:dev`, **not yet on `main`** as of this doc — `trainiq/connectors/peloton.py` still has none of #46's `_is_class_workout()`/`fetch_class_details()`/`retry_with_backoff()`), #37 (dedup), Epic 7 (training load, blocked on this)

## Evidence gap, stated up front (per the Architect role's evidence-based principle)

Two different gaps, confirmed to different degrees — the requirements doc already separates them, restated here because they drive two different parts of this design:

1. **Confirmed, live-verified.** `docs/trainiq/verification/peloton-2026-09-28.md`'s second captured record shows `effort_zones` on the existing `GET /api/user/{user_id}/workouts` record (the same call #46 also reads) as:
   ```json
   "effort_zones": {
     "total_effort_points": 39.9,
     "heart_rate_zone_durations": {
       "heart_rate_z1_duration": 0, "heart_rate_z2_duration": 119,
       "heart_rate_z3_duration": 220, "heart_rate_z4_duration": 858, "heart_rate_z5_duration": 0
     }
   }
   ```
   and that file's first record shows `"effort_zones": null` on a workout with no HR data. Both the field names and the null case are real, live evidence, not an assumption — **no Task below is needed for this part**, it can be built directly.

2. **Not yet live-verified anywhere in this repo.** The claim that `avg_hr`, `max_hr`, `max_power` (and avg cadence) come from `GET /api/workout/{id}/performance_graph?every_n=...` is the issue author's own investigation note, never captured against a real response in this project — analogous to #46's own still-open Task 1 (ride/class detail shape). Per this Architect routine's sandbox constraint (no network egress to `onepeloton.com`, same limitation #45 and #46 both documented), **this cannot be resolved by this routine** — it is flagged as a mandatory, blocking **Task 1** below, to be run by whoever holds the BO's manual bearer token, same precedent as #46's Task 1 and the `debug_peloton_manual_bearer.py` diagnostics that resolved issue #5. Every constant this design proposes for that endpoint's shape is marked **UNCONFIRMED** with an explicit fallback; nothing else in this doc (schema, the skip/COALESCE logic, the backfill script's shape, the tests' structure) changes based on what Task 1 finds.

A third, minor point the requirements doc asks to confirm: whether the issue's own `hr_total_points` (cited in the 2026-10-07 ride evidence, not yet a committed verification file) is the same value as `total_effort_points` under a different key, or genuinely distinct. The issue's own numbers (`total_effort_points: 68.0` and `hr_total_points: 68.0` on the same workout) already suggest they're the same value under two keys, but that's the issue author's note, not this repo's committed evidence — folded into Task 1 below since it costs zero extra network calls (same list-endpoint record #47 already reads for the zone data). **Not blocking and not stored either way** — AC1 only requires `heart_rate_zone_durations`/`total_effort_points`; if Task 1 finds `hr_total_points` is a genuinely distinct metric, that's new, unscoped work for a future issue, not this one.

## Approach

### Schema: new columns on `normalized_activities`, building on #46's v5

Per #46's doc, schema v5 is 6 new nullable columns for class metadata. This issue adds a **v6** migration (the Developer must check `CURRENT_SCHEMA_VERSION` at implementation time and use the next free number — if #46 hasn't landed yet, coordinate rather than hardcode 6):

| Column | Type | Source | Upsert semantics |
|---|---|---|---|
| `hr_zone_1_s` … `hr_zone_5_s` | INTEGER | `effort_zones.heart_rate_zone_durations` on the **existing** list-endpoint record (confirmed, gap 1) | unconditional overwrite |
| `effort_points` | REAL | `effort_zones.total_effort_points`, same record | unconditional overwrite |
| `hr_fetch_status` | TEXT | set by `download()`/backfill whenever the performance-endpoint fetch is attempted (see below) | **COALESCE** |
| `avg_cadence_rpm` | INTEGER, **conditional** | performance endpoint, **only added to this migration if Task 1 confirms it's present at zero extra parsing/storage cost** — otherwise this column is omitted entirely, per the requirements doc's explicit "free or out of scope" framing | COALESCE, if added |

`avg_hr`, `max_hr`, `max_power` **already exist** (schema v1) — no new columns for them, only a behavior and upsert-semantics change (next section).

**Why `hr_zone_*_s`/`effort_points` are unconditional-overwrite but `avg_hr`/`max_hr`/`max_power`/`hr_fetch_status` are COALESCE:** the first group comes from the same record `download()` already walks on every sync, with no skip optimization applied to it (see "Zone data is never gated on the checkpoint" below) — always fully re-derivable, no "didn't attempt" case, same reasoning #46 gives for every pre-existing unconditional-overwrite column. The second group is behind the skip-if-already-synced-since-checkpoint optimization this issue reuses from #46 (see next section) — it genuinely has a "not attempted this pass" case, the exact situation #46's COALESCE columns exist to handle.

**This changes `avg_hr`/`max_hr`/`max_power`'s existing upsert semantics from unconditional-overwrite to COALESCE.** This is a real, explicit change to pre-existing behavior, not a new column — flagged here rather than left for a reviewer to discover unprompted (see "Risks/tradeoffs"). It's necessary and correct: without it, a later resync that skips an already-synced, already-enriched workout (per the skip optimization) would write these three columns as `None` through `normalize()`'s `raw.get("_avg_hr")`-style reads, and an unconditional-overwrite `UPDATE` would blow away previously-captured real HR data on every subsequent sync. COALESCE fixes this exactly the way it already fixes the same problem for #46's 6 columns and for Peloton's `renormalize_provider()` safety (same mechanism, see "Affected components/files" below for why `renormalize_provider()` stays safe).

### `hr_fetch_status`: why HR/power need a status column but class metadata didn't

#46's `class_type` column could double as its own status (`CLASS_TYPE_NOT_A_CLASS`/`CLASS_TYPE_LOOKUP_FAILED` sentinels) because it's a free-text column with no other legitimate value Peloton controls. `avg_hr`/`max_hr`/`max_power` are real nullable numeric facts — a sentinel can't live inside them, and "all three NULL" is already the correct, legitimate value for "no HR monitor paired," which must not be confused with "fetch not yet attempted" or "fetch failed, should retry." Hence a dedicated column:

```python
HR_FETCH_STATUS_OK = "ok"          # attempted, response parsed (fields may still be individually None)
HR_FETCH_STATUS_FAILED = "failed"  # attempted, fetch/parse failed — AC5, backfill's --retry-failed target
# NULL (column default): never attempted this pass — COALESCE preserves prior state, same as #46's
# absent _class_* keys.
```

### Connector-internal keys, same pattern as #46 (and #45's `_distance_unit` before it)

`download()` attaches these underscore-prefixed keys; `normalize()` only reads them, never calls the network:

```
_hr_zone_1_s ... _hr_zone_5_s, _effort_points        # never gated on checkpoint
_avg_hr, _max_hr, _max_power, _hr_fetch_status        # gated on checkpoint, same as #46's _class_*
_avg_cadence                                          # only if Task 1 confirms it's free
```

### Zone data is never gated on the checkpoint

Unlike the ride-detail fetch (#46) and the performance fetch (this issue), reading `effort_zones` costs **zero** extra network calls — it's already sitting on the record `download()` fetches regardless. Skipping it for already-synced workouts would only lose data for free, with no rate-limit benefit. So this part runs unconditionally, every pass, every workout:

```python
# peloton.py — CONFIRMED field names (docs/verification/peloton-2026-09-28.md)
EFFORT_ZONES_FIELD = "effort_zones"
HR_ZONE_DURATIONS_FIELD = "heart_rate_zone_durations"
TOTAL_EFFORT_POINTS_FIELD = "total_effort_points"
HR_ZONE_DURATION_FIELDS = {
    1: "heart_rate_z1_duration", 2: "heart_rate_z2_duration", 3: "heart_rate_z3_duration",
    4: "heart_rate_z4_duration", 5: "heart_rate_z5_duration",
}
```

### The performance fetch: UNCONFIRMED endpoint, skip-reused from #46, no cache (unlike ride details)

```python
# UNCONFIRMED — Task 1 target, same convention as #46's ride-detail constants.
# The issue author's own claim, never captured against a real response here.
PERFORMANCE_ENDPOINT_TEMPLATE = "/api/workout/{workout_id}/performance_graph"
PERFORMANCE_ENDPOINT_PARAMS = {"every_n": 5}  # placeholder value only — Task 1 must
                                               # capture the literal request Peloton's
                                               # own web client makes and record the
                                               # real parameter(s), not guess one.


def _parse_performance_response(body: dict[str, Any]) -> dict[str, Any]:
    """UNCONFIRMED — Task 1's output, not written yet. Peloton's
    performance_graph response shape has never been captured in this repo
    (unlike effort_zones above, which IS confirmed). Task 1 must capture
    one real response and implement this function's actual field
    extraction against it.

    Contract, fixed regardless of what Task 1 finds: returns a dict with
    "avg_hr", "max_hr", "max_power" (each int | None — a field genuinely
    absent from a well-formed response is None, never fabricated or
    derived from effort_zones or anything else) and, ONLY if Task 1
    confirms it is present at zero extra parsing/storage cost, "avg_cadence"
    (int | None; omit the key entirely from this dict if Task 1 finds it
    is not free, so download() never looks for it). Never raises for a
    well-formed-but-data-sparse response (e.g. no monitor paired) — that
    is a legitimate set of Nones, not a fetch failure (see
    fetch_workout_performance()).
    """
    raise NotImplementedError("Task 1: capture a real performance_graph response and implement this")


def fetch_workout_performance(self, workout_id: str) -> dict[str, Any] | None:
    """Public (not `_`-prefixed): also called directly by the backfill
    script, so this HTTP/retry/parsing logic exists in exactly one place
    (#46's reasoning for fetch_class_details(), reused). Returns None for
    ANY failure short of AuthenticationError — AC5 explicitly wants every
    performance-fetch failure logged and degraded to NULL, for both the
    live sync path and the backfill tool, never aborting the run over one
    workout's missing HR data. This is a deliberately WIDER safety net
    than #46's fetch_class_details(), which only treats a confirmed 404
    this way and lets TransientError/429/5xx propagate to the Sync
    Engine's own retry policy for the whole sync attempt. See
    "Risks/tradeoffs" for why the two fetches differ.

    AuthenticationError (401/403) still propagates unchanged — an invalid
    session is a connector-wide concern (ADR-009's degradation path), not
    a per-workout data gap, and must not be silently swallowed here."""
    try:
        body = retry_with_backoff(
            lambda: self._authenticated_get(
                f"{self._base_url}{PERFORMANCE_ENDPOINT_TEMPLATE.format(workout_id=workout_id)}",
                params=PERFORMANCE_ENDPOINT_PARAMS,
            ),
            PROVIDER,
        )
    except AuthenticationError:
        raise
    except (TransientError, PelotonHTTPError) as exc:
        diagnostic_logger().warning(
            f"{PROVIDER}: performance fetch failed for workout_id={workout_id!r}: {exc}"
        )
        return None
    return _parse_performance_response(body)
```

**No in-run cache here**, unlike #46's `ride_details_cache`. Ride details are shared across every workout that retakes the same on-demand class; performance data is intrinsically per-workout-instance — there is nothing to deduplicate, so `fetch_workout_performance()` is called at most once per workout per run regardless.

### `download()`: the combined loop

```python
def download(self, since: str | None = None) -> list[dict[str, Any]]:
    # ... existing user_id + pagination logic, unchanged ...
    since_epoch = int(since) if since is not None else None
    ride_details_cache: dict[str, dict | None] = {}  # #46, ride-level only

    for workout in workouts:
        start_time = workout.get("start_time")
        is_new_since_checkpoint = (
            since_epoch is None or (start_time is not None and start_time > since_epoch)
        )

        # --- #46: class metadata (ride-level; unchanged from that design) ---
        if not _is_class_workout(workout):
            workout["_class_type"] = CLASS_TYPE_NOT_A_CLASS
        elif is_new_since_checkpoint:
            ride_id = workout.get(RIDE_ID_FIELD)
            if ride_id not in ride_details_cache:
                ride_details_cache[ride_id] = self.fetch_class_details(ride_id)
            details = ride_details_cache[ride_id]
            if details is None:
                workout["_class_type"] = CLASS_TYPE_LOOKUP_FAILED
            else:
                workout["_class_title"] = details.get(CLASS_TITLE_FIELD)
                workout["_instructor_name"] = details.get(INSTRUCTOR_NAME_FIELD)
                workout["_class_type"] = details.get(CLASS_TYPE_RAW_FIELD)
                workout["_planned_duration_s"] = details.get(PLANNED_DURATION_FIELD)
                workout["_provider_class_id"] = ride_id
        # else: an already-synced class workout — every _class_* key stays
        # absent, exactly as #46 specified.

        # --- #47: HR zone durations + effort points. Zero extra network
        # calls, so NEVER gated on the checkpoint (see "never gated" above).
        effort_zones = workout.get(EFFORT_ZONES_FIELD)
        if effort_zones is not None:
            zone_durations = effort_zones.get(HR_ZONE_DURATIONS_FIELD) or {}
            for zone_n, zone_key in HR_ZONE_DURATION_FIELDS.items():
                workout[f"_hr_zone_{zone_n}_s"] = zone_durations.get(zone_key)
            workout["_effort_points"] = effort_zones.get(TOTAL_EFFORT_POINTS_FIELD)
        # else: effort_zones is null on this record -> every _hr_zone_*_s/
        # _effort_points key stays absent -> normalize() returns None for
        # all of them (AC1).

        # --- #47: performance endpoint. Same skip-if-already-synced reuse
        # as #46's ride lookup, but gated on the checkpoint alone,
        # independent of class status — any discipline can have HR data.
        if is_new_since_checkpoint:
            performance = self.fetch_workout_performance(workout.get("id"))
            if performance is None:
                workout["_hr_fetch_status"] = HR_FETCH_STATUS_FAILED
            else:
                workout["_hr_fetch_status"] = HR_FETCH_STATUS_OK
                workout["_avg_hr"] = performance.get("avg_hr")
                workout["_max_hr"] = performance.get("max_hr")
                workout["_max_power"] = performance.get("max_power")
                if "avg_cadence" in performance:
                    workout["_avg_cadence"] = performance.get("avg_cadence")
        # else: not new since checkpoint — every _avg_hr/_max_hr/_max_power/
        # _hr_fetch_status/_avg_cadence key stays absent, same "not
        # attempted this pass" semantics as #46's _class_* keys — COALESCE
        # preserves whatever is already stored.
    return workouts
```

### `normalize()`: reads the (possibly absent) keys, never guesses

```python
def normalize(self, raw: dict[str, Any]) -> dict[str, Any]:
    return {
        # ... existing keys, unchanged except as noted ...
        "avg_hr": raw.get("_avg_hr"),      # was: hardcoded None (issue #5's finding).
        "max_hr": raw.get("_max_hr"),      # Now sourced from the performance endpoint,
        "max_power": raw.get("_max_power"),  # absent -> None, same "not attempted/no data" rule.
        "hr_zone_1_s": raw.get("_hr_zone_1_s"),
        "hr_zone_2_s": raw.get("_hr_zone_2_s"),
        "hr_zone_3_s": raw.get("_hr_zone_3_s"),
        "hr_zone_4_s": raw.get("_hr_zone_4_s"),
        "hr_zone_5_s": raw.get("_hr_zone_5_s"),
        "effort_points": raw.get("_effort_points"),
        "hr_fetch_status": raw.get("_hr_fetch_status"),
        # Only if Task 1 confirms avg cadence is free and the column was added:
        # "avg_cadence_rpm": raw.get("_avg_cadence"),
    }
```

`avg_power`'s existing derivation (`total_work / duration_s`) is untouched — unrelated to this issue, already a legitimate non-fabricated derivation per issue #5.

### The COALESCE-based upsert, extended from #46

`upsert_normalized_activity()` already (per #46) COALESCEs 6 columns in the `UPDATE` branch. This issue adds `avg_hr`, `max_hr`, `max_power`, `hr_fetch_status` (and `avg_cadence_rpm` if added) to that same COALESCE set — 10 or 11 columns total, not a second mechanism:

```python
# UPDATE branch, extending #46's column list:
#   avg_hr = COALESCE(?, avg_hr), max_hr = COALESCE(?, max_hr),
#   max_power = COALESCE(?, max_power), hr_fetch_status = COALESCE(?, hr_fetch_status),
#   hr_zone_1_s = ?, hr_zone_2_s = ?, hr_zone_3_s = ?, hr_zone_4_s = ?, hr_zone_5_s = ?,
#   effort_points = ?,   -- these 6 unconditional, per "Schema" above
#   activity_title = COALESCE(?, activity_title), ...  -- #46's existing 6, unchanged
```

This is safe for the same reason #46's COALESCE change was safe: a value genuinely re-derivable every pass (zone data) keeps unconditional overwrite; a value with a real "not attempted this pass" case gets COALESCE. For non-Peloton providers, `avg_hr`/`max_hr`/`max_power` are always supplied by `normalize()` directly (never `None` due to a skip — Strava/Eufy have no equivalent optimization), so `COALESCE(real_value, old_value)` always overwrites identically to today's behavior; no cross-provider regression.

### Why `renormalize_provider()` stays safe (same reasoning as #46)

A stored Peloton `raw_activities` row synced before this feature shipped has no `_avg_hr`/`_hr_fetch_status`/etc. keys at all. `renormalize_provider()` calls `connector.normalize(raw)` directly with no new network I/O (#36/#45's contract) — `raw.get("_avg_hr")` returns `None`, `normalize()` returns `None` for all the gated fields, and the COALESCE upsert preserves whatever's already stored. Re-running it for an unrelated future reason cannot wipe previously-backfilled HR data, exactly the same guarantee #46 established for class metadata.

## Reused mechanism from #46 (explicit BO directive)

Per the requirements doc's explicit scope note, this issue reuses rather than duplicates:
- **`retry_with_backoff()`** — the module-level function #46 extracts from `SynchronizationEngine._with_retries` into `trainiq/sync/engine.py`. `fetch_workout_performance()` calls it exactly like `fetch_class_details()` does.
- **The skip-if-already-synced-since-checkpoint optimization** — the same `since_epoch`/`is_new_since_checkpoint` computation #46 introduces, read once per workout and shared by both the ride-detail and performance-fetch branches (see "the combined loop" above), not recomputed twice.
- **The backfill script** — extended, not duplicated (next section), per the BO's explicit "share one fetch/backfill mechanism instead of building it twice."

## Backfill tool (AC4): extends #46's script, doesn't duplicate it

**Architect's call** (requirements doc leaves this open): extend and rename #46's planned `scripts/backfill_peloton_class_metadata.py` to **`scripts/backfill_peloton_workout_details.py`** — by the time this issue is implemented, #46's script should already be on `main` under its original name (the Developer confirms this and performs the rename as part of this issue's own change, updating #46's existing tests' imports/references along with it). The rename reflects that the script now backfills two independent concerns (class metadata, HR/power) through the same per-row mechanism, not that the mechanism itself changes.

```python
# trainiq/connectors/peloton.py
def apply_hr_performance_update(conn: sqlite3.Connection, external_id: str, fields: dict[str, Any]) -> None:
    """Updates ONLY avg_hr, max_hr, max_power, hr_fetch_status (and
    avg_cadence_rpm if added) on an existing normalized_activities row.
    Same narrow, single-writer-invariant exception as #46's
    apply_class_metadata_update(), same reasoning (schema.py's
    bo_confirmed_valid/bo_confirmed_at precedent). Caller commits; this
    function does not."""
    conn.execute(
        """
        UPDATE normalized_activities
        SET avg_hr = ?, max_hr = ?, max_power = ?, hr_fetch_status = ?
        WHERE provider = 'peloton' AND external_id = ?
        """,
        (fields.get("avg_hr"), fields.get("max_hr"), fields.get("max_power"),
         fields.get("hr_fetch_status"), external_id),
    )
```

Selection query — both concerns OR'd together, each row processed independently for whichever it's still missing:

```sql
SELECT na.external_id, ra.payload_json
FROM normalized_activities na
JOIN raw_activities ra ON ra.provider = na.provider AND ra.external_id = na.external_id
WHERE na.provider = 'peloton'
  AND (
    na.class_type IS NULL
    OR (:retry_failed AND na.class_type = 'lookup_failed')
    OR na.hr_fetch_status IS NULL
    OR (:retry_failed AND na.hr_fetch_status = 'failed')
  )
ORDER BY na.id
```

Per row: parse the already-stored raw payload (no re-fetch of the workouts list — `effort_zones`/`id`/`workout_type` were already captured verbatim when the row was originally synced, same `_upsert_raw_activity` "persists as-is" contract #46 relies on). If this row's class metadata is still missing/retryable, run #46's existing branch. If `hr_fetch_status` is still missing/retryable, call `connector.fetch_workout_performance(external_id)` directly (already wrapped in `retry_with_backoff()` internally — the script does not wrap it a second time) and write via `apply_hr_performance_update()`. Both branches can run for the same row in the same pass; **commit after every row** regardless of which ran, same resumability guarantee as #46 (an interrupted run loses at most the one/two in-flight lookups for that row). A single `--retry-failed` flag covers both `lookup_failed` and `hr_fetch_status='failed'` — one flag, one concept ("retry a previously-failed attempt"), not two.

## Affected components/files

**New:**
- `tests/test_peloton_connector.py` — new `normalize()`/`download()`/`fetch_workout_performance()` cases (this issue's).
- `tests/test_backfill_peloton_workout_details.py` — replaces/extends #46's planned `tests/test_backfill_peloton_class_metadata.py` test file name to match the rename.

**Changed:**
- `trainiq/storage/schema.py` — migration v6 (building on #46's v5): `hr_zone_1_s`…`hr_zone_5_s`, `effort_points`, `hr_fetch_status` (and `avg_cadence_rpm` if Task 1 confirms it's free).
- `trainiq/connectors/peloton.py` — `EFFORT_ZONES_FIELD`/`HR_ZONE_DURATIONS_FIELD`/etc. constants, the UNCONFIRMED performance-endpoint constants, `_parse_performance_response()`, `fetch_workout_performance()`, `download()`'s combined loop, `normalize()`'s new keys, `apply_hr_performance_update()`.
- `trainiq/normalization/engine.py` — `_build_activity_record()` reads the new `normalized` keys (same as #46 for its 6).
- `trainiq/sync/engine.py` — `upsert_normalized_activity()`'s column lists: `avg_hr`/`max_hr`/`max_power`/`hr_fetch_status` move into the COALESCE set; `hr_zone_*_s`/`effort_points` added as unconditional-overwrite.
- `scripts/backfill_peloton_class_metadata.py` → renamed `scripts/backfill_peloton_workout_details.py`, extended per "Backfill tool" above.

**Unchanged, verified not assumed:**
- `trainiq/normalization/renormalize.py` — no changes needed; stays safe per "Why `renormalize_provider()` stays safe" above.
- `trainiq/dedup/detector.py` — out of scope; this issue's fields have no dedup/linked-pair interaction the requirements doc asks for.
- `tests/test_architecture_invariants.py` — `apply_hr_performance_update()` is a plain `UPDATE`, not an `INSERT`/`build_canonical_record()` call, same exemption reasoning as #46's `apply_class_metadata_update()`. Flagged for a Developer/reviewer's second look at review time for the same reason #46 flagged it: a new write path the static scan cannot see by design.

## Interfaces/contracts

`fetch_workout_performance(workout_id: str) -> dict[str, Any] | None` — see full docstring above; returns `None` only on a non-auth failure, never raises for sparse-but-valid data.

`apply_hr_performance_update(conn, external_id, fields) -> None` — narrow single-writer exception, same shape as #46's `apply_class_metadata_update()`.

No new query is required by any acceptance criterion here (unlike #46's AC5 query) — Epic 7's eventual TRIMP query is explicitly out of scope.

## Task breakdown

1. **Verification (blocking, do first — same convention as #46's Task 1):** using the BO's manually-supplied bearer token, run a temporary, isolated, one-off diagnostic (same throwaway pattern as `debug_peloton_manual_bearer.py`) against the real account:
   - (a) Call the issue's claimed `GET /api/workout/{id}/performance_graph?every_n=...` (or whatever the real Peloton web client actually calls — capture that request, don't guess the parameter) for a workout known to have an HR monitor paired. Record the full real response shape.
   - (b) Identify the real field names/paths for average heart rate, max heart rate, and max power within that response.
   - (c) Check whether average cadence is present in the *same* response at no extra parsing/storage cost; if yes, note the field name so the Developer adds `avg_cadence_rpm` to the migration — if no, the Developer omits it entirely.
   - (d) Run the same call for a workout with no HR monitor paired (if one exists in the account) and confirm the response is well-formed with the relevant fields absent/null, not an error.
   - (e) Check a real captured record (live or an already-stored `raw_activities` payload) for a top-level `hr_total_points` field alongside `effort_zones.total_effort_points`; record whether they're identical or distinct (not blocking, not stored either way — see "Evidence gap").
   - Record findings as a dated addendum to `docs/trainiq/verification/peloton-2026-09-28.md` (that file's established convention). Update `PERFORMANCE_ENDPOINT_TEMPLATE`/`PERFORMANCE_ENDPOINT_PARAMS` and implement `_parse_performance_response()` against the real shape found.
2. Schema migration v6 (coordinate the actual version number with #46's landed state): `hr_zone_1_s`…`hr_zone_5_s`, `effort_points`, `hr_fetch_status`, `avg_cadence_rpm` (conditional).
3. `trainiq/connectors/peloton.py`: confirmed `effort_zones` constants (no Task 1 dependency), `fetch_workout_performance()`, `_parse_performance_response()` (per Task 1's findings), `download()`'s combined loop, `normalize()`'s new keys, `apply_hr_performance_update()`.
4. `trainiq/normalization/engine.py`: read the new keys.
5. `trainiq/sync/engine.py`: extend `upsert_normalized_activity()`'s COALESCE set with `avg_hr`/`max_hr`/`max_power`/`hr_fetch_status` (and `avg_cadence_rpm` if added); add `hr_zone_*_s`/`effort_points` as unconditional-overwrite.
6. Rename and extend the backfill script per "Backfill tool" above; update #46's existing backfill tests' references if #46 has already landed with the original name.
7. Tests (see below) — the performance-endpoint fixture cases cannot be written until step 1 lands.
8. Add a new BACKLOG entry (next free number after `BL-010`/#45 and `BL-011`/#46, both still pending as of this doc — verify the actual next-free number at implementation time) recording Task 1's findings: the real performance endpoint/params, the real field names, whether avg cadence was free, and the `hr_total_points` relationship.
9. Run the full existing test suite, in particular `tests/test_sync_engine.py`, `tests/test_renormalize.py`, `tests/test_architecture_invariants.py`, and #46's own Peloton tests (zero regressions expected in any of them).

## Test strategy notes

All in fixtures/mocks — no live Peloton access from CI (Testing scope boundary, same as #46).

`normalize()` cases (AC1, no Task 1 dependency — can be written now):
- `effort_zones` populated with all 5 zone durations + `total_effort_points` → all 6 fields pass through correctly; `avg_hr`/`max_hr`/`max_power` unaffected by this part.
- `effort_zones: null` → all 6 fields `None`, not zero (AC1's explicit "null effort_zones -> NULL, not zero").
- No `_avg_hr`/`_max_hr`/`_max_power`/`_hr_fetch_status` keys at all (not attempted this pass) → all four fields `None` in `normalize()`'s output — the case the COALESCE upsert test depends on.
- `_hr_fetch_status = "ok"` with `_avg_hr`/`_max_hr`/`_max_power` set → pass through correctly.
- `_hr_fetch_status = "failed"`, no HR/power keys → `hr_fetch_status == "failed"`, the three fields `None`.

`download()` cases:
- A workout older than `since` → no `fetch_workout_performance()` call, every gated key absent; `effort_zones` still read regardless (zone data is never gated).
- A workout with `since=None` (first sync) → always attempted.
- `fetch_workout_performance()` returns `None` (simulated persistent failure) → `_hr_fetch_status == "failed"`, a warning logged, `download()` does **not** raise (AC5 — this is the deliberate divergence from #46's ride-lookup error handling, see "Risks/tradeoffs").
- `fetch_workout_performance()` raises `AuthenticationError` → propagates out of `download()` uncaught (unchanged connector-wide behavior).

`fetch_workout_performance()` cases, once Task 1 lands:
- A captured fixture response with full HR/power data → correctly parsed.
- A captured fixture response for a workout with no monitor paired → `avg_hr`/`max_hr`/`max_power` all `None`, no exception.
- Simulated 404/5xx/429-exhausted-retries → returns `None`, logged, does not raise.
- Simulated 401/403 → raises `AuthenticationError`, propagates.

`tests/test_sync_engine.py` (`upsert_normalized_activity()`):
- Update where the new record's `avg_hr`/`max_hr`/`max_power`/`hr_fetch_status` are all `None` → all four unchanged from before (COALESCE preservation — fails loudly if a future edit reverts to unconditional overwrite).
- Update where the new record supplies real values for those four → all four overwritten.
- Update where `hr_zone_*_s`/`effort_points` are supplied → always overwritten regardless of the other four's state (proves the two groups don't share behavior).

`tests/test_backfill_peloton_workout_details.py` (extends #46's planned backfill tests):
- A row missing only class metadata, a row missing only HR data, and a row missing both → each ends up correctly and independently populated in one pass.
- `--retry-failed` re-attempts both `lookup_failed` and `hr_fetch_status='failed'` rows; a plain run skips both.
- Resume after simulated interruption (same style as #46): already-processed rows are not re-fetched on re-run.

Manual/BO-run verification (not CI): Task 1 itself, and running the backfill against the BO's real 136 rows — both explicitly out of scope for this pipeline, same as #46/#45.

## Risks/tradeoffs

- **The core risk, same category as #46's:** the performance endpoint's existence, parameters, and response shape are entirely unconfirmed against this project's own evidence. Task 1 is the mitigation; only the flagged constants and `_parse_performance_response()`'s body change based on what it finds — never the schema, the skip/COALESCE logic, or the backfill shape.
- **`fetch_workout_performance()` deliberately degrades to NULL on failures that #46's `fetch_class_details()` would let propagate** (e.g. retries-exhausted 5xx). This is not an inconsistency — it's AC5's explicit, literal requirement ("a performance-endpoint fetch failure... results in NULL... for both the live sync path and the backfill tool"), different from #46's AC for ride lookups. The tradeoff: a persistent Peloton-side outage on this specific endpoint silently yields NULL HR data for affected workouts rather than stalling/failing the whole sync, which is the right call for genuinely supplementary enrichment data feeding a not-yet-built training-load feature (Epic 7) — but it does mean an operator won't be loudly alerted to a broad performance-endpoint outage from sync failures alone; only the backfill's `hr_fetch_status='failed'` rows and the logged warnings surface it.
- **Changing `avg_hr`/`max_hr`/`max_power` from unconditional-overwrite to COALESCE is a real behavior change to pre-existing columns**, not just new columns added — called out explicitly in "Schema" above specifically so it isn't missed at review time. It is necessary for correctness once these three columns gain a "not attempted this pass" case; the alternative (leaving them unconditional-overwrite) would silently erase previously-captured HR data on every sync that skips an already-synced workout.
- **No in-run cache for the performance fetch** (unlike ride details) is correct, not a missed optimization — performance data is never shared across workouts, so there's nothing to deduplicate.
- **`avg_cadence_rpm`'s very existence as a column is conditional on Task 1's finding**, not a committed design. If Task 1 finds it isn't free, the Developer simply never adds the column — no half-built conditional logic ships either way.
- **Backfill script rename** (`backfill_peloton_class_metadata.py` → `backfill_peloton_workout_details.py`) touches #46's work if #46 has landed by implementation time — a coordination point, not a risk to this issue's own correctness, but the Developer must update #46's existing tests' references as part of this change, not leave a stale filename referenced anywhere.
