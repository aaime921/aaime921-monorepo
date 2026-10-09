#!/usr/bin/env python3
"""
scripts/backfill_peloton_workout_details.py — one-off, resumable,
rate-limited backfill of Peloton class metadata (issue #46) AND distance
(issue #57) for already-stored workouts.

Renamed + extended from scripts/backfill_peloton_class_metadata.py: issue
#57's distance fix needs a real per-workout network fetch
(performance_graph), the same category of requirement issue #46's class
metadata already had — so both independently-missing concerns now share
this one resumable per-row mechanism instead of issue #57 writing a
second, separate backfill script. #47 (HR/power, not yet implemented)
should extend this script further rather than add a third.

Deliberately NOT built on trainiq.normalization.renormalize.renormalize_provider()
(issue #36/#45's pattern): that function's entire contract is "re-derive a
canonical record from an already-stored raw payload with zero new network
I/O." Both class metadata and distance require a genuinely new network
call per row (PelotonConnector.fetch_class_details() /
fetch_workout_performance()), which renormalize_provider() has no hook for
and should not grow one for — it would stop being a pure offline
recomputation for every other caller too.

Instead, this script walks existing `normalized_activities` rows for
provider="peloton" where EITHER `class_type IS NULL` OR
`performance_fetch_status IS NULL` (i.e. never attempted by the
corresponding feature's download() pass), ordered by `id` for a stable
walk. Each row's two concerns are processed independently — a row can
need only one of the two — and written via `apply_class_metadata_update()`
/ `apply_distance_update()`, the same narrow UPDATE-only pattern for each.

Resumability (AC6 / issue #57's AC4): commits after each branch of each
row, not in a batch at the end. An interrupted run loses at most the one
in-flight lookup; everything already written stays written, and the next
run's selection query naturally excludes already-processed branches (both
successes and non-class sentinels for the class branch — only genuine
failures, `class_type = 'lookup_failed'` / `performance_fetch_status =
'failed'`, are eligible for re-attempt, and only when `--retry-failed` is
passed).

Rate limiting: the class-detail branch goes through
trainiq.sync.engine.retry_with_backoff() explicitly (ADR-037), same as
before. The distance branch calls
PelotonConnector.fetch_workout_performance(), which already retries
internally (see that method's own docstring) — this script does not wrap
it a second time.

Per this project's live-verification constraint, this script is only unit-
tested against a fake connector/fixtures in CI (tests/
test_backfill_peloton_workout_details.py) — actually running it against
the BO's real Peloton workouts is an ops task, explicitly out of scope
for this pipeline (requirements docs, "Out of scope").

Run against your real database once this ships:

    python3 scripts/backfill_peloton_workout_details.py
    python3 scripts/backfill_peloton_workout_details.py --retry-failed
    python3 scripts/backfill_peloton_workout_details.py --limit 50
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from trainiq.connectors.peloton import (
    CLASS_TITLE_FIELD,
    CLASS_TYPE_LOOKUP_FAILED,
    CLASS_TYPE_NOT_A_CLASS,
    CLASS_TYPE_RAW_FIELD,
    PERFORMANCE_FETCH_STATUS_FAILED,
    PERFORMANCE_FETCH_STATUS_OK,
    PLANNED_DURATION_FIELD,
    PROVIDER,
    RIDE_ID_FIELD,
    PelotonConnector,
    _DISTANCE_UNIT_MULTIPLIERS,
    _extract_instructor_name,
    _is_class_workout,
    _resolve_distance_unit_token,
    apply_class_metadata_update,
    apply_distance_update,
)
from trainiq.credentials.store import CredentialStore
from trainiq.logging_setup import diagnostic_logger
from trainiq.storage.schema import open_db
from trainiq.sync.engine import retry_with_backoff

APP_SUPPORT_DIR = Path.home() / "Library" / "Application Support" / "TrainIQ"
DEFAULT_DB_PATH = APP_SUPPORT_DIR / "trainiq.db"


def _select_candidates(conn, retry_failed: bool, limit: int | None) -> list[dict]:
    """Returns [{id, external_id, payload_json, class_type,
    performance_fetch_status}], ordered by raw_activities.id, for a
    stable resumable walk. A row is a candidate if EITHER its class
    metadata OR its performance/distance fetch has never been attempted
    (class_type IS NULL / performance_fetch_status IS NULL) —
    --retry-failed additionally includes rows where either branch was
    attempted but failed. _process_row() independently re-checks each
    branch's own condition per row, since a row can need only one of the
    two."""
    query = (
        "SELECT ra.id AS id, na.external_id AS external_id, ra.payload_json AS payload_json, "
        "na.class_type AS class_type, na.performance_fetch_status AS performance_fetch_status "
        "FROM raw_activities ra "
        "JOIN normalized_activities na "
        "  ON na.provider = ra.provider AND na.external_id = ra.external_id "
        "WHERE ra.provider = ? AND (na.class_type IS NULL OR na.performance_fetch_status IS NULL"
    )
    params: tuple = (PROVIDER,)
    if retry_failed:
        query += " OR na.class_type = ? OR na.performance_fetch_status = ?"
        params = params + (CLASS_TYPE_LOOKUP_FAILED, PERFORMANCE_FETCH_STATUS_FAILED)
    query += ")"
    query += " ORDER BY ra.id"
    if limit is not None:
        query += " LIMIT ?"
        params = params + (limit,)
    return [dict(row) for row in conn.execute(query, params).fetchall()]


def _process_class_metadata(
    conn, connector: PelotonConnector, raw: dict, external_id: str,
    retry_failed: bool, class_type: str | None, max_retries: int, sleep_fn,
) -> str:
    """Independent class-metadata branch (issue #46, unchanged behavior).
    Returns "skipped" if this row's class_type doesn't need
    (re-)attempting, else one of "not_a_class"/"success"/"failed"."""
    needs_attempt = class_type is None or (retry_failed and class_type == CLASS_TYPE_LOOKUP_FAILED)
    if not needs_attempt:
        return "skipped"

    if not _is_class_workout(raw):
        apply_class_metadata_update(conn, external_id, {"class_type": CLASS_TYPE_NOT_A_CLASS})
        conn.commit()
        return "not_a_class"

    # retry_with_backoff/fetch_class_details let TransientError (retries
    # exhausted) and AuthenticationError propagate uncaught — a genuinely
    # unrecoverable condition for THIS run, not a per-row "lookup failed"
    # outcome (every subsequent ride_id would fail identically). Letting
    # the script crash here is what makes AC6's resume behavior correct:
    # every row already committed above stays committed, and the next
    # invocation's candidate selection naturally skips them.
    ride_id = raw.get(RIDE_ID_FIELD)
    details = retry_with_backoff(
        lambda: connector.fetch_class_details(ride_id), PROVIDER, max_retries=max_retries, sleep_fn=sleep_fn
    )

    if details is None:
        diagnostic_logger().warning(f"{PROVIDER}: class lookup failed for ride_id={ride_id!r}")
        apply_class_metadata_update(conn, external_id, {"class_type": CLASS_TYPE_LOOKUP_FAILED})
        conn.commit()
        return "failed"

    apply_class_metadata_update(
        conn,
        external_id,
        {
            "activity_title": details.get(CLASS_TITLE_FIELD),
            "instructor_name": _extract_instructor_name(details),
            "class_type": details.get(CLASS_TYPE_RAW_FIELD),
            "planned_duration_s": details.get(PLANNED_DURATION_FIELD),
            "provider_class_id": ride_id,
        },
    )
    conn.commit()
    return "success"


def _process_distance(
    conn, connector: PelotonConnector, raw: dict, external_id: str,
    retry_failed: bool, performance_fetch_status: str | None,
) -> str:
    """Independent distance branch (issue #57).
    fetch_workout_performance() already retries/degrades internally (see
    that method's own docstring), so this does not wrap it a second time.
    Returns "skipped" if this row's performance_fetch_status doesn't need
    (re-)attempting. Otherwise "failed" only for the two real failure
    modes — the fetch itself failing, or a workout that genuinely has a
    raw `distance` value but the fetch couldn't resolve it (same warning
    normalize() raises for this case) — and "success" whenever
    performance_fetch_status is recorded as ok, including a workout with
    no distance at all (meditation, strength), which is not a failure."""
    needs_attempt = performance_fetch_status is None or (
        retry_failed and performance_fetch_status == PERFORMANCE_FETCH_STATUS_FAILED
    )
    if not needs_attempt:
        return "skipped"

    performance = connector.fetch_workout_performance(raw.get("id"))
    if performance is None:
        apply_distance_update(conn, external_id, {"performance_fetch_status": PERFORMANCE_FETCH_STATUS_FAILED})
        conn.commit()
        return "failed"

    distance_value = performance.get("distance_value")
    unit_token = _resolve_distance_unit_token(performance.get("distance_unit_raw"))
    multiplier = _DISTANCE_UNIT_MULTIPLIERS.get(unit_token)
    if distance_value is not None and multiplier is not None:
        distance_m = distance_value * multiplier
        outcome = "success"
    elif raw.get("distance") is not None:
        # A real workout-list distance exists but the performance fetch's
        # own summary didn't resolve to a usable value — same fail-safe
        # normalize() applies: logged, stored as NULL, never guessed.
        diagnostic_logger().warning(
            f"{PROVIDER}: performance fetch succeeded but yielded no usable distance "
            f"summary (external_id={external_id!r}) — distance_m stored as NULL, not guessed"
        )
        distance_m = None
        outcome = "failed"
    else:
        # Genuinely no distance on this workout (meditation, strength) —
        # not a fetch/unit problem, never warn, not a failure.
        distance_m = None
        outcome = "success"

    apply_distance_update(
        conn, external_id,
        {"distance_m": distance_m, "performance_fetch_status": PERFORMANCE_FETCH_STATUS_OK},
    )
    conn.commit()
    return outcome


def run_backfill(conn, connector: PelotonConnector, retry_failed: bool = False, limit: int | None = None,
                  max_retries: int = 3, sleep_fn=None) -> dict[str, int]:
    """Importable entry point (used directly by tests). Returns summary
    counts, split per branch since a row's two concerns are resolved
    independently. sleep_fn defaults to time.sleep when not supplied (used
    only by the class-metadata branch's explicit retry_with_backoff() call
    — the distance branch's retry lives inside
    fetch_workout_performance() itself, governed by the connector's own
    injected sleep_fn)."""
    import time as _time

    sleep_fn = sleep_fn if sleep_fn is not None else _time.sleep
    rows = _select_candidates(conn, retry_failed, limit)

    counts = {
        "processed": 0,
        "class_not_a_class": 0, "class_success": 0, "class_failed": 0, "class_skipped": 0,
        "distance_success": 0, "distance_failed": 0, "distance_skipped": 0,
    }
    for row in rows:
        raw = json.loads(row["payload_json"])
        external_id = row["external_id"]

        class_outcome = _process_class_metadata(
            conn, connector, raw, external_id, retry_failed, row["class_type"], max_retries, sleep_fn
        )
        distance_outcome = _process_distance(
            conn, connector, raw, external_id, retry_failed, row["performance_fetch_status"]
        )

        counts["processed"] += 1
        counts[f"class_{class_outcome}"] += 1
        counts[f"distance_{distance_outcome}"] += 1
    return counts


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(
        description="Backfill Peloton class metadata (issue #46) and distance (issue #57) "
                     "for already-stored workouts."
    )
    parser.add_argument("--db-path", type=Path, default=DEFAULT_DB_PATH)
    parser.add_argument(
        "--retry-failed", action="store_true",
        help="Also re-attempt rows whose previous lookup failed "
             "(class_type == 'lookup_failed' and/or performance_fetch_status == 'failed').",
    )
    parser.add_argument(
        "--limit", type=int, default=None,
        help="Cap how many workouts this invocation attempts, to budget calls across multiple manual runs.",
    )
    args = parser.parse_args()

    print(f"Database: {args.db_path}")
    print()

    conn = open_db(args.db_path)
    try:
        credential_store = CredentialStore(conn=conn)
        connector = PelotonConnector(credential_store)
        connector.authenticate()
        counts = run_backfill(conn, connector, retry_failed=args.retry_failed, limit=args.limit)
    finally:
        conn.close()

    print("=" * 40)
    print("BACKFILL RESULT")
    print("=" * 40)
    print(f"processed:         {counts['processed']}")
    print(f"class_not_a_class: {counts['class_not_a_class']}")
    print(f"class_success:     {counts['class_success']}")
    print(f"class_failed:      {counts['class_failed']}")
    print(f"class_skipped:     {counts['class_skipped']}")
    print(f"distance_success:  {counts['distance_success']}")
    print(f"distance_failed:   {counts['distance_failed']}")
    print(f"distance_skipped:  {counts['distance_skipped']}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
