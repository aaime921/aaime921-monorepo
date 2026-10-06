#!/usr/bin/env python3
"""
scripts/confirm_weigh_in.py — BO-facing un-flag mechanism for ADR-039 /
Issue #38's weigh-in plausibility rule (requirements AC6).

A flagged reading is never deleted or silently un-flagged — this script
records that the BO reviewed a specific flagged reading and confirmed it
was actually valid (a false positive), without touching the row's raw
values or its original `is_flagged_implausible`/`plausibility_reason`
verdict. That keeps the override auditable: a confirmed row reads as
"flagged, but overridden," not as if it had never been flagged at all.

Usage:
    python3 scripts/confirm_weigh_in.py --provider eufy --external-id <id> \\
        [--db-path PATH] [--note "BO confirmed: real reading"]

No un-confirm path exists in this script — reversing a confirmation isn't
in the acceptance criteria for issue #38; if needed later it's a one-line
follow-up (`bo_confirmed_valid = 0`), not designed here to avoid scope
creep.
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from trainiq.storage.schema import open_db

APP_SUPPORT_DIR = Path.home() / "Library" / "Application Support" / "TrainIQ"
DEFAULT_DB_PATH = APP_SUPPORT_DIR / "trainiq.db"


class NoSuchWeighIn(Exception):
    """No row matches (provider, external_id)."""


class WeighInNotFlagged(Exception):
    """The row exists but isn't currently flagged — nothing to confirm."""


def confirm_weigh_in(conn: sqlite3.Connection, provider: str, external_id: str) -> str:
    """Looks up the row by its existing (provider, external_id) unique
    key. Raises NoSuchWeighIn / WeighInNotFlagged rather than writing
    anything — confirming a reading that was never flagged would be
    meaningless, and confirming a nonexistent row is just a typo.
    Returns the row's `plausibility_reason`, for the caller to print back
    to the BO as confirmation of what's being overridden."""
    row = conn.execute(
        "SELECT is_flagged_implausible, plausibility_reason FROM weigh_ins "
        "WHERE provider = ? AND external_id = ?",
        (provider, external_id),
    ).fetchone()
    if row is None:
        raise NoSuchWeighIn(f"No weigh-in found for provider={provider!r}, external_id={external_id!r}")
    if not row[0]:
        raise WeighInNotFlagged(
            f"weigh-in provider={provider!r}, external_id={external_id!r} is not currently flagged — nothing to confirm"
        )

    reason = row[1]
    confirmed_at = datetime.now(timezone.utc).isoformat()
    conn.execute(
        "UPDATE weigh_ins SET bo_confirmed_valid = 1, bo_confirmed_at = ? "
        "WHERE provider = ? AND external_id = ?",
        (confirmed_at, provider, external_id),
    )
    conn.commit()
    return reason


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Confirm a flagged weigh-in as actually valid (ADR-039 / Issue #38, AC6)."
    )
    parser.add_argument("--provider", required=True, help="e.g. eufy")
    parser.add_argument("--external-id", required=True, dest="external_id")
    parser.add_argument("--db-path", type=Path, default=DEFAULT_DB_PATH)
    parser.add_argument("--note", default=None, help="Optional note, printed only — not persisted")
    args = parser.parse_args()

    conn = open_db(args.db_path)
    try:
        reason = confirm_weigh_in(conn, args.provider, args.external_id)
    except (NoSuchWeighIn, WeighInNotFlagged) as exc:
        print(f"Not confirmed: {exc}")
        return 1
    finally:
        conn.close()

    print(f"Overriding flagged verdict: {reason}")
    if args.note:
        print(f"Note: {args.note}")
    print(f"Confirmed provider={args.provider!r}, external_id={args.external_id!r} as valid.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
