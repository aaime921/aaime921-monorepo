#!/usr/bin/env python3
"""
scripts/backfill_peloton_workout_details.py — one-off, resumable,
rate-limited backfill of Peloton per-workout detail for already-stored
workouts. Covers two independently-missing concerns through the same
per-row mechanism:

  - class metadata (issue #46): activity_title/instructor_name/class_type/
    planned_duration_s/provider_class_id, via
    PelotonConnector.fetch_class_details().
  - avg/max HR + max power (issue #47, AC2/AC4): avg_hr/max_hr/max_power/
    performance_fetch_status, via
    PelotonConnector.fetch_workout_performance().

Renamed from scripts/backfill_peloton_class_metadata.py (issue #46's
original name) as part of issue #47's implementation, per the BO's
explicit directive that both features "share one fetch/backfill mechanism
instead of building it twice" (see docs/trainiq/architecture/
47-peloton-heart-rate-capture.md, "Backfill tool"). The rename reflects
that this script now backfills two independent concerns through the same
per-row mechanism, not that the mechanism itself changed.

Deliberately NOT built on trainiq.normalization.renormalize.renormalize_provider()
(issue #36/#45's pattern): that function's entire contract is "re-derive a
canonical record from an already-stored raw payload with zero new network
I/O." Both concerns here require a genuinely new network call per row,
which renormalize_provider() has no hook for and should not grow one for
— it would stop being a pure offline recomputation for every other caller
too.

Instead, this script walks existing `normalized_activities` rows for
provider="peloton" where EITHER concern is still missing/retryable
(`class_type IS NULL` OR `performance_fetch_status IS NULL`, each with its
own `--retry-failed` extension), ordered by `id` for a stable walk. Each
row's two concerns are resolved independently — a row missing only one of
the two only does that one's network call — and written via
`apply_class_metadata_update()` / `apply_hr_performance_update()`, the same
narrow UPDATE-only paths a live sync uses (see their docstrings in
trainiq/connectors/peloton.py).

Resumability (AC4/AC6): commits after every row, not in a batch at the
end, regardless of which concern(s) ran for that row. An interrupted run
loses at most the one or two in-flight lookups for the row in progress;
everything already written stays written, and the next run's selection
naturally excludes already-resolved concerns (both successes and
non-class/non-HR sentinels — only genuine failures, `class_type =
'lookup_failed'` or `performance_fetch_status = 'failed'`, are eligible
for re-attempt, and only when `--retry-failed` is passed — one flag
covers both, since both mean "retry a previously-failed attempt").

Rate limiting: each network call goes through
trainiq.sync.engine.retry_with_backoff() (ADR-037), reusing the exact same
policy a live sync already uses — honors a provider-directed Retry-After
when the connector raises TransientError with one, exponential backoff
otherwise.

Per this project's live-verification constraint, this script is only unit-
tested against a fake connector/fixtures in CI (tests/
test_backfill_peloton_workout_details.py) — actually running it against
the BO's 136 real Peloton workouts is an ops task, explicitly out of scope
for this pipeline (requirements doc, "Out of scope").

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
    _extract_instructor_name,
    _is_class_workout,
    apply_class_metadata_update,
    apply_hr_performance_update,
)
from trainiq.credentials.store import CredentialStore
from trainiq.logging_setup import diagnostic_logger
from trainiq.storage.schema import open_db
from trainiq.sync.engine import retry_with_backoff

APP_SUPPORT_DIR = Path.home() / "Library" / "Application Support" / "TrainIQ"
DEFAULT_DB_PATH = APP_SUPPORT_DIR / "trainiq.db"


def _select_candidates(conn, retry_failed: bool, limit: int | None) -> list[dict]:
    """Returns [{id, external_id, payload_json, class_type,
    performance_fetch_status}], ordered by id, for a stable resumable
    walk. A row is a candidate if EITHER concern is still missing
    ("never attempted" — class_type/performance_fetch_status IS NULL) or,
    with --retry-failed, previously failed. _process_row() below re-checks
    each concern's own current value independently, so a row selected for
    one concern that already has the other resolved does not re-fetch it."""
    query = (
        "SELECT ra.id AS id, na.external_id AS external_id, ra.payload_json AS payload_json, "
        "na.class_type AS class_type, na.performance_fetch_status AS performance_fetch_status "
        "FROM raw_activities ra "
        "JOIN normalized_activities na "
        "  ON na.provider = ra.provider AND na.external_id = ra.external_id "
        "WHERE ra.provider = ? AND (na.class_type IS NULL OR na.performance_fetch_status IS NULL"
    )
    if retry_failed:
        query += " OR na.class_type = ? OR na.performance_fetch_status = ?)"
        params: tuple = (PROVIDER, CLASS_TYPE_LOOKUP_FAILED, PERFORMANCE_FETCH_STATUS_FAILED)
    else:
        query += ")"
        params = (PROVIDER,)
    query += " ORDER BY ra.id"
    if limit is not None:
        query += " LIMIT ?"
        params = params + (limit,)
    return [dict(row) for row in conn.execute(query, params).fetchall()]


def _needs_attempt(current_value: str | None, failed_sentinel: str, retry_failed: bool) -> bool:
    """Shared "still missing or retryable" check for both concerns: never
    attempted (NULL), or previously failed and --retry-failed was passed."""
    if current_value is None:
        return True
    return retry_failed and current_value == failed_sentinel


def _process_class_metadata(
    conn, connector: PelotonConnector, raw: dict, external_id: str, row: dict,
    retry_failed: bool, max_retries: int, sleep_fn,
) -> str | None:
    """Resolves this row's class-metadata concern (issue #46), independent
    of the performance concern. Returns "not_a_class"/"success"/"failed",
    or None if this concern was already resolved and not eligible for
    retry (so the caller doesn't count it as newly processed)."""
    if not _needs_attempt(row["class_type"], CLASS_TYPE_LOOKUP_FAILED, retry_failed):
        return None

    if not _is_class_workout(raw):
        apply_class_metadata_update(conn, external_id, {"class_type": CLASS_TYPE_NOT_A_CLASS})
        return "not_a_class"

    # retry_with_backoff/fetch_class_details let TransientError (retries
    # exhausted) and AuthenticationError propagate uncaught — a genuinely
    # unrecoverable condition for THIS run, not a per-row "lookup failed"
    # outcome (every subsequent ride_id would fail identically). Letting
    # the script crash here is what makes AC6's resume behavior correct:
    # every row already committed stays committed, and the next
    # invocation's candidate selection naturally skips them.
    ride_id = raw.get(RIDE_ID_FIELD)
    details = retry_with_backoff(
        lambda: connector.fetch_class_details(ride_id), PROVIDER, max_retries=max_retries, sleep_fn=sleep_fn
    )

    if details is None:
        diagnostic_logger().warning(f"{PROVIDER}: class lookup failed for ride_id={ride_id!r}")
        apply_class_metadata_update(conn, external_id, {"class_type": CLASS_TYPE_LOOKUP_FAILED})
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
    return "success"


def _process_performance(
    conn, connector: PelotonConnector, external_id: str, row: dict,
    retry_failed: bool, max_retries: int, sleep_fn,
) -> str | None:
    """Resolves this row's avg/max HR + max power concern (issue #47,
    AC2/AC4), independent of the class-metadata concern above — both read
    from the already-stored raw payload only for their own fields
    (RIDE_ID_FIELD vs. the workout's own external_id), and call their own
    connector method. Returns "success"/"failed", or None if already
    resolved and not eligible for retry."""
    if not _needs_attempt(row["performance_fetch_status"], PERFORMANCE_FETCH_STATUS_FAILED, retry_failed):
        return None

    # fetch_workout_performance() already wraps itself in
    # retry_with_backoff() and degrades ANY non-auth failure to None
    # (AC5) — the script does not wrap it a second time, unlike the class
    # lookup above (which lets TransientError propagate here deliberately;
    # see fetch_workout_performance()'s own docstring for why the two
    # fetches differ). AuthenticationError still propagates uncaught.
    performance = connector.fetch_workout_performance(external_id)

    if performance is None:
        apply_hr_performance_update(
            conn, external_id, {"performance_fetch_status": PERFORMANCE_FETCH_STATUS_FAILED}
        )
        return "failed"

    apply_hr_performance_update(
        conn,
        external_id,
        {
            "avg_hr": performance.get("avg_hr"),
            "max_hr": performance.get("max_hr"),
            "max_power": performance.get("max_power"),
            "performance_fetch_status": PERFORMANCE_FETCH_STATUS_OK,
        },
    )
    return "success"


def _process_row(
    conn, connector: PelotonConnector, row: dict, retry_failed: bool, max_retries: int, sleep_fn
) -> dict[str, str | None]:
    """Processes one candidate row's two independent concerns and commits
    once, regardless of which ran (AC6: resumability). Returns
    {"class": outcome_or_None, "performance": outcome_or_None}."""
    raw = json.loads(row["payload_json"])
    external_id = row["external_id"]

    class_outcome = _process_class_metadata(conn, connector, raw, external_id, row, retry_failed, max_retries, sleep_fn)
    performance_outcome = _process_performance(conn, connector, external_id, row, retry_failed, max_retries, sleep_fn)
    conn.commit()
    return {"class": class_outcome, "performance": performance_outcome}


def run_backfill(conn, connector: PelotonConnector, retry_failed: bool = False, limit: int | None = None,
                  max_retries: int = 3, sleep_fn=None) -> dict[str, int]:
    """Importable entry point (used directly by tests). Returns summary
    counts. sleep_fn defaults to time.sleep when not supplied — tests
    inject a fake to assert on backoff delays without actually sleeping."""
    import time as _time

    sleep_fn = sleep_fn if sleep_fn is not None else _time.sleep
    rows = _select_candidates(conn, retry_failed, limit)

    counts = {
        "processed": 0, "not_a_class": 0, "success": 0, "failed": 0,
        "performance_success": 0, "performance_failed": 0,
    }
    for row in rows:
        outcomes = _process_row(conn, connector, row, retry_failed, max_retries, sleep_fn)
        if outcomes["class"] is None and outcomes["performance"] is None:
            continue  # selected by the OR'd query but nothing eligible here this run
        counts["processed"] += 1
        if outcomes["class"] is not None:
            counts[outcomes["class"]] += 1
        if outcomes["performance"] is not None:
            counts[f"performance_{outcomes['performance']}"] += 1
    return counts


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(
        description="Backfill Peloton class metadata and HR/power data for already-stored workouts (issues #46, #47)."
    )
    parser.add_argument("--db-path", type=Path, default=DEFAULT_DB_PATH)
    parser.add_argument(
        "--retry-failed", action="store_true",
        help="Also re-attempt rows whose previous attempt failed "
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
    print(f"processed:             {counts['processed']}")
    print(f"not_a_class:           {counts['not_a_class']}")
    print(f"class success:         {counts['success']}")
    print(f"class failed:          {counts['failed']}")
    print(f"performance success:   {counts['performance_success']}")
    print(f"performance failed:    {counts['performance_failed']}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
