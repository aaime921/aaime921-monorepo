#!/usr/bin/env python3
"""
scripts/backfill_strava_streams.py — one-off, resumable, rate-limited
backfill of Strava per-activity streams (HR, moving time, pace, GPS track)
for already-stored `strava_unofficial` activities (issue #50).

Runs dedup's run_backfill() first (idempotent) so Peloton-linked activities
are correctly excluded from trainiq.connectors.strava_streams.
enrich_strava_streams()'s eligible-row query — otherwise a Peloton-linked
ride synced from Strava too would get fetched needlessly (harmless, one
wasted request; see architecture doc, "Risks").

Resumable by re-running: enrich_strava_streams() commits after every row and
only selects rows with streams_fetch_status IS NULL, so an interrupted run
picks up exactly where it left off. On 401/403/redirect (AuthenticationError)
or 429/5xx (TransientError) it stops cleanly, prints what ran, and exits
non-zero — the cookie is cleared automatically on an auth failure (same
behavior every other call through this connector already has), so the next
regular sync's authenticate() will surface the need for a fresh cookie.

Per this project's live-verification constraint, only unit-tested against
a fake connector/fixtures in CI — running it against the BO's ~190 real
Strava-only activities is an ops task (requirements doc, "Out of scope").

Run against your real database once this ships:

    python3 scripts/backfill_strava_streams.py
    python3 scripts/backfill_strava_streams.py --limit 20
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from trainiq.connectors.strava_streams import enrich_strava_streams
from trainiq.connectors.strava_unofficial import StravaUnofficialConnector
from trainiq.credentials.store import CredentialStore
from trainiq.dedup.detector import run_backfill
from trainiq.storage.schema import open_db
from trainiq.sync.engine import AuthenticationError, TransientError

APP_SUPPORT_DIR = Path.home() / "Library" / "Application Support" / "TrainIQ"
DEFAULT_DB_PATH = APP_SUPPORT_DIR / "trainiq.db"


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(
        description="Backfill Strava-only activity streams (HR, pace, GPS track) for already-stored activities (issue #50)."
    )
    parser.add_argument("--db-path", type=Path, default=DEFAULT_DB_PATH)
    parser.add_argument(
        "--limit", type=int, default=None,
        help="Cap how many activities this invocation attempts, to budget calls across multiple manual runs.",
    )
    args = parser.parse_args()

    print(f"Database: {args.db_path}")
    print()

    conn = open_db(args.db_path)
    try:
        run_backfill(conn)

        credential_store = CredentialStore(conn=conn)
        connector = StravaUnofficialConnector(credential_store)
        if not connector.authenticate():
            print("Could not authenticate with Strava (unofficial) — run setup/manual recovery first.")
            return 1

        try:
            result = enrich_strava_streams(conn, connector, limit=args.limit)
        except (AuthenticationError, TransientError) as exc:
            print(f"Stopped: {exc}")
            return 1
    finally:
        conn.close()

    print("=" * 40)
    print("STREAMS BACKFILL RESULT")
    print("=" * 40)
    print(f"processed:    {result.processed}")
    print(f"ok:           {result.ok}")
    print(f"no_streams:   {result.no_streams}")
    print(f"unavailable:  {result.unavailable}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
