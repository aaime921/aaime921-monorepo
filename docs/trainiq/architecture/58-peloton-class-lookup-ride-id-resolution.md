# Architecture: Fix Peloton Class Lookup — Two-Step `peloton_id` → `ride_id` Resolution (resolves BL-011)

**Issue:** #58
**Requirements:** [`docs/trainiq/requirements/58-peloton-class-lookup-ride-id-resolution.md`](../requirements/58-peloton-class-lookup-ride-id-resolution.md)
**Related:** #46 / PR #55 (introduced the broken single-call lookup this
issue replaces), BL-011 (resolved by this design), #47 (shares the
per-workout fetch pattern; not touched here — see "Out of scope")

## Evidence status

Unlike #46's design, this issue rests on **live-verified evidence, not an
assumption** — the requirements doc quotes the BO's own read-only live
testing (2026-10-09, ride 2026-10-07) against all three endpoints involved,
including the exact 404s and the exact success-shape keys for both the
session object (`GET /api/peloton/{peloton_id}`) and the ride-details object
(`GET /api/ride/{ride_id}/details`). No further live-verification task is
needed before implementation — every constant this design proposes is taken
directly from that evidence table, not guessed. The one thing still
genuinely undetermined by evidence (how to represent multiple
`class_types[].name` entries on disk) is explicitly left to this doc per the
requirements doc's own framing, and is decided below under "Field mapping."

## Approach

### The bug, restated precisely

`fetch_class_details(ride_id)` (`connectors/peloton.py:571`) and its caller
in `download()` are themselves fine — `GET /api/ride/{ride_id}/details`
genuinely returns the right data when given a real `ride_id`. The defect is
one level up: `download()` passes `workout.get(RIDE_ID_FIELD)` i.e.
`workout["peloton_id"]` straight into `fetch_class_details()` as if it were
already a `ride_id`. It isn't — `peloton_id` is a **class session id**,
unique per attendance, and `GET /api/ride/{peloton_id}/details` 404s on it
every time. The fix inserts a new resolution step before the existing call,
not a rewrite of the existing call.

### Two-step lookup, one new endpoint

```
peloton_id (workout's session id)
   │
   ▼  GET /api/peloton/{peloton_id}          [NEW — step 1]
session object { ride_id, scheduled_start_time, is_live, is_encore, ... }
   │
   ▼  extract ride_id
   │
   ▼  GET /api/ride/{ride_id}/details        [EXISTING — step 2, now called correctly]
ride/class object { ride: { title, duration, difficulty_estimate,
                             instructor: { name }, ... },
                     class_types: [ { name }, ... ] }
```

A 404 at **either** step is a failed lookup for that workout — `class_type`
is set to the existing `CLASS_TYPE_LOOKUP_FAILED` sentinel, never a partial
result (AC4). The two steps are logged distinctly on failure (session
resolution vs. ride-details fetch) so a future investigation can tell which
endpoint actually failed, without changing the stored sentinel value itself
(there is still exactly one failure sentinel — the requirements doc doesn't
ask for two, only for the two cases to be *testable* as distinct, which is a
test-fixture concern, not a schema one).

### Field mapping (supersedes #46's UNCONFIRMED mapping)

Live-verified top-level shape of the step-2 response: a nested `ride` object
holds most fields, but `class_types` is a **top-level** sibling of `ride`,
not nested under it (the issue body's evidence table is explicit on this:
`ride.instructor.name` and `ride.difficulty_estimate` are under `ride.*`,
while `class_types[].name` and `is_power_zone_class` are listed at the top
level). This replaces #46's incorrect assumption that the title/duration/
instructor fields sat at the response's top level with no `ride` wrapper.

| Stored column | Source | Notes |
|---|---|---|
| `activity_title` | `ride.title` | |
| `instructor_name` | `ride.instructor.name` | `ride.instructor_id` exists too but is not stored — no requirement calls for it, and it's redundant with the name for every current consumer (AC5-style queries filter on `instructor_name`) |
| `class_type` | `class_types[].name`, comma-joined | **Architect decision** (requirements doc defers this): join multiple names with `", "` (e.g. a class with two tags becomes `"Power Zone, Tabata"`). Chosen over "first value only" because dropping a second tag silently would make an `AC5`-style filter query (`WHERE class_type = 'Power Zone'`) miss classes that are *also* tagged something else, which is worse than an occasional multi-value string. Chosen over a schema change (e.g. a join table) because nothing downstream of this column does anything but equality/substring filtering on it today (same reasoning #46's design already gave for storing it as a raw, unmapped string) — a join table would be new infrastructure with no consumer. **Empty list → stored as `""`, not `None`** — see "The None-means-not-attempted contract" below for why this distinction is load-bearing, not cosmetic. |
| `planned_duration_s` | `ride.duration` | unchanged from #46's intent, just read from the right (nested) place |
| `provider_class_id` | the **resolved** `ride_id` (step 1's output) | behavior change from #46: previously this was set to the workout's own `peloton_id`, which is per-session and would never let two attendances of the same class share a `provider_class_id`. Now it's the real, stable ride id. |
| `difficulty_estimate` (**new column**) | `ride.difficulty_estimate` | **Architect decision** (requirements doc: "the BO specifically suggested this... whether to add a new column... is the Architect's call"): add it. The ride-details response is already being fetched for every successful lookup, so this is a zero-marginal-cost column exactly as the BO argued, and `REAL`/nullable fits the existing all-nullable enrichment-column pattern with no new complexity. |

`duration_s` (the workout's *actual* recorded duration, computed from
`start_time`/`end_time` in `normalize()`) is untouched — `planned_duration_s`
is a separate, already-existing column from #46 and this issue does not
change the distinction between the two.

### The "`None` means not attempted" contract, and why an empty `class_types` needs its own sentinel

#46's COALESCE-based upsert (`sync/engine.py`) relies on a specific
invariant for all Peloton-enrichment columns: `None` from `normalize()`
means "this pass didn't attempt a lookup for this workout at all" (predates
the feature, or skipped as older than the sync checkpoint) — distinct from
"attempted, and here is the real (possibly falsy) result." A genuinely
successful lookup must therefore never produce `None` for `class_type`,
even when the ride happens to have zero `class_types` entries (plausible —
not every Peloton ride is tagged). This design stores `""` (empty string)
for that specific case, never `None`, so the COALESCE-preservation behavior
(`COALESCE(?, existing_column)`) can't mistake "resolved, this ride has no
tags" for "not attempted this pass, keep whatever was there before." This
is the one subtlety in field mapping that isn't just "read a different
response key" — it's called out explicitly because it is exactly the kind
of edge case a COALESCE-based correctness mechanism silently breaks on if
missed, and #46's own design doc flagged this general class of hazard as
something a reviewer must catch, not something the static test suite can.

## Affected components/files

**Changed:**
- `trainiq/connectors/peloton.py` — new `fetch_class_session()`, new field
  constants for the step-1 session shape and the corrected step-2 shape,
  new shared pure field-extraction function, `download()`'s two-step
  resolution + two-cache logic, `normalize()`'s new `difficulty_estimate`
  key. Removes the dead `RIDE_DETAIL_JOINS_PARAM` constant and
  `_extract_instructor_name()` helper (both specific to #46's abandoned
  "joins-on-list" plan (a) / old flat-shape assumption; superseded, not
  reusable under the corrected nested shape).
- `trainiq/storage/schema.py` — migration v7: one new nullable column,
  `difficulty_estimate REAL`, on `normalized_activities`.
- `trainiq/sync/engine.py` — `upsert_normalized_activity()`'s column lists
  gain `difficulty_estimate` as a 7th COALESCE-on-update enrichment column,
  same treatment as the existing 6.
- `trainiq/normalization/engine.py` — `_build_activity_record()` reads the
  new `difficulty_estimate` key (one line, same pattern as the existing 6).
- `scripts/backfill_peloton_class_metadata.py` — `_process_row()` and
  `run_backfill()` updated for the two-step lookup with two caches shared
  across the whole run (see "Caching" below); import list updated to match
  the new/renamed constants and helper in `peloton.py`.
- `projects/trainiq/BACKLOG.md` — BL-011 marked resolved (Developer task,
  see "Task breakdown").

**Unchanged:**
- The 6 existing enrichment columns' COALESCE-upsert mechanics, the
  `CLASS_TYPE_NOT_A_CLASS`/`CLASS_TYPE_LOOKUP_FAILED` sentinels themselves,
  `_is_class_workout()` (still correctly keyed on `workout_type`/presence of
  `peloton_id` — that field's role as "this workout has a class session
  attached" doesn't change, only what you do with its value once you have
  it), the skip-if-already-synced-since-checkpoint optimization, Strava/
  Strava-unofficial's `activity_title`/`sport_type_raw` handling,
  `trainiq/dedup/`, `trainiq/normalization/renormalize.py`,
  `tests/test_architecture_invariants.py` (no new write path —
  `apply_class_metadata_update()` already existed and is reused unchanged).

## Interfaces/contracts

```python
# trainiq/connectors/peloton.py

# Issue #58 (resolves BL-011): live-verified against the BO's real account,
# 2026-10-09. A workout's `peloton_id` is a class SESSION id, not a ride
# id — GET /api/ride/{peloton_id}/details and GET /api/ride/{peloton_id}
# both 404 on it. It must first be resolved via this endpoint.
SESSION_ENDPOINT_TEMPLATE = "/api/peloton/{peloton_id}"
SESSION_RIDE_ID_FIELD = "ride_id"

# Called with the RESOLVED ride_id from step 1 — never with peloton_id
# directly. Endpoint path itself is unchanged from #46; only the caller's
# argument changed.
RIDE_DETAIL_ENDPOINT_TEMPLATE = "/api/ride/{ride_id}/details"

# Live-verified response shape (issue #58) — supersedes #46's UNCONFIRMED
# flat-shape guess. `class_types` is a TOP-LEVEL sibling of `ride`, not
# nested under it.
RIDE_OBJECT_FIELD = "ride"
CLASS_TITLE_FIELD = "title"               # ride.title
INSTRUCTOR_OBJECT_FIELD = "instructor"    # ride.instructor
INSTRUCTOR_NAME_FIELD = "name"            # ride.instructor.name
PLANNED_DURATION_FIELD = "duration"       # ride.duration, seconds
DIFFICULTY_ESTIMATE_FIELD = "difficulty_estimate"  # ride.difficulty_estimate
CLASS_TYPES_FIELD = "class_types"         # top-level list of {name: str}
CLASS_TYPE_NAME_FIELD = "name"


def fetch_class_session(self, peloton_id: str) -> dict[str, Any] | None:
    """Step 1 of the two-step class-detail lookup. GET
    /api/peloton/{peloton_id} resolves a workout's session id to the real
    ride_id the class repeats under. Returns None on a confirmed 404 (never
    raised as PelotonHTTPError for that case) — same contract
    fetch_class_details() already uses. Raises TransientError for 429/5xx
    (ADR-037, honors Retry-After) and AuthenticationError for 401/403,
    exactly like every other authenticated_get call in this connector."""


def _extract_ride_metadata(details: dict[str, Any], ride_id: str) -> dict[str, Any]:
    """Pure mapping from a successful GET /api/ride/{ride_id}/details body
    to the 6 canonical fields. class_type is the comma-joined
    class_types[].name values — "" (not None) when the list is empty; see
    "The None-means-not-attempted contract" in the design doc for why that
    distinction matters. Shared by download() and the backfill script so
    the field-mapping logic exists in exactly one place; caching and retry
    policy stay call-site-specific (see "Caching" below), same division of
    responsibility #46 already established between download() and the
    backfill script."""
    return {
        "activity_title": ...,      # ride.get(CLASS_TITLE_FIELD)
        "instructor_name": ...,     # ride.get(INSTRUCTOR_OBJECT_FIELD, {}).get(INSTRUCTOR_NAME_FIELD)
        "class_type": ...,          # ", ".join(name for each class_types[] entry with a name)
        "planned_duration_s": ...,  # ride.get(PLANNED_DURATION_FIELD)
        "provider_class_id": ride_id,
        "difficulty_estimate": ...,  # ride.get(DIFFICULTY_ESTIMATE_FIELD)
    }
```

`download()`'s per-workout resolution (replaces the single
`fetch_class_details(ride_id=workout.get(RIDE_ID_FIELD))` call):

```python
session_ride_id_cache: dict[str, str | None] = {}   # peloton_id -> resolved ride_id, or None (session lookup failed)
ride_details_cache: dict[str, dict | None] = {}      # ride_id -> details body, or None (details lookup failed)

for workout in workouts:
    if not _is_class_workout(workout):
        workout["_class_type"] = CLASS_TYPE_NOT_A_CLASS
        continue
    # ... existing since-checkpoint skip, unchanged ...
    peloton_id = workout.get(RIDE_ID_FIELD)

    if peloton_id not in session_ride_id_cache:
        session = self.fetch_class_session(peloton_id)
        session_ride_id_cache[peloton_id] = session.get(SESSION_RIDE_ID_FIELD) if session is not None else None
    ride_id = session_ride_id_cache[peloton_id]
    if ride_id is None:
        workout["_class_type"] = CLASS_TYPE_LOOKUP_FAILED
        diagnostic_logger().warning(f"{PROVIDER}: class session lookup failed for peloton_id={peloton_id!r}")
        continue

    if ride_id not in ride_details_cache:
        ride_details_cache[ride_id] = self.fetch_class_details(ride_id)
    details = ride_details_cache[ride_id]
    if details is None:
        workout["_class_type"] = CLASS_TYPE_LOOKUP_FAILED
        diagnostic_logger().warning(f"{PROVIDER}: ride-details lookup failed for ride_id={ride_id!r} (peloton_id={peloton_id!r})")
        continue

    fields = _extract_ride_metadata(details, ride_id)
    workout["_class_title"] = fields["activity_title"]
    workout["_instructor_name"] = fields["instructor_name"]
    workout["_class_type"] = fields["class_type"]
    workout["_planned_duration_s"] = fields["planned_duration_s"]
    workout["_provider_class_id"] = fields["provider_class_id"]
    workout["_difficulty_estimate"] = fields["difficulty_estimate"]
```

`normalize()` gains one line: `"difficulty_estimate": raw.get("_difficulty_estimate")`.

### Caching (AC3)

Two independent in-memory caches, both scoped to one `download()` call (or
one backfill run — see below), mirroring exactly the two steps:

- `session_ride_id_cache`, keyed by `peloton_id`: at most one
  `GET /api/peloton/{peloton_id}` call per distinct `peloton_id` seen this
  run.
- `ride_details_cache`, keyed by the **resolved** `ride_id`: at most one
  `GET /api/ride/{ride_id}/details` call per distinct `ride_id` — this is
  what makes two different `peloton_id`s (two attendances of the same
  class) still cost only one ride-details call, which is the actual
  requirement AC3 cares about (the BO's "took the same class twice"
  scenario), not just literal `peloton_id` repetition.

Net cost per workout this run: 0 calls (non-class, or already-synced before
the checkpoint), 1 call (session id seen before but this is a new
`peloton_id` resolving to an already-seen `ride_id`), or 2 calls (both
`peloton_id` and resolved `ride_id` are new this run) — matching the
requirements doc's "up to two calls" framing exactly.

### Backfill script (`scripts/backfill_peloton_class_metadata.py`)

`run_backfill()` creates both caches **once per invocation** (not per row)
and threads them through every `_process_row()` call, so the same AC3
cross-workout caching applies across the whole backfill run, not just
within `download()`. This is a real behavior change from #46's backfill
script, which had no caching at all (each row called `fetch_class_details()`
independently) — worth calling out because without it, the BO's 131-row
backfill would cost up to 262 calls even when many of those 131 rows share
the same class, defeating AC3's intent for exactly the dataset this issue
exists to fix.

Each of the two network calls (`fetch_class_session`, `fetch_class_details`)
is individually wrapped in `retry_with_backoff()` (ADR-037, unchanged from
#46's existing per-call wrapping of `fetch_class_details`) — not the
composite resolution, so a transient failure on one step doesn't force a
retry of a step that already succeeded and was cached.

```python
def _process_row(conn, connector, row, session_ride_id_cache, ride_details_cache, max_retries, sleep_fn) -> str:
    raw = json.loads(row["payload_json"])
    external_id = row["external_id"]

    if not _is_class_workout(raw):
        apply_class_metadata_update(conn, external_id, {"class_type": CLASS_TYPE_NOT_A_CLASS})
        conn.commit()
        return "not_a_class"

    peloton_id = raw.get(RIDE_ID_FIELD)
    if peloton_id not in session_ride_id_cache:
        session = retry_with_backoff(
            lambda: connector.fetch_class_session(peloton_id), PROVIDER, max_retries=max_retries, sleep_fn=sleep_fn
        )
        session_ride_id_cache[peloton_id] = session.get(SESSION_RIDE_ID_FIELD) if session is not None else None
    ride_id = session_ride_id_cache[peloton_id]
    if ride_id is None:
        diagnostic_logger().warning(f"{PROVIDER}: class session lookup failed for peloton_id={peloton_id!r}")
        apply_class_metadata_update(conn, external_id, {"class_type": CLASS_TYPE_LOOKUP_FAILED})
        conn.commit()
        return "failed"

    if ride_id not in ride_details_cache:
        ride_details_cache[ride_id] = retry_with_backoff(
            lambda: connector.fetch_class_details(ride_id), PROVIDER, max_retries=max_retries, sleep_fn=sleep_fn
        )
    details = ride_details_cache[ride_id]
    if details is None:
        diagnostic_logger().warning(f"{PROVIDER}: ride-details lookup failed for ride_id={ride_id!r} (peloton_id={peloton_id!r})")
        apply_class_metadata_update(conn, external_id, {"class_type": CLASS_TYPE_LOOKUP_FAILED})
        conn.commit()
        return "failed"

    apply_class_metadata_update(conn, external_id, _extract_ride_metadata(details, ride_id))
    conn.commit()
    return "success"
```

`run_backfill()`'s only change: instantiate `session_ride_id_cache = {}` and
`ride_details_cache = {}` once, pass both into every `_process_row()` call
in its loop. `TransientError`/`AuthenticationError` with retries exhausted
still propagate out of `_process_row()` uncaught, same as #46 — a crash
here is correct (AC6-equivalent resumability): every row already committed
stays committed, and the caches are rebuilt fresh (cheap, correctness-only
cost) on the next invocation.

### Schema migration v7

```sql
ALTER TABLE normalized_activities ADD COLUMN difficulty_estimate REAL;
```

One new nullable column, additive, same pattern as #46's v6 migration.
`CURRENT_SCHEMA_VERSION` becomes 7.

### Upsert (`sync/engine.py`)

`difficulty_estimate` joins the existing 6 as a 7th `.get(...)`-read,
COALESCE-on-update column in `upsert_normalized_activity()` — same
treatment, same reasoning (a genuine "lookup succeeded with this value" vs.
"not attempted this pass" distinction that must survive a
`renormalize_provider()` re-run with zero new network I/O).

## Task breakdown

1. `trainiq/connectors/peloton.py`: add `SESSION_ENDPOINT_TEMPLATE`,
   `SESSION_RIDE_ID_FIELD`; replace the flat-shape field constants with the
   nested-shape ones (`RIDE_OBJECT_FIELD`, `CLASS_TYPES_FIELD`,
   `CLASS_TYPE_NAME_FIELD`, `DIFFICULTY_ESTIMATE_FIELD`); remove
   `RIDE_DETAIL_JOINS_PARAM` and `_extract_instructor_name()`; add
   `fetch_class_session()` and `_extract_ride_metadata()`; rewrite
   `download()`'s class-resolution loop per "Interfaces/contracts" above;
   add `difficulty_estimate` to `normalize()`'s output.
2. `trainiq/storage/schema.py`: migration v7 (`difficulty_estimate REAL`);
   bump `CURRENT_SCHEMA_VERSION` to 7.
3. `trainiq/sync/engine.py`: add `difficulty_estimate` to
   `upsert_normalized_activity()`'s INSERT column list and as a 7th
   COALESCE column in the UPDATE branch.
4. `trainiq/normalization/engine.py`: `_build_activity_record()` reads
   `difficulty_estimate` from the normalized dict.
5. `scripts/backfill_peloton_class_metadata.py`: update imports for the
   renamed/new constants and helper; rewrite `_process_row()` and
   `run_backfill()` per "Backfill script" above (two shared caches,
   per-call retry wrapping, two distinct failure log lines).
6. Tests (see below).
7. Mark `BL-011` resolved in `projects/trainiq/BACKLOG.md`: state plainly
   that the two-step resolution is now live-verified (not a remaining open
   question) and link to this issue/doc. Leave `BL-010` (distance unit,
   issue #45) untouched — unrelated backlog item.
8. Run the full existing test suite; confirm zero regressions, in
   particular `tests/test_peloton_connector.py`,
   `tests/test_backfill_peloton_class_metadata.py`,
   `tests/test_sync_engine.py`, and `tests/test_architecture_invariants.py`.

## Test strategy notes

All fixture-based, no live Peloton access from CI (Testing scope boundary
— unchanged from every other provider test in this project).

`tests/test_peloton_connector.py`:
- **Regression test for the old bug:** a fake session where
  `GET /api/ride/{peloton_id}/details` (called with the session id,
  mistakenly, as the old code did) returns 404 — proves the *old* call
  pattern would fail, documenting why step 1 exists. (This can be a direct
  unit test of the fake session's behavior, or a comment-anchored assertion
  inside the full-resolution test below; either way, must exist and must be
  traceable to this specific regression.)
- `fetch_class_session()` success: fake 200 response with the live-evidence
  session shape (`ride_id`, `scheduled_start_time`, `is_live`,
  `is_encore`) → returns the parsed dict; `ride_id` extracted correctly.
- `fetch_class_session()` 404 → returns `None`.
- `fetch_class_details()` success: fake 200 response with the live-evidence
  nested shape (`ride.title`, `ride.duration`, `ride.difficulty_estimate`,
  `ride.instructor.name`, `ride.instructor_id`, `ride.class_type_ids`,
  top-level `class_types: [{"name": "Power Zone"}]`, `is_power_zone_class`)
  → `_extract_ride_metadata()` returns all 6 fields correctly, including
  `class_type == "Power Zone"`.
- `fetch_class_details()` 404 on an otherwise-valid resolved `ride_id` →
  returns `None` — distinguishable in a separate test from the
  session-resolution 404 above (two different failure points, per AC8).
- `_extract_ride_metadata()` with multiple `class_types` entries → comma-
  joined string, in the documented order.
- `_extract_ride_metadata()` with an empty `class_types` list → `class_type
  == ""`, not `None` (the COALESCE-contract edge case — see "Field
  mapping" above; this is the test that would catch a future regression
  back to returning `None` here).
- `download()`: two workouts with different `peloton_id`s that resolve to
  the same `ride_id` → `fetch_class_session()` called twice (different
  session ids), `fetch_class_details()` called exactly once (cache hit on
  the resolved `ride_id`); both workouts get the same class metadata.
- `download()`: same `peloton_id` seen twice (e.g. re-synced within one
  call, if that's ever possible given upstream dedup — otherwise this
  reduces to "cache populated, second lookup of the identical id is a
  trivial hit") → `fetch_class_session()` called once.
- `download()`: session resolution fails (404) → workout's `class_type ==
  "lookup_failed"`, no attempt to call `fetch_class_details()` for it (step
  2 never runs without a resolved `ride_id`), warning logged mentioning
  `peloton_id`.
- `download()`: session resolves but ride-details fetch fails (404) →
  `class_type == "lookup_failed"`, warning logged mentioning both
  `ride_id` and `peloton_id`.
- `normalize()`: a workout with `_difficulty_estimate` set → passes through
  to `difficulty_estimate` in the normalized dict; a workout with no
  `_difficulty_estimate` key → `None` (not-attempted case, unchanged
  COALESCE contract).

`tests/test_backfill_peloton_class_metadata.py`:
- **Successful run on fixtures shaped like the BO's 131 failed rows:** seed
  `normalized_activities` with rows at `class_type = 'lookup_failed'`
  (simulating the pre-fix state) plus their original raw payloads in
  `raw_activities`; run with `--retry-failed` against a fake connector
  returning the live-evidence shapes; assert every row resolves to real
  class metadata, `difficulty_estimate` included.
- **Cross-row caching:** multiple candidate rows sharing either the same
  `peloton_id` or the same resolved `ride_id` → assert
  `fetch_class_session`/`fetch_class_details` call counts reflect the
  caching contract (at most one call per distinct id, across the *whole*
  run, not just within one row).
- **Two distinct failure points:** one row whose session resolution 404s,
  a separate row whose session resolves but whose ride-details fetch
  404s → both end at `class_type = 'lookup_failed'`, but asserted via two
  separate test cases with distinguishable fake-connector setups (per
  AC8).
- **Rate-limit retry on each step independently:** fake connector's
  `fetch_class_session()` raises `TransientError` once then succeeds (and,
  in a separate case, `fetch_class_details()` does) → row ends up
  correctly populated either way, fake `sleep_fn` called with the expected
  delay.
- Existing `--retry-failed`/resume/`not_a_class` tests from #46 continue to
  pass with updated fixtures (they exercise code paths this issue doesn't
  change, but the shared cache parameters now threading through
  `_process_row()`'s signature mean every existing call site in the test
  file needs updating, not just new tests added).

`tests/test_sync_engine.py` / upsert tests: extend the existing COALESCE
tests (insert-with-value, update-preserves-on-None, update-overwrites-on-
value) to cover `difficulty_estimate` as the 7th column, same pattern as
the existing 6.

Manual/BO-run verification (not CI, out of scope per the requirements doc):
actually running `--retry-failed` against the BO's real 131 rows.

## Risks/tradeoffs

- **`class_type`'s comma-joined representation is a step away from being a
  clean filterable value** for the (currently believed rare, not yet
  observed) case of a ride tagged with multiple `class_types`. A query
  like `WHERE class_type = 'Power Zone'` will miss a ride stored as
  `"Power Zone, Tabata"`. This is an accepted tradeoff per "Field mapping"
  above (no join-table consumer exists today), but flagged explicitly so a
  future AC5-style query author knows to use `LIKE '%Power Zone%'` or split
  on `", "` rather than assuming exact equality always works. If multi-tag
  rides turn out to be common, revisit as a dedicated schema change then,
  with real usage data instead of a hypothetical.
- **The empty-`class_types`-list `""` sentinel is a subtle contract**, not
  self-evident from the column's type alone — flagged prominently above
  ("The None-means-not-attempted contract") specifically because it's the
  kind of thing a future edit could silently regress (e.g. "simplifying"
  `_extract_ride_metadata()` to `class_types[0].get("name")` or similar
  would reintroduce exactly this hazard under a different guise). Worth a
  reviewer's explicit attention at implementation time.
- **Backfill is still genuinely network-bound and cannot be exercised live
  in CI** — same boundary as #46 and every other provider script in this
  project. Fixture coverage is the full extent of what this pipeline
  verifies; the BO running `--retry-failed` against the real 131 rows
  remains an ops task, out of scope here per the requirements doc.
- **Doubling the per-uncached-workout call count** (one session-resolution
  call plus one ride-details call, vs. #46's single — broken — call) was
  already anticipated and explicitly accepted in the requirements doc
  ("Rate limiting... this issue doubles per-uncached-workout call volume, it
  doesn't change the retry/backoff contract"); no new rate-limit mitigation
  beyond the existing per-call ADR-037 handling and the two-level caching
  above is introduced, since none was asked for and the caching already
  bounds the growth to "at most 2x," not "doubles forever on repeat syncs"
  (the existing skip-if-already-synced-since-checkpoint optimization from
  #46 still applies unchanged on top of this).
