#!/usr/bin/env python3
"""
scripts/renormalize_strava_unofficial.py — one-off CLI entry point for
trainiq.normalization.renormalize.renormalize_provider(), scoped to
provider "strava_unofficial" (issue #36).

Issue #36: all 352 activities imported so far by StravaUnofficialConnector
were stored with discipline="other" because `strava_unofficial` had no
entry in taxonomy.py's _PROVIDER_MAPS. That taxonomy gap is now fixed, but
the rows already persisted in `normalized_activities` still carry the old,
wrong discipline — a live sync will NOT self-heal them (download() only
fetches activities after the stored checkpoint). This script re-derives
every stored `strava_unofficial` row's canonical fields from its already-
stored raw_activities.payload_json, without re-fetching from Strava and
without touching raw_activities at all.

Safe to run more than once (idempotent — re-running after the first
correct run reports the same row count with everything "updated", no
further discipline changes).

StravaUnofficialConnector.normalize() makes no network call and touches no
credentials, so constructing the connector for this purpose is safe even
with an expired/absent session cookie — nothing here attempts to
authenticate or talk to Strava.

Run against your real database once this ships:

    python3 scripts/renormalize_strava_unofficial.py

Note on training_load: re-running build_canonical_record() also recomputes
training_load/training_load_method/source_confidence, not just discipline.
Today this is a no-op for training_load (resolves to None/"unknown"
regardless of discipline, since no AthleteProfile persistence exists yet)
— but if that changes before you run this script, re-normalizing will also
assign real training_load values to these rows for the first time.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from trainiq.config import get_athlete_timezone
from trainiq.connectors.strava_unofficial import PROVIDER, StravaUnofficialConnector
from trainiq.credentials.store import CredentialStore
from trainiq.normalization.renormalize import renormalize_provider
from trainiq.storage.schema import open_db
from trainiq.sync.engine import recompute_checkpoint_from_normalized

APP_SUPPORT_DIR = Path.home() / "Library" / "Application Support" / "TrainIQ"
DEFAULT_DB_PATH = APP_SUPPORT_DIR / "trainiq.db"
DEFAULT_CONFIG_PATH = APP_SUPPORT_DIR / "config.json"


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(
        description="Re-normalize existing strava_unofficial raw_activities rows (issue #36)."
    )
    parser.add_argument("--db-path", type=Path, default=DEFAULT_DB_PATH)
    parser.add_argument("--config-path", type=Path, default=DEFAULT_CONFIG_PATH)
    args = parser.parse_args()

    print(f"Database: {args.db_path}")
    print()

    conn = open_db(args.db_path)
    try:
        credential_store = CredentialStore(conn=conn)
        local_timezone = get_athlete_timezone(args.config_path)
        connector = StravaUnofficialConnector(credential_store, local_timezone=local_timezone)
        result = renormalize_provider(conn, PROVIDER, connector)

        strategy_obj = connector.active_strategy()
        strategy = strategy_obj.value if strategy_obj is not None else "default"
        new_cursor = recompute_checkpoint_from_normalized(conn, PROVIDER, strategy=strategy)

        conn.commit()
    finally:
        conn.close()

    print("=" * 40)
    print("RE-NORMALIZATION RESULT")
    print("=" * 40)
    print(f"read:                    {result.read}")
    print(f"inserted:                {result.inserted}")
    print(f"updated:                 {result.updated}")
    print(f"skipped_malformed:       {result.skipped_malformed}")
    print(f"skipped_no_external_id:  {result.skipped_no_external_id}")
    print(f"recomputed last_cursor:  {new_cursor}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
