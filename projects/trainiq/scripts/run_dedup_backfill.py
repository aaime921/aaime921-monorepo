#!/usr/bin/env python3
"""
scripts/run_dedup_backfill.py — CLI entry point for the cross-provider
activity dedup detector (trainiq.dedup.detector.run_backfill()).

Scans every normalized_activities row for provider IN ('peloton', 'strava',
'strava_unofficial'), finds cross-provider duplicate candidates by start-
time proximity plus a secondary signal (duration and/or discipline), scores
each one, and records a merge/flag decision in dedup_links. Never deletes
or modifies raw_activities/normalized_activities rows.

Idempotent: re-running against the same database only adds rows for pairs
not already recorded in dedup_links (no duplicates). Safe to run repeatedly
as a one-off backfill over existing data, or as a manual/cron step after
future syncs - this script is not wired into app.py/main(), by design (see
docs/trainiq/architecture/37-cross-provider-activity-dedup.md).

    python3 scripts/run_dedup_backfill.py [--db-path path/to/trainiq.db]
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from trainiq.dedup.detector import run_backfill
from trainiq.storage.schema import open_db

APP_SUPPORT_DIR = Path.home() / "Library" / "Application Support" / "TrainIQ"
DEFAULT_DB_PATH = APP_SUPPORT_DIR / "trainiq.db"


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Backfill cross-provider activity dedup links (Peloton/Strava/strava_unofficial)."
    )
    parser.add_argument("--db-path", type=Path, default=DEFAULT_DB_PATH)
    args = parser.parse_args()

    print(f"Database: {args.db_path}")
    print()

    conn = open_db(args.db_path)
    try:
        result = run_backfill(conn)
    finally:
        conn.close()

    print("=" * 40)
    print("DEDUP BACKFILL RESULT")
    print("=" * 40)
    print(f"Activities scanned:   {result.scanned}")
    print(f"Candidate pairs:      {result.candidate_pairs}")
    print(f"Auto-linked:          {result.linked}")
    print(f"Flagged for review:   {result.flagged}")
    print(f"Already recorded:     {result.skipped_existing}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
