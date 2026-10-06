# Architecture: Flag Implausible Eufy Weigh-Ins

**Issue:** #38
**Requirements:** `docs/trainiq/requirements/38-eufy-implausible-weigh-in-flagging.md`
**Related:** #1 (deci-kg conversion, already fixed — unaffected by this design)

## Approach

Add a **plausibility check** that runs wherever weigh-in records are built for
persistence, independent of provider. It is **relative to the athlete's own
history** (a rolling median of weight) plus an **absolute body-fat floor**,
per the requirements' explicit rejection of a fixed absolute kg range.

Per this codebase's existing separation of concerns
(`normalization/confidence.py`'s docstring: connectors never compute their
own scoring; `normalization/load.py`'s `TrainingLoadResult`-style "resolve to
a documented reason, never guess" idiom), the check is a new pure module,
**`trainiq/normalization/plausibility.py`**, parallel to `confidence.py` and
`load.py` — not inside `connectors/eufy.py`. This keeps the rule provider-
agnostic: any current or future connector with `record_kind ==
RecordKind.WEIGH_IN` gets it for free through `_build_weigh_in_record()`,
with no Eufy-specific code.

A flagged reading is **never deleted or altered** — `weight_kg`,
`body_fat_pct`, etc. are persisted exactly as received. Two new facts are
persisted alongside: whether the rule's own verdict is "implausible", and
whether the BO has since confirmed the reading as valid despite that
verdict. Analytics code excludes a row only when it's flagged **and** not
BO-confirmed, so a confirmed reading is auditably "flagged, but overridden"
rather than silently un-flagged (requirements AC6: "without losing the fact
that it was once flagged").

The one-time backfill (AC4) reuses the exact same pure plausibility function
as live sync, run once over all existing `weigh_ins` rows in timestamp order,
triggered automatically the first time the database migrates to schema v4 —
no separate script the BO has to remember to run, and no behavioral
difference between "flagged on first sync" and "flagged retroactively".

## Affected components/files

| File | Change |
|---|---|
| `trainiq/normalization/plausibility.py` | **New.** Pure function + result dataclass implementing the rule. |
| `trainiq/normalization/engine.py` | `_build_weigh_in_record()` gains a `recent_weights_kg` param; calls the new module and adds `is_flagged_implausible` / `plausibility_reason` to the returned dict. `build_canonical_record()` threads the new param through (ignored on the ACTIVITY path). |
| `trainiq/storage/schema.py` | New migration, version 4: adds `is_flagged_implausible`, `plausibility_reason`, `bo_confirmed_valid`, `bo_confirmed_at` columns to `weigh_ins`. `CURRENT_SCHEMA_VERSION = 4`. Does **not** touch BL-009 (`source_confidence`) — out of scope per requirements, left for its own migration. |
| `trainiq/storage/backfill.py` | **New.** `backfill_weigh_in_plausibility(conn)` — the one-time pass (AC4). Called from `migrate()` only on the v3→v4 transition. |
| `trainiq/sync/engine.py` | `SynchronizationEngine.sync_connector()`: for `RecordKind.WEIGH_IN`, looks up recent prior weights from the DB before calling `build_canonical_record`; `_upsert_weigh_in()` column lists extended; new `records_flagged_implausible` counter, threaded into `ConnectorSyncResult` and the summary log line. |
| `projects/trainiq/scripts/confirm_weigh_in.py` | **New.** The BO-facing un-flag CLI (AC6). |
| `docs/adr/ADR-039-weigh-in-plausibility-flagging.md` | **New** (Developer to write alongside implementation — see Task breakdown). Documents the rule and the exact threshold values, per CONSTITUTION.md Principle 8 (schema/contract changes are versioned ADRs) and the ADR-037/038 precedent. Required by requirements AC1 ("the rule is explicit and documented"), not optional. |
| `trainiq/BACKLOG.md` | Add a line cross-referencing BL-009 as "related but explicitly not addressed by issue #38" so the next person touching `weigh_ins` doesn't assume this migration covers it too. |

No change to `trainiq/connectors/eufy.py`, `trainiq/athlete/profile.py`, or
`trainiq/athlete/store.py`. The rolling median is **derived from stored
history**, not a persisted "fact" on `AthleteProfile` — consistent with
`profile.py`'s own docstring ("persistent facts only — everything else is
derived") and with how `training_load` is computed rather than stored.

## Interfaces/contracts

### `trainiq/normalization/plausibility.py` (new)

```python
from __future__ import annotations
from dataclasses import dataclass
from statistics import median
from typing import Optional, Sequence

DEFAULT_WEIGHT_DEVIATION_THRESHOLD_PCT = 25.0
DEFAULT_ROLLING_WINDOW_SIZE = 5          # how many prior readings feed the median
DEFAULT_MIN_HISTORY_FOR_WEIGHT_CHECK = 3  # below this, skip the weight-deviation check entirely
DEFAULT_BODY_FAT_FLOOR_PCT = 3.0          # essential-fat floor; 0 and the BO's observed 5.0-on-outliers
                                           # are caught primarily by the weight check, not this floor —
                                           # see Risks/tradeoffs

@dataclass(frozen=True)
class WeighInPlausibility:
    is_plausible: bool
    reason: Optional[str]  # None iff is_plausible; else a human-readable explanation


def evaluate_weigh_in_plausibility(
    weight_kg: Optional[float],
    body_fat_pct: Optional[float],
    recent_weights_kg: Sequence[float],
    *,
    deviation_threshold_pct: float = DEFAULT_WEIGHT_DEVIATION_THRESHOLD_PCT,
    min_history: int = DEFAULT_MIN_HISTORY_FOR_WEIGHT_CHECK,
    body_fat_floor_pct: float = DEFAULT_BODY_FAT_FLOOR_PCT,
) -> WeighInPlausibility:
    """Pure function, no I/O. `recent_weights_kg` must already be the
    athlete's prior UNFLAGGED (or BO-confirmed-valid) readings strictly
    before this one's timestamp, newest-first, already limited to the
    rolling window — this function does no filtering/ordering itself.

    Never fabricates a verdict from insufficient data (Constitution
    Principle 1): with fewer than `min_history` prior readings, the
    weight-deviation axis is skipped, not assumed-plausible via a default
    baseline.
    """
```

Rule (both axes are independent; either flags):

1. `body_fat_pct is not None and body_fat_pct <= body_fat_floor_pct` → implausible, reason names the floor.
2. `weight_kg is not None and len(recent_weights_kg) >= min_history`: let `baseline = median(recent_weights_kg)`; if `baseline > 0` and `abs(weight_kg - baseline) / baseline * 100 > deviation_threshold_pct` → implausible, reason names the deviation and baseline.
3. Otherwise → plausible, `reason=None`.

Boundary is strict `>` — a reading exactly at the threshold is **not** flagged (deterministic, tested explicitly per AC7c).

### `trainiq/normalization/engine.py` changes

```python
def build_canonical_record(
    provider: str,
    record_kind: RecordKind,
    normalized: dict[str, Any],
    athlete_profile: Optional[AthleteProfile] = None,
    recent_weights_kg: Sequence[float] = (),
) -> dict[str, Any]:
    if record_kind == RecordKind.ACTIVITY:
        return _build_activity_record(provider, normalized, athlete_profile)
    return _build_weigh_in_record(provider, normalized, recent_weights_kg)


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
        "is_flagged_implausible": not verdict.is_plausible,
        "plausibility_reason": verdict.reason,
    }
```

`is_flagged_implausible`/`plausibility_reason` are plain dict keys like every
other field here — no new typed model introduced (BL-002, keeping a plain
dict, is still open and this doesn't change that convention).

### Schema migration v4 (`trainiq/storage/schema.py`)

```python
CURRENT_SCHEMA_VERSION = 4

_MIGRATIONS[4] = """
-- ADR-039 / Issue #38: weigh-in plausibility flagging.
ALTER TABLE weigh_ins ADD COLUMN is_flagged_implausible INTEGER NOT NULL DEFAULT 0;
ALTER TABLE weigh_ins ADD COLUMN plausibility_reason TEXT;
ALTER TABLE weigh_ins ADD COLUMN bo_confirmed_valid INTEGER NOT NULL DEFAULT 0;
ALTER TABLE weigh_ins ADD COLUMN bo_confirmed_at TEXT;
"""
```

Four single-purpose columns, matching the ADR-038 `ALTER TABLE` precedent
(one migration, several related columns, one comment naming the authorizing
ADR/issue). `is_flagged_implausible`/`plausibility_reason` are the rule's own
verdict and are **overwritten on every re-evaluation** (e.g. Eufy's full
resync, BL-006) since the rolling median can shift as history grows.
`bo_confirmed_valid`/`bo_confirmed_at` are BO-owned and must never be written
by sync code — `_upsert_weigh_in()`'s column lists exclude them entirely, so
a resync cannot silently revert a confirmation.

Analytics predicate (wherever `weigh_ins` is read for trends):
```sql
WHERE is_flagged_implausible = 0 OR bo_confirmed_valid = 1
```

### `trainiq/storage/backfill.py` (new)

```python
def backfill_weigh_in_plausibility(conn: sqlite3.Connection) -> int:
    """Evaluates every existing weigh_ins row against
    evaluate_weigh_in_plausibility(), processing rows in ascending
    timestamp order (across all providers) so each row's rolling window is
    built only from rows that are chronologically prior to it — identical
    semantics to the live-sync path. Returns the number of rows flagged.

    Idempotent by construction (deterministic given the same stored data),
    but only ever invoked once, from migrate()'s v3->v4 transition, so it
    runs exactly once per database.
    """
```

Called from `storage/schema.py::migrate()`:
```python
for version in range(current + 1, target + 1):
    script = _MIGRATIONS.get(version)
    if script is None:
        raise RuntimeError(f"No migration defined for version {version}")
    conn.executescript(script)
    if version == 4:
        backfill_weigh_in_plausibility(conn)
    conn.execute("UPDATE schema_version SET version = ?", (version,))
    conn.commit()
```

### `trainiq/sync/engine.py` changes

`ConnectorSyncResult` gains one additive field, appended at the end (matches
the RC1-HF-006 precedent — never reorder/rename existing fields):
```python
@dataclass
class ConnectorSyncResult:
    ...
    records_skipped: int = 0
    records_flagged_implausible: int = 0
```

New private helper, used only for `RecordKind.WEIGH_IN` connectors:
```python
def _recent_weights_before(self, timestamp: str, limit: int) -> list[float]:
    rows = self._conn.execute(
        "SELECT weight_kg FROM weigh_ins "
        "WHERE weight_kg IS NOT NULL AND timestamp < ? "
        "AND (is_flagged_implausible = 0 OR bo_confirmed_valid = 1) "
        "ORDER BY timestamp DESC LIMIT ?",
        (timestamp, limit),
    ).fetchall()
    return [r[0] for r in rows]
```

Deliberately **not** filtered by `provider` — the rolling median is of the
athlete's weight, not of a single device's readings, per requirements
("relative to the individual athlete"). If a second weigh-in provider is
added later, its readings feed and are checked against the same shared
baseline.

In `sync_connector()`'s per-record loop, for `connector.record_kind ==
RecordKind.WEIGH_IN`:
```python
recent = self._recent_weights_before(normalized["timestamp"], DEFAULT_ROLLING_WINDOW_SIZE)
canonical = build_canonical_record(provider, connector.record_kind, normalized, self._athlete_profile, recent)
...
if canonical.get("is_flagged_implausible"):
    flagged_implausible_count += 1
```
(Still inside the existing `try/except (KeyError, TypeError, ValueError)`
boundary around `build_canonical_record` — the plausibility function itself
never raises, it's a pure total function over its inputs, so this doesn't
change the existing error-handling contract.)

`_upsert_weigh_in()` INSERT/UPDATE column lists extended with
`is_flagged_implausible, plausibility_reason` only (never
`bo_confirmed_valid`/`bo_confirmed_at`, as above).

Summary log line (exact current format, one clause appended):
```python
summary_logger().info(
    f"{provider}: downloaded {len(raw_records)}, "
    f"inserted {inserted_count}, updated {updated_count}, "
    f"malformed {skipped_malformed}, skipped {skipped_no_external_id}, "
    f"flagged {flagged_implausible_count} implausible"
)
```
Always appended (not conditional like the BL-006 clause) — `flagged 0
implausible` is informative for every connector, including ACTIVITY-kind
ones where it will always read 0.

### `projects/trainiq/scripts/confirm_weigh_in.py` (new, un-flag mechanism, AC6)

CLI, following the existing `scripts/` convention (standalone, argparse-based
like the OAuth setup scripts):
```
python -m trainiq.scripts.confirm_weigh_in --db PATH/TO/trainiq.db \
    --provider eufy --external-id <id> [--note "BO confirmed: real reading"]
```
Behavior:
- Looks up the row by `(provider, external_id)` (the table's existing unique
  key — no new identifier needed).
- Errors (no DB write) if no such row exists, or if it isn't currently
  `is_flagged_implausible = 1` (nothing to confirm — avoids silently
  "confirming" a reading that was never flagged, which would be meaningless).
- Sets `bo_confirmed_valid = 1`, `bo_confirmed_at = <now, UTC ISO8601>`.
- Prints the row's original `plausibility_reason` back to the BO as
  confirmation of what's being overridden, before and after the write —
  this is the "auditable" requirement: the BO sees exactly what they're
  overriding, and the override is additive (original flag/reason untouched
  in the row).
- No un-confirm path in this slice — reversing a confirmation isn't in the
  acceptance criteria; if needed later it's a one-line follow-up
  (`bo_confirmed_valid = 0`), not designed here to avoid scope creep.

## Task breakdown

1. Migration v4 in `storage/schema.py` (columns only, no logic) +
   `test_storage.py::test_v4_migration_adds_plausibility_columns_to_weigh_ins`.
2. `normalization/plausibility.py` + `tests/test_plausibility.py` (pure
   function, no DB): normal/outlier/borderline/insufficient-history/body-fat-floor
   cases, directly exercising `evaluate_weigh_in_plausibility()`.
3. Wire into `normalization/engine.py::_build_weigh_in_record()` +
   `build_canonical_record()`; extend `tests/test_normalization_engine.py`
   with the three AC7 cases (normal/outlier/borderline) at the
   `build_canonical_record()` level, reusing the existing plain-dict style.
4. `storage/backfill.py` + test reproducing the issue's exact evidence table
   as fixture rows (insert via raw SQL at schema v3, then run `migrate()` to
   v4, assert exactly the 7 known-bad rows end up flagged and no 80+kg row
   does) — this is the AC4 proof.
5. `sync/engine.py`: `_recent_weights_before()`, `ConnectorSyncResult` field,
   summary line, `_upsert_weigh_in()` column extension. Extend
   `test_sync_engine.py` for: the new summary clause, the counter, and that
   a resync never clears `bo_confirmed_valid`/`bo_confirmed_at` on an
   already-confirmed row.
   **Risk to verify explicitly in a test:** because Eufy's
   `supports_incremental_sync = False` means every sync reprocesses full
   history (BL-006), and `download()`'s return order isn't guaranteed
   chronological, process `raw_records` sorted by `normalized["timestamp"]`
   ascending before this loop when `record_kind == RecordKind.WEIGH_IN`, so
   `_recent_weights_before()` lookups are evaluated in the same
   chronological order the backfill used — otherwise a sync's flagging
   results could depend on download order rather than solely on the data.
6. `scripts/confirm_weigh_in.py` + a small script test, following the
   `test_debug_eufy_script.py` / `test_setup_peloton_oauth_script.py`
   pattern already in `tests/`.
7. `docs/adr/ADR-039-weigh-in-plausibility-flagging.md`: records the exact
   constants chosen here (±25%, window of 5, floor of 3.0%) and why, per
   Principle 8. Short — this design doc is the source of truth; the ADR is
   the permanent record once implemented.
8. One `BACKLOG.md` line noting BL-009 remains open and distinct from this
   migration.

## Test strategy notes

- **Normal reading, not flagged:** `weight_kg=85.0, body_fat_pct=18.0`,
  `recent_weights_kg=[84.0, 86.0, 85.5, 83.5, 85.2]` → plausible.
- **Obvious outlier, flagged (reproduces the issue's evidence):**
  `weight_kg=20.7, body_fat_pct=5.0`, same baseline → median ≈85, deviation
  ≈75.6% ≫ 25% → flagged, reason names the weight-deviation axis (body-fat
  floor of 3.0% doesn't independently trigger at 5.0 — the weight axis alone
  is sufficient and is what actually catches all 7 of the BO's known rows).
- **Borderline, deterministic:** with median 85.0 and a 25% threshold, test
  both `weight_kg=63.76` (deviation 25.0 − ε%, just under → not flagged) and
  `weight_kg=63.0` (deviation 25.88% → flagged), pinning the strict-`>`
  boundary behavior exactly (AC7c: "whichever way the rule resolves it" —
  this doc fixes that answer).
- **Insufficient history:** fewer than 3 prior readings → weight axis
  skipped entirely regardless of value; only the body-fat floor can flag.
- **Body-fat-only outlier:** normal weight, `body_fat_pct=0.0` → flagged
  independent of the weight axis.
- **Backfill correctness (AC4):** fixture rows reproducing the issue's exact
  evidence table (7 bad rows interleaved with normal 80–88.3 kg rows, by
  timestamp) inserted directly at schema v3; after `migrate()` to v4, assert
  exactly those 7 are flagged and nothing else is.
- **Regression:** full existing suite (`test_normalization_engine.py`,
  `test_sync_engine.py`, `test_storage.py`, `test_eufy_connector.py`) must
  still pass unchanged (AC8) — none of the existing tests construct
  `weigh_ins` rows with the new columns, so defaults (`0`/`NULL`) must make
  every pre-existing assertion still hold.
- Per the existing repo convention: no live-account testing — everything
  above is fixture/constructed-dict based, consistent with
  `test_normalization_engine.py`'s and `test_eufy_connector.py`'s existing
  style (plain literals, constructor-injected fakes, no network).

## Risks/tradeoffs

- **Threshold values (±25%, window of 5, min-history of 3, body-fat floor of
  3.0%) are a judgment call**, not derived from a larger dataset — the issue
  only gives us 568 points from one athlete. They're chosen to comfortably
  separate the BO's ~80–88 kg baseline from the ~19–36 kg outliers (a
  75%+ gap) while being loose enough not to flag ordinary week-to-week
  weight fluctuation or a genuine multi-month trend (the median adapts as
  real change accumulates one reading at a time). Documented explicitly in
  ADR-039 so they can be revisited with real data rather than silently
  re-tuned.
- **Body-fat floor is intentionally conservative (3.0%, not sex-adjusted).**
  `AthleteProfile.sex` exists and a sex-aware floor (e.g. ~10% for female)
  would catch more bad readings on that axis alone, but risks false-
  positives for a genuinely very lean athlete. Since the weight-deviation
  axis already catches all 7 of the BO's known records independently, the
  floor is kept as a conservative second signal rather than tuned to this
  one case — a future issue can revisit this if a bad reading ever has
  *both* a plausible weight and a body_fat_pct between 3% and a
  sex-appropriate floor.
- **Cross-provider shared baseline:** if a second weigh-in source is ever
  added, a systematic calibration difference between two scales (not just
  noise) could cause one provider's otherwise-normal readings to look like
  outliers relative to a median blended across both. Not a concern for this
  issue (only Eufy exists today) but worth a comment in the code pointing
  future readers here.
- **Order-dependent flagging on a from-empty-DB first sync** (see Task
  breakdown item 5) is a real edge case the design closes by sorting the
  in-run batch chronologically before evaluation, but it's worth calling out
  explicitly as the one place this design's "query committed history, not
  in-memory batch state" simplification needed an extra rule to stay fully
  order-independent.
- **No un-confirm path** for `confirm_weigh_in.py` is a deliberate scope cut
  (see script contract above), not an oversight.
