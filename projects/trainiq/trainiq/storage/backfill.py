"""
trainiq.storage.backfill — ADR-039 / Issue #38, corrected by Issue #42

One-time backfill of the weigh-in plausibility rule (AC4) against rows
already sitting in the database before this feature existed. Reuses the
exact same pure plausibility function the live-sync path uses
(`evaluate_weigh_in_plausibility`), run once over every existing
`weigh_ins` row in chronological order, so each row's rolling window is
built only from rows strictly prior to it — identical semantics to the
live-sync path (`sync/engine.py::_recent_weights_before`).

`backfill_weigh_in_plausibility()` is invoked exactly once per database,
from `storage/schema.py::migrate()`'s v3->v4 transition.
`decouple_weigh_in_plausibility()` (Issue #42) is invoked exactly once per
database, from the v4->v5 transition — not a script the BO has to
remember to run.
"""

from __future__ import annotations

import sqlite3

from trainiq.normalization.plausibility import (
    DEFAULT_ROLLING_WINDOW_SIZE,
    evaluate_weigh_in_plausibility,
)


def backfill_weigh_in_plausibility(conn: sqlite3.Connection) -> int:
    """Evaluates every existing `weigh_ins` row against
    `evaluate_weigh_in_plausibility()`, processing rows in ascending
    timestamp order (across all providers, per the requirements' "relative
    to the individual athlete" framing — not per-provider) so each row's
    rolling window is built only from rows that are chronologically prior
    to it. Returns the number of rows flagged.

    Idempotent by construction (deterministic given the same stored data),
    but only ever invoked once, from migrate()'s v3->v4 transition, so it
    runs exactly once per database.

    ISSUE #42: `evaluate_weigh_in_plausibility()`'s return shape changed
    (one combined verdict -> independent weight/body-fat verdicts) as part
    of that issue's correction. This function's own logic is otherwise
    unchanged from #38 — it still combines the two axes with OR into the
    single `is_flagged_implausible`/`plausibility_reason` pair those
    columns meant at schema v4, which is still what they mean at the
    moment this step runs (the v4->v5 rename/split happens in the very
    next migration step). Whatever this step writes is fully recomputed
    and overwritten by `decouple_weigh_in_plausibility()` immediately
    after, within the same `migrate()` call — this adaptation exists only
    so a fresh v0->v5 migration doesn't crash on the old attribute names,
    not to change this step's historical result.
    """
    rows = conn.execute(
        "SELECT id, weight_kg, body_fat_pct FROM weigh_ins ORDER BY timestamp ASC"
    ).fetchall()

    recent_weights: list[float] = []
    flagged_count = 0
    for row in rows:
        row_id, weight_kg, body_fat_pct = row[0], row[1], row[2]
        window = recent_weights[-DEFAULT_ROLLING_WINDOW_SIZE:]
        verdict = evaluate_weigh_in_plausibility(weight_kg, body_fat_pct, window)
        is_plausible = verdict.is_weight_plausible and verdict.is_body_fat_plausible

        if not is_plausible:
            flagged_count += 1
            reason = verdict.weight_reason if not verdict.is_weight_plausible else verdict.body_fat_reason
            conn.execute(
                "UPDATE weigh_ins SET is_flagged_implausible = 1, plausibility_reason = ? WHERE id = ?",
                (reason, row_id),
            )
        elif weight_kg is not None:
            recent_weights.append(weight_kg)

    return flagged_count


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
    for row in rows:
        row_id, weight_kg, body_fat_pct = row[0], row[1], row[2]
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
