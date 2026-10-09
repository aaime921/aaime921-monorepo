#!/usr/bin/env python3
"""
scripts/renormalize_peloton_distance.py — one-off CLI entry point for
trainiq.normalization.renormalize.renormalize_provider(), scoped to
provider "peloton" (issue #45).

Issue #45: every Peloton row synced so far was normalized by
PelotonConnector.normalize() always treating raw `distance` as kilometers
(the now-removed DISTANCE_KM_TO_M_MULTIPLIER), but the live API actually
reports `distance` in whatever unit the account's own display settings
use. For an account whose unit is actually miles, every stored
`distance_m` is ~38% short. A live sync will NOT self-heal these rows
(download() only fetches activities after the stored checkpoint, and
existing raw_activities rows predate the `_distance_unit` key the fixed
download() now attaches to every workout).

This script re-derives every stored `peloton` row's `distance_m` (and
other normalize() output) from its already-stored raw_activities.payload_json,
without re-fetching from Peloton and without touching raw_activities at
all — it injects the operator-confirmed `--unit` into an in-memory copy of
each row's raw dict via renormalize_provider()'s `raw_transform` parameter,
the same `_distance_unit` key normalize() already knows how to read for
live syncs.

`--unit` is required and has no default: the project's never-guess
standard applies to this script's own ergonomics, not just the connector's
code. The confirmed unit must come from independent evidence (for this
BO's account: the Peloton<->Strava pairwise comparison in issue #45's own
evidence, which showed "mi"), not a default this script could get wrong
silently.

Safe to run more than once (idempotent — re-running after the first
correct run reports the same row count with everything "updated", no
further distance_m changes, same guarantee renormalize_provider() already
gives).

PelotonConnector.normalize() makes no network call and touches no
credentials (confirmed by reading the method: it only reads from its `raw`
argument and calls diagnostic_logger()), so constructing the connector for
this script is safe with no live session, same justification
renormalize_strava_unofficial.py already gives for its own connector.

Run against your real database once this ships (the account's confirmed
unit from issue #45's evidence is "mi" for this BO):

    python3 scripts/renormalize_peloton_distance.py --unit mi

After running, issue #45's AC5 (linked Peloton<->Strava pairs from issue
#37 agree on distance within 1%) is the verification check — run against
the real database, by the BO, not by this script.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from trainiq.connectors.peloton import PROVIDER, PelotonConnector
from trainiq.credentials.store import CredentialStore
from trainiq.normalization.renormalize import renormalize_provider
from trainiq.storage.schema import open_db

APP_SUPPORT_DIR = Path.home() / "Library" / "Application Support" / "TrainIQ"
DEFAULT_DB_PATH = APP_SUPPORT_DIR / "trainiq.db"


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(
        description="Re-normalize existing peloton raw_activities rows' distance_m (issue #45)."
    )
    parser.add_argument("--db-path", type=Path, default=DEFAULT_DB_PATH)
    parser.add_argument(
        "--unit",
        required=True,
        choices=["mi", "km"],
        help="Confirmed Peloton account distance unit for the rows being corrected "
             "(e.g. 'mi' for this BO's account, per issue #45's evidence). Required — "
             "this script never guesses a unit.",
    )
    args = parser.parse_args()

    print(f"Database: {args.db_path}")
    print(f"Unit:     {args.unit}")
    print()

    conn = open_db(args.db_path)
    try:
        credential_store = CredentialStore(conn=conn)
        connector = PelotonConnector(credential_store)
        result = renormalize_provider(
            conn, PROVIDER, connector,
            raw_transform=lambda raw: {**raw, "_distance_unit": args.unit},
        )
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

    return 0


if __name__ == "__main__":
    sys.exit(main())
