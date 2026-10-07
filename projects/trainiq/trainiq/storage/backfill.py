"""
trainiq.storage.backfill — ADR-039 / Issue #38

One-time backfill of the weigh-in plausibility rule (AC4) against rows
already sitting in the database before this feature existed. Reuses the
exact same pure plausibility function the live-sync path uses
(`evaluate_weigh_in_plausibility`), run once over every existing
`weigh_ins` row in chronological order, so each row's rolling window is
built only from rows strictly prior to it — identical semantics to the
live-sync path (`sync/engine.py::_recent_weights_before`).

Invoked exactly once per database, from `storage/schema.py::migrate()`'s
v3->v4 transition — not a script the BO has to remember to run.
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

        if not verdict.is_plausible:
            flagged_count += 1
            conn.execute(
                "UPDATE weigh_ins SET is_flagged_implausible = 1, plausibility_reason = ? WHERE id = ?",
                (verdict.reason, row_id),
            )
        elif weight_kg is not None:
            recent_weights.append(weight_kg)

    return flagged_count
