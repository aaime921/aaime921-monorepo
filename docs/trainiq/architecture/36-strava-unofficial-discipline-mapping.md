# Architecture: Map `strava_unofficial` Activities to Canonical Discipline Taxonomy

**Issue:** #36
**Requirements:** [`docs/trainiq/requirements/36-strava-unofficial-discipline-mapping.md`](../requirements/36-strava-unofficial-discipline-mapping.md)
**Related:** #30, PR #31 (`StravaUnofficialConnector`)

## Approach

Two independent pieces of work, both minimal:

**1. Taxonomy fix.** Register `strava_unofficial` in `trainiq/normalization/taxonomy.py`'s
`_PROVIDER_MAPS` by **reusing `_STRAVA_MAP` directly** — the exact pattern already
established for `peloton_csv` reusing `_PELOTON_MAP` (taxonomy.py:89): same dict
object, not a copy, zero new taxonomy categories. This is safe because
`strava_unofficial`'s `discipline_raw` vocabulary (`"Ride"`, `"Run"`, `"Workout"`) is
confirmed identical to official Strava's (`strava_unofficial.py:312` extracts
`activity_type_display_name`/`display_type`, which are Strava's own display strings
for the same underlying activity types `_STRAVA_MAP` already covers).

Resolves the BA's open question ("shared vs. separate map for `Walk`") in favor of
**shared**: add one explicit `"Walk": Discipline.OTHER` entry to `_STRAVA_MAP` itself,
rather than forking a `strava_unofficial`-only map. Reasoning:
- It's the same reuse pattern this module already uses for `peloton_csv` — introducing
  a *second* pattern (a separate map, just for this one provider) for no functional
  gain would make the module harder to read, not easier.
- The requirements doc explicitly permits this ("minimal consequence of adding the
  `Walk` entry" to the shared map is in-scope). The out-of-scope list only excludes
  changes to *other providers'* mapping *beyond* that minimal consequence — official
  Strava's `Walk` activities also currently fall to `OTHER` via the unrecognized-value
  warning path (same latent gap, confirmed by reading `_STRAVA_MAP`: no `"Walk"` key
  exists today), so making it explicit for both is strictly a bugfix in both places,
  not a behavior change for either.
- `Mountain Bike Ride` needs no map entry at all: `strava_unofficial.normalize()`
  (strava_unofficial.py:312) already prefers `activity_type_display_name` ("Ride")
  over `display_type` ("Mountain Bike Ride"), so `discipline_raw` is already `"Ride"`
  by the time it reaches `map_discipline()`. This needs an end-to-end fixture test to
  prove, not a map change.

**2. Re-normalization of the 352 existing rows.** `raw_activities` already stores the
untouched provider payload for every one of these rows specifically so normalization
can be re-run later without re-fetching (per the existing module docstring at
`sync/engine.py:471-474`). A live sync will **not** self-heal them: `download()` only
fetches activities after the stored checkpoint (`sync/engine.py:438-439`), so already-
fetched rows are never re-passed through `normalize()`/`build_canonical_record()` by
the normal sync path. A dedicated, idempotent one-off pass is required.

`SynchronizationEngine`'s own docstring (`sync/engine.py:22-37`) is explicit that
retroactively recomputing past normalized records is "a new, separate capability, not
an extension of this engine's existing behavior" (stated there for `athlete_profile`
changes, but the principle — don't graft backfill semantics onto the forward-only sync
path — applies equally here). Accordingly, this design does **not** add a
re-normalize method to `SynchronizationEngine` or touch `sync_connector()`/
`run_once()` at all. Instead:

- The existing idempotent upsert SQL in `SynchronizationEngine._upsert_normalized_activity`
  (`sync/engine.py:198-240`) is extracted, behavior-unchanged, into a module-level
  function `upsert_normalized_activity(conn, record)` in the same file. The instance
  method becomes a one-line delegator. This is the one piece of code reuse needed to
  avoid duplicating the `INSERT OR IGNORE` / conditional `UPDATE` pattern a second time.
- A new, small module `trainiq/normalization/renormalize.py` contains the actual
  backfill loop, built only from already-pure pieces (`connector.normalize()`,
  `build_canonical_record()`, the newly-exported `upsert_normalized_activity()`) — it
  depends on `trainiq.sync.engine` for that one function, same direction of dependency
  the module docstring already accepts elsewhere in this package.
- A thin CLI script, `scripts/renormalize_strava_unofficial.py`, wires a real DB
  connection + `StravaUnofficialConnector` and calls the module function, following
  the exact `open_db()` / `CredentialStore` / argparse `--db-path` shape already used
  by `scripts/import_peloton_csv.py` and `scripts/connect_peloton_manual_recovery.py`.
  `StravaUnofficialConnector.normalize()` makes no network call and touches no
  credentials (verified by reading `strava_unofficial.py:293-324`), so constructing the
  connector for this purpose is safe even with an expired/absent session cookie.

Running the current `athlete_profile` (if any) through `build_canonical_record()` for
these rows also recomputes `training_load`/`training_load_method`/`source_confidence`
as a side effect — see Risks/tradeoffs; today this resolves to
`training_load=None, method=unknown` for every row regardless of discipline, since no
`AthleteProfile` persistence exists yet (Epic 7 not built — `normalization/load.py`
docstring), so in practice this backfill only changes the `discipline` column. This is
documented, not silently relied upon.

## Affected components/files

**Modified:**
- `projects/trainiq/trainiq/normalization/taxonomy.py` — add `"Walk"` to
  `_STRAVA_MAP`; add `"strava_unofficial": _STRAVA_MAP` to `_PROVIDER_MAPS`; bump
  `MAPPING_VERSION` with a dated comment.
- `projects/trainiq/trainiq/sync/engine.py` — extract `_upsert_normalized_activity`'s
  body into a module-level `upsert_normalized_activity(conn, record) -> str`; the
  instance method delegates to it. No behavior change to `SynchronizationEngine`.

**New:**
- `projects/trainiq/trainiq/normalization/renormalize.py` — the re-normalization loop.
- `projects/trainiq/scripts/renormalize_strava_unofficial.py` — CLI entry point.
- Tests (see Test strategy notes).

**Not touched:** `strava_unofficial.py`'s extraction logic, `strava.py`, `app.py`,
`connectors/base.py`, the schema. Confirms the requirements doc's scope boundaries.

## Interfaces/contracts

`trainiq/normalization/taxonomy.py`:
```python
MAPPING_VERSION = 2  # 2026-10-06: register strava_unofficial (reuses _STRAVA_MAP;
                      # adds explicit Walk->OTHER, closing the same latent gap for
                      # official Strava) — issue #36

_STRAVA_MAP: dict[str, Discipline] = {
    ...  # unchanged existing entries
    "Walk": Discipline.OTHER,  # explicit, deliberate — not a warning-fallback
}

_PROVIDER_MAPS: dict[str, dict[str, Discipline]] = {
    "strava": _STRAVA_MAP,
    "peloton": _PELOTON_MAP,
    "peloton_csv": _PELOTON_MAP,
    "strava_unofficial": _STRAVA_MAP,  # same vocabulary as official Strava's
                                        # display strings — see architecture
                                        # doc for issue #36
}
```

`trainiq/sync/engine.py` (extraction — signature is new, behavior is not):
```python
def upsert_normalized_activity(conn: sqlite3.Connection, record: dict) -> str:
    """Returns "inserted" or "updated". Moved out of SynchronizationEngine so a
    one-off re-normalization pass can reuse the exact idempotent persistence
    primitive instead of duplicating this SQL (see renormalize.py)."""
    # body identical to today's SynchronizationEngine._upsert_normalized_activity

class SynchronizationEngine:
    def _upsert_normalized_activity(self, record: dict) -> str:
        return upsert_normalized_activity(self._conn, record)
```

`trainiq/normalization/renormalize.py` (new):
```python
@dataclass(frozen=True)
class RenormalizeResult:
    read: int
    inserted: int
    updated: int
    skipped_malformed: int
    skipped_no_external_id: int

def renormalize_provider(
    conn: sqlite3.Connection,
    provider: str,
    connector: Connector,
    athlete_profile: Optional[AthleteProfile] = None,
) -> RenormalizeResult:
    """Re-derives every normalized_activities row for `provider` from its
    already-stored raw_activities payload, via connector.normalize() +
    build_canonical_record() — the same transformation a live sync would apply,
    run again against old raw data. Never touches raw_activities. Idempotent:
    reruns produce identical normalized_activities rows (all "updated", no
    further changes) because persistence goes through the same
    upsert_normalized_activity() keyed on UNIQUE(provider, external_id) a live
    sync uses.

    Malformed-record handling mirrors SynchronizationEngine.sync_connector()
    exactly (sync/engine.py:477-487): a row that fails to build a canonical
    record is logged and skipped, never allowed to abort the whole pass.
    """
```

`scripts/renormalize_strava_unofficial.py` (new CLI):
```
usage: renormalize_strava_unofficial.py [--db-path PATH]

Re-normalizes every stored strava_unofficial raw_activities row so its
discipline (and any other canonical field) reflects the post-#36 taxonomy
mapping, without re-fetching from Strava. Safe to run more than once.

Prints: "read: 352, inserted: 0, updated: 352, skipped_malformed: 0,
skipped_no_external_id: 0" (a real count per run, not an estimate — same
convention as upsert_normalized_activity's "inserted"/"updated" strings).
Exit code 0 on success; the connection is committed before exit.
```

## Task breakdown

1. `taxonomy.py`: add `"Walk": Discipline.OTHER` to `_STRAVA_MAP`; add
   `"strava_unofficial": _STRAVA_MAP` to `_PROVIDER_MAPS`; bump `MAPPING_VERSION` to
   `2` with a dated comment referencing issue #36 (per the module's existing
   convention at line 42-47).
2. `test_taxonomy.py`: add `strava_unofficial` cases — `Run`→RUNNING, `Ride`→CYCLING,
   `Workout`→STRENGTH, `Walk`→OTHER **with no "unrecognized" warning logged** (assert
   via `caplog`, distinguishing AC4's explicit-mapping requirement from the generic
   unrecognized-value fallback already covered by
   `test_unrecognized_discipline_value_falls_back_to_other_not_guessed`). Also add
   (or confirm, since `_STRAVA_MAP` is shared) that `map_discipline("strava", "Walk")`
   now also resolves to OTHER explicitly — one assertion documenting the side effect.
3. `test_strava_unofficial_connector.py` (or a new end-to-end test alongside it): a
   fixture raw payload with `display_type="Mountain Bike Ride"`,
   `activity_type_display_name="Ride"`, run through `StravaUnofficialConnector.normalize()`
   then `map_discipline()`, asserting the result is `Discipline.CYCLING` — this is AC5,
   and must exercise both functions together, not assume the extraction order.
4. `sync/engine.py`: extract `_upsert_normalized_activity`'s body verbatim into
   module-level `upsert_normalized_activity(conn, record)`; make the instance method a
   one-line delegator. Run `test_sync_engine.py` unmodified to confirm zero behavior
   change.
5. New `trainiq/normalization/renormalize.py`: implement `renormalize_provider()` per
   the contract above — `SELECT external_id, payload_json FROM raw_activities WHERE
   provider = ?`, loop: `json.loads(payload_json)` → `connector.normalize(raw)` →
   (try) `build_canonical_record(provider, connector.record_kind, normalized,
   athlete_profile)` (except `KeyError, TypeError, ValueError`: log + count as
   `skipped_malformed`, matching `sync/engine.py:477-487`'s own exception set) →
   `upsert_normalized_activity(conn, canonical_record)`, tallying `inserted`/`updated`.
   Do not call `conn.commit()` inside this function — the caller (script) owns the
   transaction boundary, matching how `SynchronizationEngine` callers already commit
   (`sync/engine.py:182` for checkpoints — commit happens at the call site that owns
   the connection's lifetime).
6. New test `test_renormalize.py`: build an in-memory `open_db()` connection, insert
   fixture `raw_activities` rows for `strava_unofficial` directly (mirroring the
   Ride/Run/Walk/Workout/Mountain-Bike-Ride table from the requirements doc) with a
   pre-existing `normalized_activities` row stuck at `discipline="other"` for each
   (simulating the real bug), run `renormalize_provider()`, and assert: (a) every row's
   `discipline` is now correct, (b) `raw_activities` is byte-for-byte unchanged, (c)
   running `renormalize_provider()` a second time returns the same row count with
   `updated == 5, inserted == 0` and no further `discipline` changes (idempotency).
7. New `scripts/renormalize_strava_unofficial.py`: CLI following
   `import_peloton_csv.py`'s shape — `open_db(args.db_path)`, build
   `StravaUnofficialConnector(CredentialStore(conn=conn))`, call
   `renormalize_provider(conn, "strava_unofficial", connector)`, `conn.commit()`,
   print the result's counts, return exit code `0`. Module docstring documents the
   command the BO runs once against their own database (per requirements doc's
   out-of-scope note — the pipeline ships the script; the BO executes it).
8. Run the full existing `taxonomy.py`/`strava_unofficial`/`sync_engine` test suites
   to confirm AC8 (no regressions).

## Test strategy notes

- **Unit (taxonomy):** every value in the requirements doc's evidence table, plus the
  "Walk is explicit, not a warning-fallback" distinction (AC4) — this needs a
  `caplog`-based assertion, not just a return-value assertion, since both the old
  (buggy) and new (fixed) behavior return `Discipline.OTHER` for `Walk`; the
  observable difference is the absence of the "unrecognized discipline_raw" warning.
- **Unit/integration (connector + taxonomy together):** the `Mountain Bike Ride`
  end-to-end case (AC5) — must not be tested as a taxonomy-only unit test, since the
  whole point is that the map never sees `"Mountain Bike Ride"` as input.
- **Integration (renormalize):** fixture-based only, per this project's established
  live-verification boundary (CI has no access to the BO's real database) — the test
  builds its own small SQLite DB via `open_db()`, never touches
  `APP_SUPPORT_DIR/trainiq.db`. Must prove idempotency explicitly (run twice, assert
  identical outcome on the second run), since AC7 requires it and it's easy to get
  right by accident on a single run and wrong on a rerun.
- **Manual (BO):** running `scripts/renormalize_strava_unofficial.py` against the real
  database with the real 352 rows is explicitly BO-side verification, not a pipeline
  deliverable — document the exact command in the script's docstring and in the
  handoff comment so the BO doesn't have to read the source to find it.

## Risks/tradeoffs

- **Shared-map side effect on official Strava.** Adding `"Walk"` to `_STRAVA_MAP`
  changes official `strava` provider behavior too (OTHER-via-explicit-mapping instead
  of OTHER-via-warning). The requirements doc anticipated and permitted exactly this;
  flagged here so it's visible in history, not discovered later as a surprise diff in
  `strava`'s test suite. If this project ever wants `strava` and `strava_unofficial` to
  diverge on `Walk` specifically, that's a future, explicitly-scoped change — not
  something this issue should pre-empt by guessing a separate-map design nobody asked
  for.
- **`training_load`/`source_confidence` recomputation during backfill.** Re-running
  `build_canonical_record()` recomputes more than `discipline`. Today this is a no-op
  for `training_load` (no `AthleteProfile` exists yet — resolves to
  `None`/`unknown` regardless of discipline), but this is an environment fact, not a
  structural guarantee — if Epic 7 ships an `AthleteProfile` before this backfill runs,
  re-normalizing would also assign real `training_load` values to these 352 rows for
  the first time (correct, but worth the BO knowing before running the script, since
  it's a bigger diff than "just the discipline column" at that point). Noted in the
  script's docstring, not silently left as a surprise.
- **Re-normalization is a manual, BO-triggered step, not automatic-on-next-sync.**
  Chosen over "automatic on next sync" because the Sync Engine's checkpoint-based
  `download()` has no mechanism to "re-fetch" already-synced activities short of
  resetting the provider's checkpoint (which would re-download from Strava
  unnecessarily, burning rate limit and risking the exact session-cookie fragility
  this connector's docstring already flags as a real constraint) — reusing already-
  stored `raw_activities` via a dedicated pass is strictly cheaper and matches why
  `raw_activities` is retained at all (`sync/engine.py:471-474`). The cost is an
  explicit manual step instead of a self-healing one; acceptable since this project's
  own conventions already route one-off/BO-run operations through `scripts/`
  (`import_peloton_csv.py`, `connect_peloton_manual_recovery.py`, etc.).
- **`sync/engine.py` refactor touches a file with real production behavior.** Mitigated
  by making the extraction a pure move (no logic change) and relying on
  `test_sync_engine.py`'s existing coverage of `_upsert_normalized_activity`'s
  insert/update paths to catch any accidental behavior drift — if those tests still
  pass unmodified, the delegation is behavior-preserving.

## What NOT to do (carried from requirements doc's out-of-scope section)

- Do not execute `renormalize_strava_unofficial.py` against the BO's live database as
  part of this pipeline — ship the script and its tests; the BO runs it.
- Do not change `strava_unofficial.normalize()`'s extraction order/logic.
- Do not add discipline categories beyond the existing `Discipline` enum.
