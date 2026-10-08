# Architecture: Stop Treating Eufy's "Not Measured" Body-Fat Sentinel as Implausible

**Issue:** #42
**Requirements:** `docs/trainiq/requirements/42-eufy-body-fat-zero-not-implausible.md`
**Related:** #38 (original plausibility rule), PR #41 (shipped it), ADR-039 (corrected by this design)

## Approach

Two independent defects, fixed at the two layers where they were introduced:

1. **Connector layer (`EufyConnector.normalize()`):** `body_fat_pct = 0.0` and
   `muscle_mass_pct = 0.0` are Eufy's "not measured" sentinel (BO-confirmed
   live evidence for both fields — see requirements "Open questions"
   resolution, BO comment on the issue). `normalize()` now maps a literal
   `0` on either field to `None` before the value ever reaches persistence
   or the plausibility rule, the same place issue #1 already fixed the
   deci-kilogram weight scaling — extraction-time sentinel cleanup is this
   connector's existing job, not a new category of work.

2. **Rule layer (`normalization/plausibility.py`):** ADR-039's rule
   conflated two independent questions — "is this body-fat reading
   physiologically plausible?" and "is this weight reading plausible?" —
   into one verdict, so tripping the (conservative, secondary) body-fat
   floor suppressed the (primary, already-correct) weight-deviation axis's
   verdict on the **weight itself**. `evaluate_weigh_in_plausibility()` now
   returns two independent verdicts instead of one. This is a correction to
   the rule's shape, not just its inputs: even with defect 1 fixed, a
   future non-zero-but-below-floor body-fat reading (e.g. `1.5`) would
   still wrongly exclude a perfectly good weight reading under the old
   single-verdict design. Both fixes are required; neither alone satisfies
   the acceptance criteria (AC2/AC3 specifically describe the decoupling,
   independent of AC1's sentinel-normalization).

The body-fat floor is **kept** (requirements explicitly allow this), but is
now evaluated only against present, non-null, non-zero values, and a trip
on it flags only the body-fat aspect of the record. This defends the rule
itself against the same mistake even if some future connector forgets to
normalize its own zero-sentinel the way Eufy now does — the two checks
(`> 0` guard and the connector-level normalization) are deliberately
redundant, not one-or-the-other.

Schema v5 splits the single `is_flagged_implausible`/`plausibility_reason`
pair into two independent pairs — one per axis — so "is this weight
excluded from analytics" and "is this body-composition reading suspect"
are no longer the same bit. `bo_confirmed_valid`/`bo_confirmed_at` stay
attached to the weight axis only (that's the thing a BO override actually
restores to analytics); no equivalent override is introduced for the
body-fat axis in this slice (see Risks/tradeoffs).

The corrective one-time pass (AC4) runs as part of the v4→v5 migration,
reusing the same "re-evaluate everything in chronological order" shape
`backfill_weigh_in_plausibility()` already established for v3→v4 — not a
new kind of operation, the same one applied to the rule's corrected shape.
It also normalizes any already-stored `body_fat_pct = 0` /
`muscle_mass_pct = 0` rows to `NULL` first, so rows synced before this fix
shipped end up byte-identical (schema-wise) to rows that will be synced
after it, rather than leaving stale zeros only a *future* resync would
clean up.

## Affected components/files

| File | Change |
|---|---|
| `trainiq/connectors/eufy.py` | `normalize()`: map a literal `0` on `body_fat_pct`/`muscle_mass_pct` to `None`. |
| `trainiq/normalization/plausibility.py` | `evaluate_weigh_in_plausibility()` returns two independent verdicts (new `WeighInPlausibility` shape) instead of one; body-fat floor gains an explicit `> 0` guard. |
| `trainiq/normalization/engine.py` | `_build_weigh_in_record()` maps the two verdicts onto four output keys instead of two. |
| `trainiq/storage/schema.py` | New migration, version 5: renames the two existing plausibility columns, adds two more. `CURRENT_SCHEMA_VERSION = 5`. |
| `trainiq/storage/backfill.py` | New `decouple_weigh_in_plausibility(conn)` — the v4→v5 corrective pass (AC4). Existing `backfill_weigh_in_plausibility()` (v3→v4) is untouched, since it already ran against the BO's real database; rewriting it would not change anything that already happened and would make the migration history harder to audit. |
| `trainiq/sync/engine.py` | `_upsert_weigh_in()` column lists use the four new/renamed columns. `_recent_weights_before()`'s predicate uses the weight-axis column only. `flagged_implausible_count` / `records_flagged_implausible` count the weight axis only (see Risks/tradeoffs). |
| `scripts/confirm_weigh_in.py` | Reads/writes the renamed weight-axis column instead of the old combined one; behavior and CLI contract otherwise unchanged. |
| `docs/trainiq/adr/ADR-039-weigh-in-plausibility-flagging.md` | Updated in place (not a new ADR — requirements AC8 and the issue both say "update ADR-039") with a "Correction (Issue #42)" section: the missing-vs-zero distinction, the schema v5 column split, and why `bo_confirmed_valid` stays attached to the weight axis only. |
| `trainiq/BACKLOG.md` | One line noting BL-009 (`source_confidence` on `weigh_ins`) is still unrelated to and unaffected by this migration — same precedent #38 set when it added v4. |

No change to `trainiq/athlete/profile.py`, `trainiq/athlete/store.py`, or
`trainiq/connectors/base.py`.

## Interfaces/contracts

### `trainiq/normalization/plausibility.py`

```python
@dataclass(frozen=True)
class WeighInPlausibility:
    is_weight_plausible: bool
    weight_reason: Optional[str]        # None iff is_weight_plausible
    is_body_fat_plausible: bool
    body_fat_reason: Optional[str]      # None iff is_body_fat_plausible


def evaluate_weigh_in_plausibility(
    weight_kg: Optional[float],
    body_fat_pct: Optional[float],
    recent_weights_kg: Sequence[float],
    *,
    deviation_threshold_pct: float = DEFAULT_WEIGHT_DEVIATION_THRESHOLD_PCT,
    min_history: int = DEFAULT_MIN_HISTORY_FOR_WEIGHT_CHECK,
    body_fat_floor_pct: float = DEFAULT_BODY_FAT_FLOOR_PCT,
) -> WeighInPlausibility:
    """Pure function, no I/O. The two axes are now fully independent —
    one can never flag the other. `recent_weights_kg` semantics unchanged
    from ADR-039: the athlete's prior readings whose WEIGHT was not
    flagged (or was BO-confirmed), strictly before this one's timestamp,
    newest-first, already window-limited — this function does no
    filtering/ordering itself. A reading whose body-fat axis trips still
    contributes its weight to future rolling windows, since a missing or
    implausible body-composition value says nothing about whether the
    weight itself was measured correctly.
    """
    if body_fat_pct is not None and body_fat_pct > 0 and body_fat_pct <= body_fat_floor_pct:
        body_fat_verdict = (False, (
            f"body_fat_pct {body_fat_pct} is at or below the physiological "
            f"floor of {body_fat_floor_pct}"
        ))
    else:
        body_fat_verdict = (True, None)

    weight_verdict = (True, None)
    if weight_kg is not None and len(recent_weights_kg) >= min_history:
        baseline = median(recent_weights_kg)
        if baseline > 0:
            deviation_pct = abs(weight_kg - baseline) / baseline * 100
            if deviation_pct > deviation_threshold_pct:
                weight_verdict = (False, (
                    f"weight_kg {weight_kg} deviates {deviation_pct:.1f}% from the "
                    f"athlete's rolling median baseline of {baseline:.1f} kg, "
                    f"exceeding the {deviation_threshold_pct}% threshold"
                ))

    return WeighInPlausibility(
        is_weight_plausible=weight_verdict[0], weight_reason=weight_verdict[1],
        is_body_fat_plausible=body_fat_verdict[0], body_fat_reason=body_fat_verdict[1],
    )
```

`body_fat_pct > 0` is the new guard (requirements AC3: "applies only to
present, non-null, **non-zero**" values) — kept in the rule itself, not
only relied upon via connector-level normalization, so the rule is
correct even for a future connector that hasn't normalized its own
zero-sentinel.

### `trainiq/connectors/eufy.py` — `normalize()`

```python
scale_data = raw.get("scale_data") or {}
raw_weight = scale_data.get("weight")
raw_body_fat = scale_data.get("body_fat")
raw_muscle_mass = scale_data.get("muscle_mass")
return {
    "provider": PROVIDER,
    "external_id": str(raw["id"]),
    "timestamp": raw["create_time"],
    "weight_kg": (
        raw_weight / WEIGHT_DECI_KG_TO_KG_DIVISOR if raw_weight is not None else None
    ),
    # Issue #42, BO-confirmed live evidence: Eufy reports 0.0 for a
    # metric the scale did not actually measure this weigh-in (e.g.
    # weighed with socks on, no impedance reading) — not a genuine 0%
    # reading. Normalized to None here, at the same extraction layer
    # issue #1 already established for the deci-kg weight conversion,
    # so neither persistence nor the plausibility rule ever sees the
    # sentinel as a real value.
    "body_fat_pct": None if raw_body_fat == 0 else raw_body_fat,
    "muscle_mass_pct": None if raw_muscle_mass == 0 else raw_muscle_mass,
}
```

### Schema migration v5 (`trainiq/storage/schema.py`)

```python
CURRENT_SCHEMA_VERSION = 5

_MIGRATIONS[5] = """
-- ADR-039 (corrected by Issue #42): decouple body-fat plausibility from
-- weight plausibility. A body-fat verdict must never suppress the weight.
ALTER TABLE weigh_ins RENAME COLUMN is_flagged_implausible TO is_weight_flagged_implausible;
ALTER TABLE weigh_ins RENAME COLUMN plausibility_reason TO weight_plausibility_reason;
ALTER TABLE weigh_ins ADD COLUMN is_body_fat_flagged_implausible INTEGER NOT NULL DEFAULT 0;
ALTER TABLE weigh_ins ADD COLUMN body_fat_plausibility_reason TEXT;
"""
```

`ALTER TABLE ... RENAME COLUMN` requires SQLite ≥ 3.25 (2018-09-15); every
Python version this project supports (3.9+) bundles a newer `sqlite3`, so
this is safe without a feature check, consistent with Milestone 4's
rejection of a migration framework in favor of plain `executescript()`
(Decision Matrix 11.1) — a rename is still just a statement in that model,
not new machinery.

`bo_confirmed_valid`/`bo_confirmed_at` are untouched — same two columns,
same owner (`scripts/confirm_weigh_in.py` only), same meaning.

Called from `migrate()`:
```python
for version in range(current + 1, target + 1):
    script = _MIGRATIONS.get(version)
    if script is None:
        raise RuntimeError(f"No migration defined for version {version}")
    conn.executescript(script)
    if version == 4:
        backfill_weigh_in_plausibility(conn)
    if version == 5:
        decouple_weigh_in_plausibility(conn)
    conn.execute("UPDATE schema_version SET version = ?", (version,))
    conn.commit()
```

### `trainiq/storage/backfill.py` — new `decouple_weigh_in_plausibility()`

```python
def decouple_weigh_in_plausibility(conn: sqlite3.Connection) -> int:
    """The v4->v5 corrective pass (Issue #42, AC4). Two steps:

    1. Normalizes any already-stored body_fat_pct/muscle_mass_pct of
       exactly 0 to NULL — rows synced before EufyConnector.normalize()'s
       fix shipped (including every row already on the BO's production
       database) carry the literal-zero sentinel; this makes them match
       what a fresh sync now produces, rather than leaving them to drift
       back into NULL only the next time that specific row happens to
       resync.
    2. Re-evaluates every weigh_ins row, in ascending timestamp order
       (unchanged semantics from backfill_weigh_in_plausibility), against
       the corrected evaluate_weigh_in_plausibility(), writing the two
       now-independent verdicts into the four flag/reason columns.
       Mirrors _recent_weights_before()'s live-sync predicate: a row
       contributes its weight to later rows' rolling windows whenever
       ITS OWN weight axis is plausible (or BO-confirmed), regardless of
       that row's body-fat verdict.

    Never touches bo_confirmed_valid/bo_confirmed_at (AC5). Idempotent by
    construction, invoked exactly once per database from migrate()'s
    v4->v5 transition.

    Returns the number of rows whose WEIGHT axis is flagged after this
    pass (the number actually excluded from analytics) — not a combined
    count, since the two axes no longer share one meaning.
    """
    conn.execute("UPDATE weigh_ins SET body_fat_pct = NULL WHERE body_fat_pct = 0")
    conn.execute("UPDATE weigh_ins SET muscle_mass_pct = NULL WHERE muscle_mass_pct = 0")

    rows = conn.execute(
        "SELECT id, weight_kg, body_fat_pct FROM weigh_ins ORDER BY timestamp ASC"
    ).fetchall()

    recent_weights: list[float] = []
    weight_flagged_count = 0
    for row_id, weight_kg, body_fat_pct in rows:
        window = recent_weights[-DEFAULT_ROLLING_WINDOW_SIZE:]
        verdict = evaluate_weigh_in_plausibility(weight_kg, body_fat_pct, window)

        conn.execute(
            "UPDATE weigh_ins SET is_weight_flagged_implausible = ?, weight_plausibility_reason = ?, "
            "is_body_fat_flagged_implausible = ?, body_fat_plausibility_reason = ? WHERE id = ?",
            (
                not verdict.is_weight_plausible, verdict.weight_reason,
                not verdict.is_body_fat_plausible, verdict.body_fat_reason,
                row_id,
            ),
        )
        if not verdict.is_weight_plausible:
            weight_flagged_count += 1
        if verdict.is_weight_plausible and weight_kg is not None:
            recent_weights.append(weight_kg)

    return weight_flagged_count
```

Note the rolling-window append condition changed from the v4 backfill's
`elif weight_kg is not None` (reachable only when the combined verdict was
plausible) to an explicit `if verdict.is_weight_plausible and weight_kg is
not None` — a row whose body-fat axis alone trips must still feed later
rows' baselines, which is the whole point of this issue.

### `trainiq/normalization/engine.py` — `_build_weigh_in_record()`

```python
def _build_weigh_in_record(
    provider: str, normalized: dict[str, Any], recent_weights_kg: Sequence[float] = ()
) -> dict[str, Any]:
    verdict = evaluate_weigh_in_plausibility(
        normalized.get("weight_kg"), normalized.get("body_fat_pct"), recent_weights_kg
    )
    return {
        "provider": provider,
        "external_id": normalized["external_id"],
        "timestamp": normalized["timestamp"],
        "weight_kg": normalized.get("weight_kg"),
        "body_fat_pct": normalized.get("body_fat_pct"),
        "muscle_mass_pct": normalized.get("muscle_mass_pct"),
        "is_weight_flagged_implausible": not verdict.is_weight_plausible,
        "weight_plausibility_reason": verdict.weight_reason,
        "is_body_fat_flagged_implausible": not verdict.is_body_fat_plausible,
        "body_fat_plausibility_reason": verdict.body_fat_reason,
    }
```

### `trainiq/sync/engine.py` changes

`_upsert_weigh_in()`'s INSERT/UPDATE column lists replace
`is_flagged_implausible, plausibility_reason` with the four new/renamed
columns (still never `bo_confirmed_valid`/`bo_confirmed_at`).

`_recent_weights_before()`'s predicate changes from
`(is_flagged_implausible = 0 OR bo_confirmed_valid = 1)` to
`(is_weight_flagged_implausible = 0 OR bo_confirmed_valid = 1)` —
mechanically a rename, but worth stating explicitly: this is the one
query in the codebase whose correctness this whole issue is about, so the
column it filters on must be the weight axis, never a combined one.

In `sync_connector()`'s per-record loop, the flagged-count check changes
from `canonical_record.get("is_flagged_implausible")` to
`canonical_record.get("is_weight_flagged_implausible")` —
`records_flagged_implausible` (and the summary log's "flagged N
implausible" clause) keep meaning **excluded from analytics**, which is a
property of the weight axis only now. A body-fat-only flag is real
information (persisted, queryable) but isn't a sync-health signal the
existing counter was ever meant to surface, and inventing a second counter
isn't required by any acceptance criterion — not done here to avoid scope
creep (see Risks/tradeoffs if this turns out to be wanted later).

### `scripts/confirm_weigh_in.py` changes

`confirm_weigh_in()`'s lookup/guard reads `is_weight_flagged_implausible`
and `weight_plausibility_reason` instead of the old combined names; the
`WeighInNotFlagged` message and CLI contract are otherwise unchanged. No
equivalent confirm/override path is added for the body-fat axis in this
slice — see Risks/tradeoffs.

## Task breakdown

1. `connectors/eufy.py`: `normalize()` sentinel fix (both fields) +
   extend `test_eufy_connector.py` with cases for `body_fat=0`/
   `muscle_mass=0` → `None`, and a present non-zero value passing through
   unchanged (regression against over-normalizing).
2. `normalization/plausibility.py`: new `WeighInPlausibility` shape, `> 0`
   guard, independent-verdict logic. Update `tests/test_plausibility.py`'s
   existing assertions (`verdict.is_plausible`/`verdict.reason` →
   `verdict.is_weight_plausible`/`verdict.is_body_fat_plausible`/etc.) and
   add: `weight_kg=85.0, body_fat_pct=0.0` → body-fat axis flags, weight
   axis stays plausible (the issue's core regression case, AC6); a
   non-zero sub-floor value (e.g. `1.5`) behaves the same way; `0.0` with
   an also-implausible weight → both axes flag independently (not one
   suppressing detection of the other).
3. Migration v5 in `storage/schema.py` (rename + two new columns) +
   `test_storage.py::test_v5_migration_splits_weight_and_body_fat_plausibility_columns`.
4. `storage/backfill.py`: `decouple_weigh_in_plausibility()` + a test
   reproducing the BO's exact evidence table as fixture rows inserted
   directly at schema v4 (7 known-bad rows + 123 rows at 81-88 kg with
   `body_fat_pct = 0` interleaved with normal readings, by timestamp) —
   migrate to v5, assert exactly the 7 end up with
   `is_weight_flagged_implausible = 1`, the 123 end up with
   `is_weight_flagged_implausible = 0` and `body_fat_pct IS NULL`, and no
   row's `bo_confirmed_valid`/`bo_confirmed_at` changed. This is AC4's
   proof.
5. Wire into `normalization/engine.py::_build_weigh_in_record()`; extend
   `tests/test_normalization_engine.py` for the four-key output shape and
   the independent-flagging case.
6. `sync/engine.py`: column renames in `_upsert_weigh_in()`, predicate
   rename in `_recent_weights_before()`, counter source change. Extend
   `test_sync_engine.py`: a body-fat-only-flagged reading still appears in
   a later record's `recent_weights_kg` lookup (the regression this issue
   is actually about, exercised at the sync-engine level, not just the
   pure-function level); `records_flagged_implausible` only counts
   weight-axis flags; a resync still never clears
   `bo_confirmed_valid`/`bo_confirmed_at`.
7. `scripts/confirm_weigh_in.py`: column rename; extend
   `test_confirm_weigh_in_script.py` for the renamed column, unchanged
   behavior otherwise.
8. `docs/trainiq/adr/ADR-039-weigh-in-plausibility-flagging.md`: add a
   "Correction (Issue #42)" section per Affected components/files above.
9. One `BACKLOG.md` line noting BL-009 is unaffected by v5 (same
   precedent as v4's own note).

## Test strategy notes

- **Core regression (AC6):** `weight_kg=82.2, body_fat_pct=0.0` against a
  baseline consistent with it (e.g. `[81.0, 82.0, 83.0, 84.0, 85.0]`) →
  weight axis plausible, body-fat axis flagged, and — at the
  `EufyConnector.normalize()` level — `body_fat_pct` normalized to `None`
  before it ever reaches the rule at all (so in practice the body-fat
  axis won't even trip for Eufy data post-fix; the pure-function-level
  test above exists so the rule is independently correct even if
  `body_fat_pct=0.0` reaches it directly, e.g. from a future connector
  that hasn't normalized its own sentinel).
- **#38 non-regression (AC7):** the original 7-row evidence (sub-40 kg
  weights, `body_fat_pct` 0.0 or 5.0) must still end up
  `is_weight_flagged_implausible = 1` — these trip the weight-deviation
  axis regardless of the body-fat axis's (now-independent) verdict.
- **Decoupling, not just sentinel cleanup:** a reading with a genuinely
  implausible non-zero body-fat value (e.g. `1.5`, between 0 and the 3.0
  floor) and a normal weight must flag only the body-fat axis — this is
  the case that proves the fix isn't just "handle zero as a special case"
  but actually decouples the two verdicts, per AC2/AC3.
- **Backfill correctness (AC4):** see Task breakdown item 4 — this is the
  one test that proves the full migration path against the BO's exact
  reported evidence shape, not just synthetic unit cases.
- **Rolling-baseline correctness:** a body-fat-only-flagged row's weight
  must still appear in a subsequent row's `recent_weights_kg` — this is
  the single most important behavioral assertion in this issue, since
  it's the concrete mechanism by which the old bug suppressed 123 good
  readings from ever contributing to anyone else's baseline either.
- Per existing repo convention: no live-account testing: everything above
  is fixture/constructed-dict based.

## Risks/tradeoffs

- **No confirm/override path for the body-fat axis.** `confirm_weigh_in.py`
  stays scoped to the weight axis, matching what the requirements and
  acceptance criteria actually ask for (AC5 names only
  `bo_confirmed_valid`/`bo_confirmed_at`, which gate analytics exclusion —
  a property of weight, not body-fat, under this design). A body-fat flag
  is visible in the database (queryable, auditable) but not analytics-
  excluding, so there's nothing today for a BO override to *restore*. If a
  future need arises to silence a body-fat false positive (e.g. a
  genuinely very lean athlete tripping the floor repeatedly), that's a new,
  separate capability — not a gap in this issue's scope.
- **`records_flagged_implausible` now undercounts "rows with any flag."**
  It counts analytics-excluding (weight) flags only, by design (see
  `sync/engine.py` section above). A sync where every record has a
  body-fat-only flag and zero weight flags would log "flagged 0
  implausible," which is correct for "nothing was excluded from
  analytics" but could read as "nothing unusual happened" even though 100%
  of records have a flagged body-fat field. Not fixed here since no
  acceptance criterion asks for body-composition-specific sync-health
  visibility; flagged here so a future issue asking for that doesn't
  have to rediscover this distinction from scratch.
- **Column rename over additive-only columns.** ADR-039's own precedent
  (v2, v4) was purely additive `ALTER TABLE ... ADD COLUMN`. This design
  renames two existing columns instead of leaving
  `is_flagged_implausible`/`plausibility_reason` in place and adding two
  more alongside them. The rename was chosen deliberately: the old names'
  *meaning* changed (a single combined verdict → one specific axis), and
  keeping the old names while quietly narrowing what they mean would be
  the more dangerous option — any code written against the old semantics
  later (a report, a one-off query) would silently read "is this weight
  bad" as "was anything about this record flagged," which is exactly
  today's bug in a new location. `ALTER TABLE RENAME COLUMN` is a single
  statement with no data loss, fully supported by this project's
  hand-rolled migration runner with no new machinery required.
- **Same cross-provider shared-baseline caveat ADR-039 already documents**
  (only Eufy exists today; a second weigh-in provider with a systematic
  calibration difference could look like an outlier against a blended
  median) — unaffected by, and not reconsidered as part of, this issue.
