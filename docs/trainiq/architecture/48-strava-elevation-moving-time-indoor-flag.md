# Architecture: Keep Elevation Gain, Moving Time and Indoor/Outdoor Flag (Strava)

**Issue:** #48
**Requirements:** [`docs/trainiq/requirements/48-strava-elevation-moving-time-indoor-flag.md`](../requirements/48-strava-elevation-moving-time-indoor-flag.md)
**Related:** [`docs/trainiq/architecture/30-strava-unofficial-web-endpoints.md`](30-strava-unofficial-web-endpoints.md) (web endpoint field names), [`docs/trainiq/architecture/36-strava-unofficial-discipline-mapping.md`](36-strava-unofficial-discipline-mapping.md) (the `renormalize_provider()` / `upsert_normalized_activity()` machinery this issue reuses verbatim)

## Approach

Three canonical fields — `elevation_gain_m`, `moving_time_s`, `is_indoor` —
flow through the same four layers every other canonical activity field
already uses (connector raw extraction → `normalize()` → `build_canonical_record()`
→ `normalized_activities`). **The one piece of real, new work is that
`normalized_activities` has no columns for them today** — confirmed by
reading `trainiq/storage/schema.py`: migration 1's `normalized_activities`
DDL has exactly the columns `_build_activity_record()` already returns, and
nothing named `elevation_gain_m`/`moving_time_s`/`is_indoor` appears
anywhere in the schema. This is a schema migration, not just a mapping
change — the requirements doc doesn't say so explicitly (it's written from
the connector side), but it's the load-bearing fact this design has to get
right first.

**Four changes, in dependency order:**

1. **Schema migration (new).** Add migration 6: three new nullable columns
   on `normalized_activities`. No `NOT NULL DEFAULT`, unlike e.g. migration
   4's `is_flagged_implausible INTEGER NOT NULL DEFAULT 0` — those columns
   have a real "unknown things are implausible until evaluated" default;
   these three don't. `is_indoor` in particular must default to SQL
   `NULL`, not `0`/`False` — AC3 is explicit that "missing" and
   "confirmed-outdoor" are different facts, and `ADD COLUMN ... INTEGER NOT
   NULL DEFAULT 0` would make every pre-migration row (and every future row
   where `trainer` is genuinely absent) silently say "confirmed outdoor."
   Plain `ADD COLUMN` with no `NOT NULL`/`DEFAULT` clause backfills existing
   rows with `NULL`, which is the only correct value for them anyway (their
   raw payloads predate this fix either way).

2. **`strava_unofficial` connector — `normalize()`.** Add the three fields,
   reading `elevation_gain_raw`, `moving_time_raw`, `trainer` with `.get()`
   (never `[...]`, so a missing key is `None`, not a `KeyError`) — same
   defensive style the requirements doc's "NULL on missing data" rule
   requires and the existing `distance_m` line already models
   (`raw.get("distance_raw")`).

3. **Official `strava` connector — both `_activity_to_raw_dict()` *and*
   `normalize()`.** The requirements doc flags this as the easy-to-miss
   part and it's confirmed by reading the code: `_activity_to_raw_dict()`
   is what actually gets persisted to `raw_activities`; today it doesn't
   capture `total_elevation_gain`, `moving_time`, or `trainer` at all, so
   fixing only `normalize()` would silently normalize from fields that were
   never stored. Both methods need the three fields added.

4. **`build_canonical_record()` / `upsert_normalized_activity()`.** The
   normalized-dict → canonical-record → SQL path is provider-agnostic — one
   change in `_build_activity_record()` and one in `upsert_normalized_activity()`
   covers both connectors. No connector-specific logic belongs here; this
   is exactly the kind of pass-through `distance_m`/`avg_hr`/etc. already
   demonstrate.

**Backfill reuses issue #36's machinery unmodified — no new backfill code
for `strava_unofficial`.** `renormalize_provider()` and
`scripts/renormalize_strava_unofficial.py` already do exactly "re-derive
`normalized_activities` from already-stored `raw_activities` via
`connector.normalize()` + `build_canonical_record()`," which is precisely
what AC4 asks for. Nothing about this issue's change requires touching
either file — running the existing script after this ships re-populates the
three new columns for all 352 rows, because `strava_unofficial`'s raw
payloads already contain `elevation_gain_raw`/`moving_time_raw`/`trainer`
(confirmed in the existing test fixtures — see Affected components).

**Official-`strava` backfill is a documented gap, not a built feature —
confirmed against the actual flow, not assumed.** `renormalize_provider()`
is already fully generic (`provider` + any `Connector`), so *mechanically*
nothing new is needed to re-normalize official-`strava` rows once their
`raw_activities` payloads contain the new fields. The problem is upstream:
today's `_activity_to_raw_dict()` never wrote `total_elevation_gain`,
`moving_time`, or `trainer` into `raw_activities`, so **every official-`strava`
row synced before this fix lacks this data in its stored payload** —
re-normalization cannot manufacture data that was never persisted. Only a
fresh `download()`/sync (which calls the *fixed* `_activity_to_raw_dict()`,
re-fetching from Strava) populates `raw_activities` with the new fields for
those rows; re-normalization only becomes useful for official `strava`
*after* that fresh sync. Per this project's live-verification boundary
(no live Strava account in CI/this pipeline — see `docs/trainiq/roles/technical-architect.md`'s
"Testing scope boundaries"), actually running that fresh sync is BO-side,
not a pipeline deliverable (same treatment the requirements doc's "Out of
scope" already gives it). This design does not add a
`scripts/renormalize_strava.py` wrapper: it would just be
`renormalize_provider(conn, "strava", StravaConnector(...))` with no new
logic, and writing it now, before any post-fix `raw_activities` rows exist
to re-normalize, would be a script with nothing to run against — the
Developer's handoff/BACKLOG note (Task 9 below) should record the one-line
shape so wiring it up later (after a BO fresh sync) is trivial, without
building and shipping unused tooling today.

## Affected components/files

**Modified:**
- `projects/trainiq/trainiq/storage/schema.py` — new migration `6`:
  ```sql
  ALTER TABLE normalized_activities ADD COLUMN elevation_gain_m REAL;
  ALTER TABLE normalized_activities ADD COLUMN moving_time_s INTEGER;
  ALTER TABLE normalized_activities ADD COLUMN is_indoor INTEGER;
  ```
  `CURRENT_SCHEMA_VERSION` bumped `5` → `6`. No backfill function needed in
  `migrate()`'s `if version == N:` chain (unlike migrations 4/5) — there's
  no existing data to transform, only new nullable columns on existing rows,
  which `ADD COLUMN` already fills with `NULL` for free.
- `projects/trainiq/trainiq/connectors/strava_unofficial.py` — `normalize()`
  only (confirmed by reading the file: `elevation_gain_raw`/`moving_time_raw`/
  `trainer` already arrive in `raw` unchanged today, since this connector
  stores the full web-endpoint payload verbatim into `raw_activities` — see
  `download()`/the module docstring's "issue #30" note; no `download()`
  change needed, unlike the official connector).
- `projects/trainiq/trainiq/connectors/strava.py` — both
  `_activity_to_raw_dict()` (capture) and `normalize()` (map).
- `projects/trainiq/trainiq/normalization/engine.py` — `_build_activity_record()`:
  three new pass-through keys.
- `projects/trainiq/trainiq/sync/engine.py` — `upsert_normalized_activity()`:
  extend both the `INSERT OR IGNORE` and `UPDATE` column lists/params.
- `projects/trainiq/tests/test_strava_unofficial_connector.py` — existing
  fixtures (e.g. the one at line ~542, already carrying `moving_time_raw`/
  `elevation_gain_raw` with no `trainer` key) need a `trainer` key added
  where the test wants to exercise it, plus new assertions per Test
  strategy notes.
- `projects/trainiq/tests/test_strava_connector.py` — `_fake_activity()`
  helper needs `total_elevation_gain`/`moving_time`/`trainer` params (with
  defaults matching today's implicit "ride, outdoor, has some elevation"
  shape, so every existing call site that doesn't care about these fields
  keeps working unchanged).
- `projects/trainiq/tests/test_storage.py` — add/extend a migration test
  for version 6 (schema-version assertions already follow
  `schema.CURRENT_SCHEMA_VERSION`, so most existing assertions need no
  change — only a dedicated new-columns-are-NULL-after-migration test is
  new).
- `projects/trainiq/tests/test_renormalize.py` — extend the existing
  `strava_unofficial` renormalization fixtures (already present at line
  ~78 and ~295) to also assert the three new columns survive a
  renormalize pass, since `build_canonical_record()`'s output now includes
  them.

**Not touched:** `trainiq/connectors/base.py` (no contract change — still a
plain dict, no new abstract fields), `trainiq/normalization/taxonomy.py`,
`trainiq/normalization/confidence.py` (see Risks/tradeoffs — deliberately
not touched), `trainiq/normalization/load.py`, `trainiq/connectors/peloton.py`
(not in scope — these fields aren't part of this issue's Peloton surface),
`trainiq/dedup/*`, `scripts/renormalize_strava_unofficial.py` (reused
as-is), `trainiq/app.py`, `trainiq/setup_wizard.py`.

## Interfaces/contracts

```python
# --- trainiq/storage/schema.py -------------------------------------------

CURRENT_SCHEMA_VERSION = 6

_MIGRATIONS: dict[int, str] = {
    # ... 1-5 unchanged ...
    # Issue #48: elevation gain, moving time, indoor/outdoor flag for
    # Strava activities. Nullable, no default — "missing" and "confirmed
    # outdoor/zero-elevation" are different facts (AC3); ADD COLUMN with
    # no NOT NULL/DEFAULT backfills existing rows with NULL, which is
    # correct for them regardless (their raw payloads predate this fix).
    6: """
        ALTER TABLE normalized_activities ADD COLUMN elevation_gain_m REAL;
        ALTER TABLE normalized_activities ADD COLUMN moving_time_s INTEGER;
        ALTER TABLE normalized_activities ADD COLUMN is_indoor INTEGER;
    """,
}

# --- trainiq/connectors/strava_unofficial.py: normalize() ----------------

def normalize(self, raw: dict[str, Any]) -> dict[str, Any]:
    return {
        # ... unchanged existing keys ...
        "distance_m": raw.get("distance_raw"),
        "calories": None,
        # Issue #48. .get() everywhere, never raw[...]: a missing source
        # field must stay None, never fabricated/defaulted (AC3).
        "elevation_gain_m": raw.get("elevation_gain_raw"),
        "moving_time_s": raw.get("moving_time_raw"),
        "is_indoor": raw.get("trainer"),
        "synced_at": datetime.now(timezone.utc).isoformat(),
    }

# --- trainiq/connectors/strava.py: _activity_to_raw_dict() ---------------

@staticmethod
def _activity_to_raw_dict(activity) -> dict[str, Any]:
    return {
        # ... unchanged existing keys ...
        "distance": float(activity.distance) if activity.distance is not None else None,
        # Issue #48: stravalib exposes these on the same SummaryActivity
        # object already in use here — confirmed in requirements doc.
        # Captured here (not just in normalize()) or they're discarded
        # before normalize() ever sees them.
        "total_elevation_gain": (
            float(activity.total_elevation_gain)
            if activity.total_elevation_gain is not None
            else None
        ),
        "moving_time": int(activity.moving_time) if activity.moving_time is not None else None,
        "trainer": activity.trainer,
    }

# --- trainiq/connectors/strava.py: normalize() ----------------------------

def normalize(self, raw: dict[str, Any]) -> dict[str, Any]:
    return {
        # ... unchanged existing keys ...
        "distance_m": raw["distance"],
        "calories": None,
        # Issue #48. raw.get(...), not raw[...]: these three keys are new
        # as of this issue, so any raw_activities row stored before this
        # fix genuinely lacks them — must resolve to None, not KeyError,
        # for renormalize_provider() to be able to re-process old rows at
        # all (an old row's canonical fields just stay NULL, per the
        # Backfill note — not a crash).
        "elevation_gain_m": raw.get("total_elevation_gain"),
        "moving_time_s": raw.get("moving_time"),
        "is_indoor": raw.get("trainer"),
        "synced_at": datetime.now(timezone.utc).isoformat(),
    }

# --- trainiq/normalization/engine.py: _build_activity_record() -----------

def _build_activity_record(
    provider: str, normalized: dict[str, Any], athlete_profile: Optional[AthleteProfile]
) -> dict[str, Any]:
    discipline = map_discipline(provider, normalized.get("discipline_raw"))
    confidence = compute_source_confidence(RecordKind.ACTIVITY, normalized)
    load_result = compute_training_load(normalized, athlete_profile)

    return {
        # ... unchanged existing keys ...
        "calories": normalized.get("calories"),
        # Issue #48: plain pass-through, same pattern as distance_m/avg_hr
        # above — no new derived logic, no taxonomy, no confidence-scoring
        # involvement (see Risks/tradeoffs).
        "elevation_gain_m": normalized.get("elevation_gain_m"),
        "moving_time_s": normalized.get("moving_time_s"),
        "is_indoor": normalized.get("is_indoor"),
        "training_load": load_result.load,
        "training_load_method": load_result.method.value,
        "source_confidence": confidence,
    }

# --- trainiq/sync/engine.py: upsert_normalized_activity() ----------------

def upsert_normalized_activity(conn: sqlite3.Connection, record: dict) -> str:
    cursor = conn.execute(
        """
        INSERT OR IGNORE INTO normalized_activities
            (provider, external_id, start_time, duration_s, discipline, distance_m,
             avg_hr, max_hr, avg_power, max_power, calories,
             elevation_gain_m, moving_time_s, is_indoor,
             training_load, training_load_method, source_confidence)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            record["provider"], record["external_id"], record["start_time"], record["duration_s"],
            record["discipline"], record["distance_m"], record["avg_hr"], record["max_hr"],
            record["avg_power"], record["max_power"], record["calories"],
            record["elevation_gain_m"], record["moving_time_s"], record["is_indoor"],
            record["training_load"], record["training_load_method"], record["source_confidence"],
        ),
    )
    if cursor.rowcount == 1:
        return "inserted"

    conn.execute(
        """
        UPDATE normalized_activities SET
            start_time = ?, duration_s = ?, discipline = ?, distance_m = ?,
            avg_hr = ?, max_hr = ?, avg_power = ?, max_power = ?, calories = ?,
            elevation_gain_m = ?, moving_time_s = ?, is_indoor = ?,
            training_load = ?, training_load_method = ?, source_confidence = ?
        WHERE provider = ? AND external_id = ?
        """,
        (
            record["start_time"], record["duration_s"], record["discipline"], record["distance_m"],
            record["avg_hr"], record["max_hr"], record["avg_power"], record["max_power"],
            record["calories"], record["elevation_gain_m"], record["moving_time_s"], record["is_indoor"],
            record["training_load"], record["training_load_method"],
            record["source_confidence"], record["provider"], record["external_id"],
        ),
    )
    return "updated"
```

`is_indoor` is stored exactly as Python's `bool | None` — sqlite3 already
adapts `True`/`False` to `1`/`0` automatically for every other boolean
column in this schema (e.g. `weigh_ins.is_weight_flagged_implausible`), so
no explicit cast is needed; `None` binds to SQL `NULL` the same way every
other nullable param here already does.

## Task breakdown

1. `schema.py`: add migration `6` (the three `ALTER TABLE ADD COLUMN`
   statements above, no `NOT NULL`/`DEFAULT`); bump `CURRENT_SCHEMA_VERSION`
   to `6`. No entry needed in `migrate()`'s `if version == N:` backfill
   chain.
2. `strava_unofficial.py`: add `elevation_gain_m`/`moving_time_s`/`is_indoor`
   to `normalize()`'s return dict, per the Interfaces/contracts snippet
   above. No change to `download()` or any other method.
3. `strava.py`: add `total_elevation_gain`/`moving_time`/`trainer` capture
   to `_activity_to_raw_dict()`; add the three mapped fields to
   `normalize()`'s return dict.
4. `normalization/engine.py`: add the three pass-through keys to
   `_build_activity_record()`'s returned dict. Do not touch
   `_build_weigh_in_record()` — weigh-ins have no concept of these fields.
5. `sync/engine.py`: extend `upsert_normalized_activity()`'s `INSERT OR
   IGNORE` and `UPDATE` statements and their param tuples with the three
   new columns, per the snippet above.
6. `tests/test_storage.py`: add a migration test asserting that after
   `migrate()` reaches version 6, `normalized_activities` has the three new
   columns and that a pre-existing row (inserted before running the
   migration, mirroring how the existing migration-4/5 tests already set up
   pre-migration rows) reads back `NULL` for all three — not `0`/`False`.
7. `tests/test_strava_connector.py`: extend `_fake_activity()` with
   `total_elevation_gain=100.0, moving_time=3500, trainer=False` defaults
   (chosen so every existing call site keeps its current behavior
   unchanged); add new `normalize()`/`_activity_to_raw_dict()` tests per
   "Test strategy notes" below.
8. `tests/test_strava_unofficial_connector.py`: add a `trainer` key to the
   fixtures that need it; add new tests per "Test strategy notes" below.
9. `tests/test_renormalize.py`: extend the existing `strava_unofficial`
   fixtures/assertions so a renormalize pass is asserted to populate the
   three new columns from already-stored raw payloads (AC4). Add a short
   code comment (not a new script) at the top of the file, or in
   `renormalize_provider()`'s own docstring, noting that re-normalizing
   `strava` (official) the same way requires a fresh sync first for rows
   predating this fix (per the Backfill note) — so a future BO-run
   `renormalize_provider(conn, "strava", StravaConnector(...))` call needs
   no new code, just a thin CLI wrapper mirroring
   `scripts/renormalize_strava_unofficial.py` once that fresh sync has
   happened. This satisfies AC5's documentation requirement without
   building an unused script today.
10. Run the full test suite (`pytest`) — confirm no regressions outside the
    files above; `test_sync_engine.py`'s own `upsert_normalized_activity`
    coverage must still pass with the new columns present (existing test
    records will need the three new keys added wherever they construct a
    canonical record dict by hand, if any do — confirm while running the
    suite).

## Test strategy notes

- **Schema migration:** a dedicated test (new, in `test_storage.py`) that
  migrates a fresh v5 (or earlier) database to v6 with a pre-existing
  `normalized_activities` row, and asserts `elevation_gain_m`,
  `moving_time_s`, `is_indoor` are all `None` on that row afterward — this
  is the direct test of the "no silent `False`/`0` default" requirement
  (AC3) at the schema layer, independent of any connector.
- **`strava_unofficial.normalize()` (AC1, AC3, AC6):**
  - Outdoor case: `elevation_gain_raw` present and non-zero, `trainer:
    False` → `elevation_gain_m` equals it, `is_indoor is False` (not just
    falsy — assert identity/type, since `0 == False` in Python would mask
    a bug that stored `0` instead of the real boolean).
  - Indoor trainer case: `trainer: True`, `elevation_gain_raw` absent or
    `0` → `is_indoor is True`, `elevation_gain_m` reflects whatever was
    given (0.0 or None, not fabricated either way).
  - Missing-everything case: a payload with none of `elevation_gain_raw`,
    `moving_time_raw`, `trainer` present at all → all three canonical
    fields are `None` — this is AC6's third required case and the one that
    most directly guards against a `.get(..., False)`-style regression
    creeping in later.
  - `moving_time_s` is asserted distinct from `duration_s` in at least one
    test (different values for `moving_time_raw` vs. `elapsed_time_raw` in
    the fixture) — guards against a copy-paste that accidentally maps both
    canonical fields from the same raw key.
- **`strava.py` (`_activity_to_raw_dict()` + `normalize()` together, AC2):**
  mirror the same three cases (outdoor-with-elevation, indoor-trainer,
  all-three-missing) through `_fake_activity()` → `_activity_to_raw_dict()`
  → `normalize()`, asserting the full round trip — this is the test that
  would have caught the "fixed `normalize()` but forgot
  `_activity_to_raw_dict()`" mistake the requirements doc explicitly warns
  about, since testing `normalize()` alone against a hand-built raw dict
  would not.
- **Renormalization (AC4):** extend the existing `strava_unofficial`
  idempotency test(s) in `test_renormalize.py` so the fixture raw payloads'
  already-present `elevation_gain_raw`/`moving_time_raw` (and a newly added
  `trainer` key) are asserted to show up in the post-renormalize
  `normalized_activities` row — proving the backfill path actually works
  end-to-end through `renormalize_provider()`, not just that `normalize()`
  produces the right dict in isolation.
- **`build_canonical_record()` / `upsert_normalized_activity()`:** at least
  one test constructing a `normalized` dict with the three new keys set to
  real (non-`None`) values, running it through
  `build_canonical_record()` → `upsert_normalized_activity()`, and reading
  the row back — confirms the full pass-through, including that SQLite
  round-trips the `bool`→`INTEGER` `is_indoor` value correctly on both
  insert and update paths (the `UPDATE` branch needs its own assertion, not
  just `INSERT OR IGNORE`'s, since they're two separate SQL statements with
  independently-maintained column lists — exactly the kind of place a
  partial edit misses one branch).
- **Unchanged, keep as-is:** every other `normalize()`/`_activity_to_raw_dict()`
  assertion in both connectors' test files that doesn't reference these
  three fields; `test_taxonomy.py`; `test_sync_engine.py`'s non-activity
  (weigh-in) coverage.
- **What NOT to test here:** anything about official-`strava` backfill
  actually running against pre-fix rows — per the Backfill note, that
  requires a fresh sync this pipeline cannot perform (no live Strava
  account in CI), so it's a BO-side verification step, not a pipeline test.

## Risks/tradeoffs

- **`confidence.py`'s `_ACTIVITY_OPTIONAL_FIELDS` is deliberately NOT
  extended to include the three new fields.** `compute_source_confidence()`
  treats `avg_hr`/`avg_power`/`distance_m`/`calories` as the "optional,
  completeness-affecting" set today; adding `elevation_gain_m`/
  `moving_time_s`/`is_indoor` to that tuple would silently change
  `source_confidence` for every existing Strava activity (and every other
  provider's, since the tuple is shared across all ACTIVITY-kind records)
  the moment this ships — a behavior change nothing in the requirements or
  acceptance criteria asks for ("Any new analytics... built on top of
  these fields" is explicitly out of scope). Leaving this tuple untouched
  is a deliberate choice, flagged so a future reader doesn't assume it was
  an oversight.
- **Nullable `ADD COLUMN` with no backfill step is the right call here, but
  it's a different migration shape than migrations 4/5**, which both ran a
  dedicated Python backfill function (`backfill_weigh_in_plausibility`,
  `decouple_weigh_in_plausibility`) via `migrate()`'s `if version == N:`
  hook. Those migrations needed to *compute* a value for existing rows;
  this one's correct value for existing rows is simply "unknown" (`NULL`),
  which `ADD COLUMN`'s own default behavior already provides — adding a
  no-op backfill function here would be unnecessary surface, not extra
  safety.
- **`is_indoor`'s "never defaults to `False`" requirement is only as strong
  as the discipline across three separate `.get()` call sites staying
  consistent** (two connectors' `normalize()`, plus the schema's lack of a
  `DEFAULT` clause). Nothing structurally prevents a future edit from
  writing `raw.get("trainer", False)` by habit (matching a common Python
  idiom that's wrong specifically for this field). The test suite's
  explicit "all-three-missing → all-three-`None`" case (per Test strategy
  notes) is the guard against regression here, not a type system
  constraint — worth the Developer's attention specifically on this point.
- **Official-`strava` backfill remaining undocumented-as-tooling (no
  `renormalize_strava.py` script) until a fresh sync happens is an
  intentional sequencing choice, not a deferral of required scope.**
  AC5 asks for documentation of the constraint, which this doc provides;
  building the wrapper script now would be dead code until the BO runs a
  fresh sync, and the wrapper itself is a trivial, well-precedented copy of
  `scripts/renormalize_strava_unofficial.py` whenever that day comes — the
  cost of deferring it is near zero, and the cost of shipping an untestable
  script today (nothing in CI can exercise it meaningfully without live
  `raw_activities` rows that contain the new fields) is not.
