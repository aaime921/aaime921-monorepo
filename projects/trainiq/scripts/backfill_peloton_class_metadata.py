#!/usr/bin/env python3
"""
scripts/backfill_peloton_class_metadata.py — one-off, resumable, rate-limited
backfill of Peloton class metadata for already-stored workouts (issue #46).

Deliberately NOT built on trainiq.normalization.renormalize.renormalize_provider()
(issue #36/#45's pattern): that function's entire contract is "re-derive a
canonical record from an already-stored raw payload with zero new network
I/O." Class metadata requires a genuinely new network call per class
(trainiq.connectors.peloton.PelotonConnector.fetch_class_details()), which
renormalize_provider() has no hook for and should not grow one for — it
would stop being a pure offline recomputation for every other caller too.

Instead, this script walks existing `normalized_activities` rows for
provider="peloton" with `class_type IS NULL` (i.e. never attempted by this
feature's download()), ordered by `id` for a stable walk, and writes each
result through `apply_class_metadata_update()` — a narrow UPDATE-only path
that touches only the 5 Peloton-enrichment columns (see that function's
docstring in trainiq/connectors/peloton.py).

Resumability (AC6): commits after every row, not in a batch at the end. An
interrupted run loses at most the one in-flight lookup; everything already
written stays written, and the next run's `class_type IS NULL` selection
naturally excludes already-processed rows (both successes and non-class
sentinels — only genuine lookup failures, `class_type = 'lookup_failed'`,
are eligible for re-attempt, and only when `--retry-failed` is passed).

Rate limiting: each class-detail fetch goes through
trainiq.sync.engine.retry_with_backoff() (ADR-037), reusing the exact same
policy a live sync already uses — honors a provider-directed Retry-After
when the connector raises TransientError with one, exponential backoff
otherwise.

Per this project's live-verification constraint, this script is only unit-
tested against a fake connector/fixtures in CI (tests/
test_backfill_peloton_class_metadata.py) — actually running it against the
BO's 136 real Peloton workouts is an ops task, explicitly out of scope for
this pipeline (requirements doc, "Out of scope").

Run against your real database once this ships:

    python3 scripts/backfill_peloton_class_metadata.py
    python3 scripts/backfill_peloton_class_metadata.py --retry-failed
    python3 scripts/backfill_peloton_class_metadata.py --limit 50
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
    PLANNED_DURATION_FIELD,
    PROVIDER,
    RIDE_ID_FIELD,
    PelotonConnector,
    _extract_instructor_name,
    _is_class_workout,
    apply_class_metadata_update,
)
from trainiq.credentials.store import CredentialStore
from trainiq.logging_setup import diagnostic_logger
from trainiq.storage.schema import open_db
from trainiq.sync.engine import retry_with_backoff

APP_SUPPORT_DIR = Path.home() / "Library" / "Application Support" / "TrainIQ"
DEFAULT_DB_PATH = APP_SUPPORT_DIR / "trainiq.db"


def _select_candidates(conn, retry_failed: bool, limit: int | None) -> list[dict]:
    """Returns [{external_id, payload_json}], ordered by id, for a stable
    resumable walk. class_type IS NULL means "never attempted" (the
    not-yet-migrated or not-yet-synced-since-this-feature case);
    --retry-failed additionally includes rows already attempted but that
    failed (class_type = 'lookup_failed')."""
    query = (
        "SELECT ra.id AS id, na.external_id AS external_id, ra.payload_json AS payload_json "
        "FROM raw_activities ra "
        "JOIN normalized_activities na "
        "  ON na.provider = ra.provider AND na.external_id = ra.external_id "
        "WHERE ra.provider = ? AND (na.class_type IS NULL"
    )
    if retry_failed:
        query += " OR na.class_type = ?)"
        params: tuple = (PROVIDER, CLASS_TYPE_LOOKUP_FAILED)
    else:
        query += ")"
        params = (PROVIDER,)
    query += " ORDER BY ra.id"
    if limit is not None:
        query += " LIMIT ?"
        params = params + (limit,)
    return [dict(row) for row in conn.execute(query, params).fetchall()]


def _process_row(conn, connector: PelotonConnector, row: dict, max_retries: int, sleep_fn) -> str:
    """Processes one candidate row and commits immediately (AC6:
    resumability). Returns one of "not_a_class", "success", "failed" for
    the caller's summary counts."""
    raw = json.loads(row["payload_json"])
    external_id = row["external_id"]

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


def run_backfill(conn, connector: PelotonConnector, retry_failed: bool = False, limit: int | None = None,
                  max_retries: int = 3, sleep_fn=None) -> dict[str, int]:
    """Importable entry point (used directly by tests). Returns summary
    counts. sleep_fn defaults to time.sleep when not supplied — tests
    inject a fake to assert on backoff delays without actually sleeping."""
    import time as _time

    sleep_fn = sleep_fn if sleep_fn is not None else _time.sleep
    rows = _select_candidates(conn, retry_failed, limit)

    counts = {"processed": 0, "not_a_class": 0, "success": 0, "failed": 0}
    for row in rows:
        outcome = _process_row(conn, connector, row, max_retries, sleep_fn)
        counts["processed"] += 1
        counts[outcome] += 1
    return counts


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(
        description="Backfill Peloton class metadata for already-stored workouts (issue #46)."
    )
    parser.add_argument("--db-path", type=Path, default=DEFAULT_DB_PATH)
    parser.add_argument(
        "--retry-failed", action="store_true",
        help="Also re-attempt rows whose previous lookup failed (class_type == 'lookup_failed').",
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
    print(f"processed:    {counts['processed']}")
    print(f"not_a_class:  {counts['not_a_class']}")
    print(f"success:      {counts['success']}")
    print(f"failed:       {counts['failed']}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
