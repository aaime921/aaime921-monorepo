# Architecture: Record Class Title, Instructor, Class Type and Planned Length (Peloton + Strava)

**Issue:** #46
**Requirements:** [`docs/trainiq/requirements/46-peloton-strava-class-metadata.md`](../requirements/46-peloton-strava-class-metadata.md)
**Related:** #5 (workout-list endpoint shape, the only live-verified Peloton evidence this design can build on), #37 (dedup links, reused for the linked-pair query), #45 (`_distance_unit`-style connector-internal key pattern and `raw_transform` renormalize precedent this design follows), #33 (checkpoint cursor type discipline)

## Evidence gap, stated up front (per the Architect role's evidence-based principle)

This design rests on **two unverified facts**, not one. The requirements doc frames the Peloton *call choice* (joins-on-list vs. per-workout details endpoint) as the open verification gap — but checking the actual live-captured evidence on file (`docs/trainiq/verification/peloton-2026-09-28.md`) turns up a second, more basic gap: that file's "full real record" for `GET /api/user/{user_id}/workouts` lists exactly `id, start_time, end_time, fitness_discipline, total_work, distance, calories, effort_zones` — **it does not show a `workout_type` or `peloton_id` field at all.** The issue body's own claim ("the workout payload has `peloton_id`..., `workout_type: "class"`") is therefore itself unconfirmed against this project's own evidence, not just assumed-but-probably-true. No `docs/trainiq/verification/*.md` file covers the ride/class detail endpoint (`GET /api/ride/{id}/details` or the `joins=` parameter) at all.

Per the role doc ("Don't assume 'the documentation says this' without checking live-captured evidence... If not, flag as a verification gap") and this Architect routine's own sandbox constraint (no network egress to `onepeloton.com`, same limitation documented in #45's design), this is flagged, not guessed past. **Task 1 below is a mandatory, blocking live-verification step**, run by whoever holds the BO's manual bearer token — same precedent as #45's Task 1 and the `debug_peloton_manual_bearer.py` diagnostics that resolved issue #5. Every constant this design proposes for field names Task 1 might change is marked **UNCONFIRMED** and given its own fallback. Everything else in this doc (schema, the skip/COALESCE optimization, the backfill tool's resumability, the query, the tests' shapes) does not change regardless of what Task 1 finds — only the handful of flagged constants would.

## Approach

### Schema: new columns on `normalized_activities`, not a separate table

Six new nullable columns, additive migration (schema v5):

| Column | Type | Populated by |
|---|---|---|
| `activity_title` | TEXT | Peloton (class title) or Strava/Strava-unofficial (raw `name`) |
| `instructor_name` | TEXT | Peloton only |
| `class_type` | TEXT | Peloton only — Peloton's own raw category string, stored verbatim |
| `planned_duration_s` | INTEGER | Peloton only |
| `provider_class_id` | TEXT | Peloton only |
| `sport_type_raw` | TEXT | Strava/Strava-unofficial only |

**Why one table, not `activity_details`:** the requirements doc leaves this open, but two acceptance criteria settle it in practice. AC4 (linked-pair precedence) needs "query the stored data, get the Peloton side's class metadata" to be a plain `WHERE provider = 'peloton'` filter, not a join against a second table keyed by an id that would itself need the dedup logic to resolve first. AC5 (the Matt Wilpers/Power Zone query) needs to be a single-table `WHERE` clause the requirements doc explicitly asks to be "documented," not a multi-table query whose correctness depends on join semantics. Putting these columns directly on `normalized_activities` makes both queries trivial (see "Interfaces/contracts" below) and costs nothing extra for providers that don't use them — they're simply `NULL`, same pattern `athlete_profile`'s and `weigh_ins`' nullable columns already use throughout this schema.

**`class_type` is stored as Peloton's raw string, not mapped through a taxonomy.** Unlike `discipline` (which genuinely needs a small, closed canonical enum because the training-load engine branches on it), nothing downstream of `class_type` computes anything from it — AC5 only needs it to be an equality-filterable string. Inventing a canonical class-type enum here would be exactly the kind of "designing against an assumption with no evidence" this project's principle warns against, since the full set of values Peloton's taxonomy actually uses isn't known (Task 1 doesn't need to enumerate them — it only needs to confirm the field exists and show its format on real records).

### Connector-internal keys, same pattern as #45's `_distance_unit`

`download()` is the only place that may make network calls (Connector architecture invariant: Download fetches, Normalize maps — #45 made this split explicit for Peloton and this design preserves it). It attaches underscore-prefixed, connector-internal keys to each raw workout dict before returning it; `normalize()` reads only those keys and never calls the network. Keys, all optional (may be entirely absent — see "the skip case" below):

```
_class_title, _instructor_name, _class_type, _planned_duration_s, _provider_class_id
```

### Determining class vs. non-class, and the two sentinel values (AC2)

```python
# peloton.py — UNCONFIRMED, Task 1 target. The one real record on file
# (docs/trainiq/verification/peloton-2026-09-28.md) does not show either
# of these fields — they are the issue's own claim, not yet independently
# confirmed. If Task 1 finds WORKOUT_TYPE_FIELD doesn't exist at all,
# _is_class_workout() falls back to "has a non-null RIDE_ID_FIELD" as the
# class signal instead (the issue's own alternative framing implies some
# ride-id-shaped field must exist for the per-workout-details endpoint to
# be callable at all) — Task 1 must state explicitly which of the two
# applies, not leave both branches live in the shipped code.
WORKOUT_TYPE_FIELD = "workout_type"
RIDE_ID_FIELD = "peloton_id"

CLASS_TYPE_NOT_A_CLASS = "not_a_class"      # AC2: just-ride/scenic/free mode
CLASS_TYPE_LOOKUP_FAILED = "lookup_failed"  # AC2/AC7: class, but the detail fetch failed


def _is_class_workout(raw: dict[str, Any]) -> bool:
    """raw[WORKOUT_TYPE_FIELD] == "class" today. See module-level comment —
    Task 1 may need to change this to `raw.get(RIDE_ID_FIELD) is not None`
    if WORKOUT_TYPE_FIELD turns out not to exist."""
```

Both sentinels are plain strings in the same free-text `class_type` column a real Peloton category value would occupy — collision with a real Peloton-assigned category string is not a realistic concern (Peloton does not control this column's vocabulary; this project does).

### The ride/class detail call: joins-on-list preferred, per-ride-id cache as the fallback's mitigation

```python
# UNCONFIRMED — Task 1 picks one. In preference order:
#  (a) joins=ride,ride.instructor on the EXISTING paginated
#      GET /api/user/{user_id}/workouts call — zero new calls beyond
#      today's pagination if Peloton actually embeds the data this way.
#  (b) GET /api/ride/{ride_id}/details, one call per UNIQUE ride_id
#      (not per workout — see _RideDetailsCache below; Peloton reuses the
#      same ride_id whenever the BO retakes an on-demand class, so a
#      within-run cache keyed by ride_id is a real, free savings even
#      under plan (b), not just a defensive nicety).
RIDE_DETAIL_JOINS_PARAM = {"joins": "ride,ride.instructor"}
RIDE_DETAIL_ENDPOINT_TEMPLATE = "/api/ride/{ride_id}/details"

# UNCONFIRMED field names WITHIN whichever shape (a)/(b) resolves to.
# Least-confident of all the constants in this file — Peloton's ride
# object shape has literally never been captured in this repo.
CLASS_TITLE_FIELD = "title"
INSTRUCTOR_NAME_FIELD = "name"          # nested under an instructor object
CLASS_TYPE_RAW_FIELD = "ride_type_id"   # or whatever Task 1 actually finds
PLANNED_DURATION_FIELD = "duration"     # seconds


def fetch_class_details(self, ride_id: str) -> dict[str, Any] | None:
    """Public (not `_`-prefixed): also called directly by the backfill
    script (see below), so the HTTP/retry/error-shape logic exists in
    exactly one place. Returns None for a confirmed "this class no longer
    exists" response (e.g. 404) — logged by the caller, never raised as
    PelotonHTTPError for that specific case. Raises TransientError for
    429/5xx (ADR-037, honors Retry-After) and AuthenticationError for
    401/403 — both let the caller's existing retry/degradation handling
    apply unchanged, same as every other authenticated_get call in this
    connector."""
```

`download()` keeps an in-memory `dict[str, dict]` cache keyed by ride id for the duration of one call, so a BO who retakes the same on-demand class twice only triggers one network call for it, under plan (b); harmless no-op under plan (a) since there's nothing left to fetch per-ride at all.

### The skip-already-known-workouts optimization — required for correctness, not just efficiency

Peloton's `download()` already re-fetches and re-normalizes the **entire** workout history on every single sync (no verified request-side filter exists — see the module's existing comment on `since`). Naively adding a ride-detail fetch per class workout would turn that pre-existing, already-accepted inefficiency into O(history size) *new* network calls on every routine sync, not just the one-time backfill — a real, growing rate-limit exposure the issue's own "rate-limited and resumable" framing for the backfill tool shows this project is already sensitive to.

The fix: `download()` already receives `since` (the resume cursor) as a parameter, even though it isn't forwarded into the list-endpoint request itself (per issue #5's finding — no verified filter param exists for *that* call). Nothing stops using it for a second, independent purpose: deciding whether a given workout's class-detail fetch is worth attempting at all.

```python
def download(self, since: str | None = None) -> list[dict[str, Any]]:
    # ... existing user_id + pagination logic, unchanged ...
    since_epoch = int(since) if since is not None else None
    ride_details_cache: dict[str, dict | None] = {}
    for workout in workouts:
        if not _is_class_workout(workout):
            workout["_class_type"] = CLASS_TYPE_NOT_A_CLASS  # cheap, no network, every run
            continue
        start_time = workout.get("start_time")
        is_new_since_checkpoint = since_epoch is None or (start_time is not None and start_time > since_epoch)
        if not is_new_since_checkpoint:
            # Deliberately leaves every _class_* key ABSENT — see
            # normalize()'s handling and the COALESCE-based upsert below.
            # "Absent" here means "not attempted this run," which must be
            # distinguishable from "attempted and failed."
            continue
        ride_id = workout.get(RIDE_ID_FIELD)
        if ride_id not in ride_details_cache:
            ride_details_cache[ride_id] = self.fetch_class_details(ride_id)
        details = ride_details_cache[ride_id]
        if details is None:
            workout["_class_type"] = CLASS_TYPE_LOOKUP_FAILED
            diagnostic_logger().warning(f"{PROVIDER}: class lookup failed for ride_id={ride_id!r}")
        else:
            workout["_class_title"] = details.get(CLASS_TITLE_FIELD)
            workout["_instructor_name"] = details.get(INSTRUCTOR_NAME_FIELD)
            workout["_class_type"] = details.get(CLASS_TYPE_RAW_FIELD)
            workout["_planned_duration_s"] = details.get(PLANNED_DURATION_FIELD)
            workout["_provider_class_id"] = ride_id
    return workouts
```

This is not only a rate-limit optimization — it's load-bearing for a correctness issue that already exists elsewhere in this codebase's pattern: `trainiq.normalization.renormalize.renormalize_provider()` (issue #36/#45) re-derives canonical records by calling `connector.normalize(raw)` directly against an **already-stored** `raw_activities` payload, with **no new network call** at all. A stored Peloton raw payload never has `_class_*` keys (those only ever get attached by a live `download()`, never persisted into `raw_activities` under those underscore-prefixed names — `_upsert_raw_activity` persists the dict as-is, so they *would* actually be in there once this ships, but any row synced before this feature's `download()` existed has no such keys). Without special handling, re-running `renormalize_provider()` for Peloton for any unrelated future reason would silently wipe every already-backfilled class title/instructor/type back to `NULL` — a real, concrete regression this design must not introduce. See "the COALESCE-based upsert" below for the other half of the fix.

### `normalize()`: reads the (possibly absent) keys, never guesses

```python
def normalize(self, raw: dict[str, Any]) -> dict[str, Any]:
    # ... existing discipline/duration/power/distance logic, unchanged ...
    return {
        # ... existing keys, unchanged ...
        "activity_title": raw.get("_class_title"),
        "instructor_name": raw.get("_instructor_name"),
        "class_type": raw.get("_class_type"),            # None means "not attempted this pass"
        "planned_duration_s": raw.get("_planned_duration_s"),
        "provider_class_id": raw.get("_provider_class_id"),
    }
```

`raw.get("_class_type")` returns `None` in exactly the three cases that must all be treated as "say nothing new this pass, preserve whatever's already stored": the workout predates this feature's `download()` entirely (no keys were ever attached), or it's older than the sync checkpoint (deliberately skipped above). It returns `CLASS_TYPE_NOT_A_CLASS` or `CLASS_TYPE_LOOKUP_FAILED` for the two "attempted, with a definite outcome" cases, and Peloton's real raw category string on a successful lookup. `normalize()` itself does no interpretation of which case it is — that distinction lives entirely in `download()`, consistent with "Normalize: never derive fields that don't exist."

### The COALESCE-based upsert — the other half of the renormalize-safety fix

`upsert_normalized_activity()` (`sync/engine.py`) is extended with the 6 new columns. For these 6 **only**, the `UPDATE` branch uses `COALESCE(?, existing_column)` instead of unconditional overwrite; every pre-existing column keeps today's unconditional-overwrite behavior exactly as-is (that behavior is correct for them — `discipline`/`training_load`/etc. are always fully re-derivable from the raw payload alone, with no "didn't attempt" case).

```python
def upsert_normalized_activity(conn: sqlite3.Connection, record: dict) -> str:
    cursor = conn.execute(
        """
        INSERT OR IGNORE INTO normalized_activities
            (provider, external_id, start_time, duration_s, discipline, distance_m,
             avg_hr, max_hr, avg_power, max_power, calories,
             training_load, training_load_method, source_confidence,
             activity_title, instructor_name, class_type, planned_duration_s,
             provider_class_id, sport_type_raw)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (..., record["activity_title"], record["instructor_name"], record["class_type"],
         record["planned_duration_s"], record["provider_class_id"], record["sport_type_raw"]),
    )
    if cursor.rowcount == 1:
        return "inserted"
    conn.execute(
        """
        UPDATE normalized_activities SET
            start_time = ?, duration_s = ?, discipline = ?, distance_m = ?,
            avg_hr = ?, max_hr = ?, avg_power = ?, max_power = ?, calories = ?,
            training_load = ?, training_load_method = ?, source_confidence = ?,
            activity_title = COALESCE(?, activity_title),
            instructor_name = COALESCE(?, instructor_name),
            class_type = COALESCE(?, class_type),
            planned_duration_s = COALESCE(?, planned_duration_s),
            provider_class_id = COALESCE(?, provider_class_id),
            sport_type_raw = COALESCE(?, sport_type_raw)
        WHERE provider = ? AND external_id = ?
        """,
        (..., record["activity_title"], record["instructor_name"], record["class_type"],
         record["planned_duration_s"], record["provider_class_id"], record["sport_type_raw"],
         record["provider"], record["external_id"]),
    )
    return "updated"
```

This is safe for Strava (always supplies a real `activity_title`/`sport_type_raw` or a deliberate `None` when genuinely absent — `COALESCE(NULL, existing_NULL)` is still `NULL`, no behavior change) and for Peloton non-class workouts (always recompute the same sentinel every run — `COALESCE(same_value, old_value)` overwrites identically to before).

### Strava `name` / raw `sport_type` (AC3)

`StravaConnector._activity_to_raw_dict()` gains one field: `"name": activity.name if activity.name else None`. `normalize()`:

```python
discipline_raw = raw["sport_type"] or raw["type"]
return {
    ...,
    "discipline_raw": discipline_raw,
    "activity_title": raw.get("name"),
    "sport_type_raw": discipline_raw,   # same fallback chain, persisted under its own name
}
```

`StravaUnofficialConnector.normalize()` — `raw["name"]` is already present in the stored payload (the web endpoint's own field, per the issue body's verbatim field list; no `download()` change needed, only `normalize()`):

```python
return {
    ...,
    "activity_title": raw.get("name"),
    "sport_type_raw": raw.get("activity_type_display_name") or raw.get("display_type"),
}
```

### Linked-pair precedence (AC4) is satisfied by the schema choice, not new logic

Because `instructor_name`/`class_type`/`planned_duration_s`/`provider_class_id` are only ever populated on `provider = 'peloton'` rows, a query that filters on them structurally can never surface a Strava row's (non-existent) values — there is no merge, override, or `dedup_links` lookup needed for *this specific* query shape. Strava's own `activity_title` (its `name`) stays on its own row regardless, exactly as the requirements doc requires ("not lost, but is not what such a query surfaces"). A future aggregate that must avoid double-counting a linked pair (e.g. "total minutes trained," explicitly out of scope here) would still need `trainiq.dedup.detector.primary_activity_ids()` (#37) — noted for whoever builds that later, not needed by anything in this issue.

### Backfill tool (AC6)

A new script, `scripts/backfill_peloton_class_metadata.py`. Deliberately **not** built on `renormalize_provider()` (#36/#45's pattern): that function's entire contract is "re-derive a canonical record from an already-stored raw payload with zero new network I/O" — class metadata requires a genuinely new network call per class, which that function has no hook for and should not grow one for (it would stop being a pure offline recomputation for every other caller too).

Instead, a dedicated, narrow write path:

```python
# trainiq/connectors/peloton.py

def apply_class_metadata_update(conn: sqlite3.Connection, external_id: str, fields: dict[str, Any]) -> None:
    """Updates ONLY the 5 Peloton-enrichment columns on an existing
    normalized_activities row. Does not call build_canonical_record() and
    is not, and must not become, a parallel path for anything
    discipline/confidence/training_load/start_time computes — this is
    deliberately as narrow as ADR-039's bo_confirmed_valid/bo_confirmed_at
    columns, which schema.py's own comment already documents as written
    outside the normal sync upsert for the same reason (a value some other
    process legitimately owns, supplementary to the row's canonical
    identity). Caller commits; this function does not."""
    conn.execute(
        """
        UPDATE normalized_activities
        SET activity_title = ?, instructor_name = ?, class_type = ?,
            planned_duration_s = ?, provider_class_id = ?
        WHERE provider = 'peloton' AND external_id = ?
        """,
        (fields.get("activity_title"), fields.get("instructor_name"), fields.get("class_type"),
         fields.get("planned_duration_s"), fields.get("provider_class_id"), external_id),
    )
```

The script:
1. Authenticates a real `PelotonConnector` (same manual bearer token every live sync already uses).
2. Selects candidates: `SELECT external_id, payload_json FROM raw_activities na JOIN ... WHERE normalized_activities.provider = 'peloton' AND (class_type IS NULL OR (--retry-failed AND class_type = 'lookup_failed'))`, ordered by `id` for a stable, resumable walk.
3. For each: parse the already-stored raw payload (no re-fetch of the workouts list needed — `workout_type`/`peloton_id` were already captured verbatim by whatever `download()` run originally stored this row, per `_upsert_raw_activity`'s "persists the dict as-is" contract). Non-class → `apply_class_metadata_update()` with the `NOT_A_CLASS` sentinel, no network call. Class → `connector.fetch_class_details(ride_id)` wrapped in `retry_with_backoff()` (see below) → success or `LOOKUP_FAILED`, written via `apply_class_metadata_update()`.
4. **Commits after every row**, not in a batch at the end — an interrupted run (AC6's "resuming after a simulated interruption") loses at most the one in-flight lookup; everything already written stays written, and step 2's `class_type IS NULL` selection naturally excludes it on the next run.
5. Honors a rate limit via the retry helper below; an optional `--limit N` flag caps how many workouts one invocation attempts, for an operator who wants to budget calls across multiple manual runs rather than one long one.

### Retry/backoff reuse: `retry_with_backoff()` extracted from the Sync Engine

`SynchronizationEngine._with_retries` is promoted to a module-level function in `sync/engine.py` — exactly the same move issue #36 already made for `upsert_normalized_activity` (method → module function, for reuse by a one-off script), so the backfill tool gets ADR-037's real, already-tested retry-after-honoring backoff instead of a second, duplicated implementation.

```python
# trainiq/sync/engine.py

def retry_with_backoff(
    fn: Callable[[], T], provider: str, max_retries: int = 3,
    backoff_base_s: float = 1.0, sleep_fn: Callable[[float], None] = time.sleep,
) -> T:
    """Extracted from SynchronizationEngine._with_retries (issue #46) so
    scripts/backfill_peloton_class_metadata.py can reuse the exact same
    ADR-037 policy. Behavior for existing callers is unchanged."""


class SynchronizationEngine:
    def _with_retries(self, fn, provider):
        return retry_with_backoff(fn, provider, self._max_retries, self._backoff_base_s, self._sleep)
```

## Affected components/files

**New:**
- `scripts/backfill_peloton_class_metadata.py` — the backfill tool.
- `tests/test_peloton_class_metadata.py` (or extends `tests/test_peloton_connector.py` — Developer's call) — new `normalize()`/`download()` cases.
- `tests/test_backfill_peloton_class_metadata.py` — backfill tests (success, rate-limit retry, resume).

**Changed:**
- `trainiq/storage/schema.py` — migration v5 (6 new nullable columns on `normalized_activities`).
- `trainiq/connectors/peloton.py` — `_is_class_workout()`, `fetch_class_details()`, the two sentinels, `download()`'s class-detail attachment + skip-if-already-synced logic, `normalize()`'s 5 new keys, `apply_class_metadata_update()`.
- `trainiq/connectors/strava.py` — `name` added to the raw dict; `normalize()`'s 2 new keys.
- `trainiq/connectors/strava_unofficial.py` — `normalize()`'s 2 new keys (no `download()` change — `name` is already in the stored payload).
- `trainiq/normalization/engine.py` — `_build_activity_record()` reads the 6 new `normalized` keys into the canonical dict.
- `trainiq/sync/engine.py` — `upsert_normalized_activity()`'s column lists (COALESCE for the 6 new columns in the `UPDATE` branch only); `_with_retries` delegates to the new `retry_with_backoff()`.

**Unchanged, verified not assumed:**
- `trainiq/dedup/detector.py` / `dedup_links` — AC4 needs no changes here (see "Linked-pair precedence" above).
- `trainiq/normalization/renormalize.py` — not extended for this feature; the backfill tool is a deliberately separate mechanism (see "Backfill tool" above). Re-running `renormalize_provider()` for Peloton for an unrelated future reason remains safe because of the COALESCE upsert change, with no changes to `renormalize.py` itself needed.
- `tests/test_architecture_invariants.py` — no change needed. `apply_class_metadata_update()` is a plain `UPDATE`, not an `INSERT INTO normalized_activities` and not a `build_canonical_record()` call, so it trips neither of that file's static checks — deliberately, per the same reasoning schema.py's own comment already gives for `bo_confirmed_valid`/`bo_confirmed_at` (ADR-039) being written outside the sync-upsert path. Worth a Developer's second look at review time precisely because it's a new write path that the test suite's static scan cannot see by design (see that test file's own stated caveat) — flagged here so it isn't mistaken for an oversight.
- `trainiq/normalization/taxonomy.py` / `map_discipline()` — untouched; `class_type` is a separate, unmapped, free-text concept from `discipline` (see "Approach").

## Interfaces/contracts

Documented query for AC5 ("all Power Zone rides with Matt Wilpers in the last 90 days") — exact value for `class_type` pending Task 1, shown here with the flagged placeholder:

```sql
SELECT * FROM normalized_activities
WHERE provider = 'peloton'
  AND instructor_name = 'Matt Wilpers'
  AND class_type = 'power_zone_max'   -- Task 1's confirmed raw value, not this placeholder
  AND start_time >= :cutoff_iso_or_epoch;   -- 90 days ago, computed by the caller
```

No `dedup_links` involvement needed — see "Linked-pair precedence" above for why.

## Task breakdown

1. **Verification (blocking, do first — same convention as #45's Task 1):** using the BO's manually-supplied bearer token, run a temporary, isolated, one-off diagnostic (same throwaway pattern as `debug_peloton_manual_bearer.py`) against a real account. Confirm: (a) does `workout_type` exist on a real `GET /api/user/{user_id}/workouts` record, and if so its values for a known class and a known non-class workout; (b) does `peloton_id` (or any ride-id-shaped field) exist on that same record; (c) does `joins=ride,ride.instructor` on that endpoint actually embed ride/instructor data, and if so its exact field names/shapes for title, instructor name, class type, and planned duration; (d) if (c) doesn't work, try `GET /api/ride/{ride_id}/details` instead and capture its shape. Record findings as a dated addendum to `docs/trainiq/verification/peloton-2026-09-28.md` (that file's own established convention — see its existing "Correction note" section). Update `WORKOUT_TYPE_FIELD`, `RIDE_ID_FIELD`, `_is_class_workout()`'s fallback branch (if needed), `RIDE_DETAIL_JOINS_PARAM`/`RIDE_DETAIL_ENDPOINT_TEMPLATE` (pick one), and `CLASS_TITLE_FIELD`/`INSTRUCTOR_NAME_FIELD`/`CLASS_TYPE_RAW_FIELD`/`PLANNED_DURATION_FIELD` to match. If `workout_type` genuinely doesn't exist anywhere, switch `_is_class_workout()` to the `RIDE_ID_FIELD`-presence fallback and record that as the finding instead — it doesn't block the rest of this design, only that one function's internals.
2. Schema migration v5: 6 new nullable columns on `normalized_activities` (`trainiq/storage/schema.py`).
3. `trainiq/connectors/peloton.py`: sentinels, `_is_class_workout()`, `fetch_class_details()` (with its within-call ride-id cache), `download()`'s skip-if-already-synced + attach logic, `normalize()`'s 5 new keys, `apply_class_metadata_update()`.
4. `trainiq/connectors/strava.py` and `strava_unofficial.py`: `name`/`sport_type` extraction per "Approach" above.
5. `trainiq/normalization/engine.py`: `_build_activity_record()` reads the 6 new keys.
6. `trainiq/sync/engine.py`: extend `upsert_normalized_activity()`'s column lists with COALESCE for the 6 new columns in the `UPDATE` branch; extract `retry_with_backoff()`.
7. `scripts/backfill_peloton_class_metadata.py`: selection query, per-row processing loop, `--retry-failed` and `--limit` flags, incremental commit.
8. Tests (see below).
9. Add a new `BL-011` entry to `projects/trainiq/BACKLOG.md` (the next free number — `BL-010` is already reserved by #45's design, not yet landed as of this doc) recording whichever of Task 1's findings turned out to be a real discrepancy from the issue's original assumption (at minimum: whether `workout_type`/`peloton_id` actually exist, and which of the joins-vs-details-endpoint approaches was used), independent of what Task 1 finds.
10. Run the full existing test suite and confirm zero regressions — in particular `tests/test_sync_engine.py`, `tests/test_renormalize.py`, and `tests/test_architecture_invariants.py` (unchanged, but worth confirming nothing here accidentally trips its static checks).

## Test strategy notes

All in fixtures/mocks — no live Peloton/Strava access from CI (Testing scope boundary).

`tests/test_peloton_connector.py` (or a new file), `normalize()` cases — AC1/AC2/AC7, tested directly against raw dicts (no `download()` involved):
- A class workout with `_class_title`/`_instructor_name`/`_class_type`/`_planned_duration_s`/`_provider_class_id` all set → all 5 fields pass through correctly; `duration_s` (actual) unaffected.
- A non-class workout (`_class_type = "not_a_class"`, no other `_class_*` keys) → `class_type == "not_a_class"`, `instructor_name is None`.
- A class workout where `_class_type = "lookup_failed"` and no other `_class_*` keys → same, `class_type == "lookup_failed"`.
- A workout with **no** `_class_*` keys at all (the "not attempted this pass" case) → every one of the 5 new fields is `None` in `normalize()`'s output — this is the case the COALESCE upsert test (below) depends on.

`download()` cases:
- Two workouts sharing the same `ride_id`, both newer than `since` → `fetch_class_details()` called exactly once (cache hit on the second), both workouts get the same class metadata.
- A class workout older than `since` → no `fetch_class_details()` call at all, no `_class_*` keys attached.
- A class workout with `since=None` (first-ever sync) → always attempted, regardless of `start_time`.
- `fetch_class_details()` returns `None` (simulated 404) → `_class_type == "lookup_failed"`, a warning logged.
- `fetch_class_details()` raises `TransientError` (simulated 429) → propagates out of `download()` uncaught (same as every other transient condition in this connector — the Sync Engine's existing retry policy handles it, nothing new needed here).

`tests/test_sync_engine.py` (or a new `test_upsert_normalized_activity.py`), `upsert_normalized_activity()` cases:
- Insert a fresh row with all 6 new fields populated → stored as given.
- Update an existing row where the new record's 6 new fields are all `None` → all 6 **unchanged** from before (proves the COALESCE preservation — this is the test that would fail loudly if a future edit reverted to unconditional overwrite).
- Update an existing row where the new record supplies real values for the 6 fields → all 6 overwritten (proves COALESCE doesn't just always preserve).

`tests/test_strava_connector.py` / `tests/test_strava_unofficial_connector.py`: a fixture activity with `name`/`sport_type` (official) or `name`/`activity_type_display_name` (unofficial) set → `activity_title`/`sport_type_raw` populated correctly; a fixture with `name` absent/`None` → `activity_title is None`, no warning (this isn't an error condition).

`tests/test_backfill_peloton_class_metadata.py`:
- **Successful run:** seed `raw_activities`/`normalized_activities` with a mix of class and non-class Peloton rows, all `class_type IS NULL`; run the script against a fake connector; assert every row ends with the correct `class_type`/metadata and `conn.commit()` was called per-row (or at minimum that the DB state is fully correct — commit-per-row is an implementation detail, the observable contract is "every completed row survives an interruption," tested next).
- **Simulated rate-limiting:** fake connector's `fetch_class_details()` raises `TransientError(retry_after_s=...)` once then succeeds → the row ends up correctly populated, and the fake `sleep_fn` was called with the expected delay (same assertion style `test_sync_engine.py` already uses for ADR-037).
- **Resume after simulated interruption:** run the script against a DB with N pending rows, stop it (e.g. inject an exception) after processing some but not all; re-run against the same DB; assert the already-processed rows were not re-fetched (fake connector's call count) and no duplicate/changed values resulted, and the remaining rows are now processed.
- **`--retry-failed`:** a row with `class_type = 'lookup_failed'` is skipped by a plain run and re-attempted only when the flag is passed.

Manual/BO-run verification (not CI): Task 1 itself, and actually invoking the backfill script against the BO's 136 real rows — both explicitly out of scope for this pipeline per the requirements doc, same as #45's AC1/AC5.

## Risks/tradeoffs

- **The core risk, stated once more because it's load-bearing for almost everything else in this doc:** `workout_type`/`peloton_id`'s very existence, and the entire ride/class detail response shape, are unconfirmed against this project's own live evidence. Task 1 is the mitigation, structured so only a small set of named constants (never the schema, the skip/COALESCE logic, the backfill tool's shape, or the tests' structure) need to change based on what it finds.
- **The skip-if-already-synced optimization means a normal live sync will never "heal" a row that failed its class lookup once**, even if the underlying cause (e.g. a transient Peloton-side issue) resolves itself later — only the backfill script's `--retry-failed` flag re-attempts those. This is a deliberate trade: the alternative (retrying every `lookup_failed` row on every single sync, forever) risks exactly the unbounded-rate-limit-cost problem this optimization exists to avoid, for a case (a genuinely deleted class) that's unlikely to ever resolve. Flagged as a real, accepted limitation, not an oversight — recorded in the new BACKLOG item (Task 9) so it's visible to whoever next revisits Peloton's rate-limit behavior.
- **`class_type` stores Peloton's raw vocabulary unmapped.** If a future issue wants a canonical class-type enum (e.g. to group "Power Zone" and "Power Zone Max" together), that's new taxonomy work on top of this column, not something this design pre-builds speculatively without evidence of what values actually exist.
- **The backfill tool's narrow `UPDATE` path deliberately bypasses `build_canonical_record()`/the single-writer invariant's authorized-entry-point list.** This is a considered exception (see "Affected components/files" above and schema.py's existing `bo_confirmed_valid` precedent), not an oversight, but it is exactly the kind of "new abstraction layer touching these tables" `test_architecture_invariants.py`'s own docstring says its static checks cannot catch — a human reviewer is the actual backstop here, so this is called out explicitly rather than left for a reviewer to discover unprompted.
- **Backfill is network-bound and genuinely cannot be exercised live in CI** — same Testing scope boundary as every other provider-specific script in this project (`peloton_smart_sync.py`, `run_dedup_backfill.py`'s eventual real-data run). Fixture coverage (above) is the full extent of what this pipeline can verify; the BO running it against the real 136 rows is explicitly out of scope, per the requirements doc.
- **`sport_type_raw` duplicates `discipline_raw`'s value for Strava/Strava-unofficial** (same fallback chain, just persisted under a new name instead of only used transiently). This is intentional, not an oversight — `discipline_raw` was never persisted to `normalized_activities` before this issue, and inventing a different value for `sport_type_raw` than what `map_discipline()` already consumes would create two sources of truth for the same underlying fact.
