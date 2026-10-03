#!/usr/bin/env python3
"""
scripts/import_peloton_csv.py — CLI entry point for the Peloton manual CSV
export importer (trainiq.csv_import.peloton_csv.import_peloton_csv()).

This existed as tested, working logic with no way to actually run it — no
CLI ever called it outside the test suite. This script is that missing
entry point, nothing more: it opens the real database via the normal
open_db() (schema migration, not a diagnostic bypass — this command is
meant to write), calls import_peloton_csv() once, and prints the result.

Peloton's own live API automated login is confirmed broken as of 2026-09-28
(see BACKLOG.md BL-008's "Live reconfirmation" entry) — this CSV path is
the current, viable way to get real Peloton history into TrainIQ. Data
lands under provider "peloton_csv", distinct from the live-API provider
string "peloton", by design (see peloton_csv.py's own module docstring) —
the two are never silently merged.

Get a CSV export from Peloton's own site (Profile -> Account -> "Export
Data" / workout history download), then run:

    python3 scripts/import_peloton_csv.py path/to/your_export.csv

Idempotent: re-running against the same file updates existing rows, never
duplicates them (UNIQUE(provider, external_id), per the module's own
INSERT OR IGNORE + conditional UPDATE pattern).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from trainiq.csv_import.peloton_csv import import_peloton_csv
from trainiq.storage.schema import open_db

APP_SUPPORT_DIR = Path.home() / "Library" / "Application Support" / "TrainIQ"
DEFAULT_DB_PATH = APP_SUPPORT_DIR / "trainiq.db"


def main() -> int:
    parser = argparse.ArgumentParser(description="Import a Peloton manual CSV export into TrainIQ.")
    parser.add_argument("csv_path", type=str, help="Path to the Peloton workout-history CSV export")
    parser.add_argument("--db-path", type=Path, default=DEFAULT_DB_PATH)
    args = parser.parse_args()

    if not Path(args.csv_path).exists():
        print(f"CSV not found: {args.csv_path}")
        return 1

    print(f"Database: {args.db_path}")
    print(f"CSV:      {args.csv_path}")
    print()

    conn = open_db(args.db_path)
    try:
        result = import_peloton_csv(args.csv_path, conn)
    finally:
        conn.close()

    print("=" * 40)
    print("IMPORT RESULT")
    print("=" * 40)
    print(f"Records seen:                    {result.records_seen}")
    print(f"Records inserted:                {result.records_inserted}")
    print(f"Records updated:                 {result.records_updated}")
    print(f"Skipped (no identity):           {result.records_skipped_no_identity}")
    print(f"Skipped (missing required field):{result.records_skipped_missing_required_field}")
    print(f"Unique external IDs:             {result.unique_external_ids}")
    if result.collisions:
        print(f"Collisions detected:             {len(result.collisions)}")
        for a, b in result.collisions[:10]:
            print(f"  {a!r} vs {b!r}")
    else:
        print("Collisions detected:             0")

    return 0


if __name__ == "__main__":
    sys.exit(main())
