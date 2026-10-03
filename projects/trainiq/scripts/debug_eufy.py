#!/usr/bin/env python3
"""
scripts/debug_eufy.py — RC1-HF-003 Phase 2/3: capture the real Eufy
device-data payload, and (this revision) run controlled experiments on
whether the Cloud API honors incremental filtering via `start_time`.

THIS IS A ONE-OFF DIAGNOSTIC EXPERIMENT, NOT A PRODUCTION FEATURE. The
--since flag exists solely to answer one open question (BL-006): does
`/device/{id}/data` actually filter by `start_time`, or does it silently
ignore an unrecognized/unsupported parameter and return full history
regardless? EufyConnector.download() is NOT modified by this change and
remains the only production code path — this script deliberately
duplicates its exact `if since: params["start_time"] = since` logic
rather than importing or calling it, so this experiment is self-contained
and never touches production parsing.

Does exactly three things: authenticate with already-stored credentials,
call the device-data endpoint once (optionally with --since), and write
the COMPLETE, UNMODIFIED JSON response to disk. Nothing else.

Explicitly does NOT:
  - call SynchronizationEngine.run_once() or touch raw_activities,
    normalized_activities, weigh_ins, or any checkpoint — this is a
    read-only diagnostic, not a sync.
  - go through EufyConnector.download()'s own parsing, which already
    extracts body.get("data", []) — that would lose exactly the wrapper
    structure (res_code, page_size, offset, etc.) this script exists to
    capture. The raw HTTP response is dumped before any extraction.
  - modify normalize() or any parsing logic — that's explicitly Phase 4,
    only after this script's output has been inspected by hand.

Prerequisite: Eufy must already be connected (run the app once — via the
first-time setup wizard — so credentials and device_id already exist in
Keychain/config.json). This script does not itself prompt for anything.

Usage:
    python3 scripts/debug_eufy.py
    python3 scripts/debug_eufy.py --since 1781160591
    python3 scripts/debug_eufy.py --since 9999999999
    python3 scripts/debug_eufy.py --out debug/eufy_download_response.json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from trainiq.config import get_eufy_device_id
from trainiq.connectors.eufy import EufyConnector
from trainiq.credentials.store import CredentialStore
from trainiq.storage.schema import open_db

APP_SUPPORT_DIR = Path.home() / "Library" / "Application Support" / "TrainIQ"
DEFAULT_OUT = Path("debug") / "eufy_download_response.json"


def _redact_token(token: Optional[str]) -> str:
    if not token:
        return "(none)"
    return f"{token[:6]}..." if len(token) > 6 else f"{token}..."


def _extract_timestamps(records: list[dict[str, Any]]) -> tuple[Any, Any]:
    """Returns (oldest, newest) create_time values found across records,
    or (None, None) if the list is empty or the field is absent."""
    timestamps = [r.get("create_time") for r in records if isinstance(r, dict) and r.get("create_time") is not None]
    if not timestamps:
        return None, None
    return min(timestamps), max(timestamps)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT, help="Where to write the raw JSON response")
    parser.add_argument("--db-path", type=Path, default=APP_SUPPORT_DIR / "trainiq.db")
    parser.add_argument("--config-path", type=Path, default=APP_SUPPORT_DIR / "config.json")
    parser.add_argument(
        "--since", type=str, default=None,
        help="Optional. If omitted, behavior is identical to before this revision "
             "(no start_time parameter sent — full history requested). If supplied, "
             "sent as params['start_time'], using the EXACT same conditional logic "
             "as EufyConnector.download() itself: 'if since: params[\"start_time\"] = since'.",
    )
    args = parser.parse_args()

    conn = open_db(args.db_path)
    try:
        credential_store = CredentialStore(conn=conn)
        device_id = get_eufy_device_id(args.config_path)
        if not device_id:
            print("No Eufy device_id found in config.json — run the setup wizard first (python3 -m trainiq.app).")
            return 1

        connector = EufyConnector(credential_store, device_id=device_id)
        print("Authenticating with Eufy...")
        if not connector.authenticate():
            print("Authentication failed — check stored credentials (Keychain: eufy/email, eufy/password).")
            return 1
        print(f"Authenticated. Fetching device data for device_id={device_id}...\n")

        # Exactly EufyConnector.download()'s own conditional — duplicated,
        # not imported, so this experiment never touches production code.
        params: dict[str, Any] = {}
        if args.since:
            params["start_time"] = args.since

        url = f"{connector._base_url}/device/{device_id}/data"
        headers = {"token": connector._access_token}

        print("=" * 40)
        print("REQUEST")
        print("=" * 40)
        print(f"URL:\n{url}\n")
        print(f"Headers:\ntoken={_redact_token(connector._access_token)}\n")
        print(f"Query parameters:\n{json.dumps(params, indent=2)}\n")

        response = connector._session.get(url, headers=headers, params=params)
        raw_body = response.json()
        data = raw_body.get("data", []) if isinstance(raw_body, dict) else []
        oldest, newest = _extract_timestamps(data)
        response_size_bytes = len(response.content) if hasattr(response, "content") else len(json.dumps(raw_body))

        print("=" * 40)
        print("RESPONSE")
        print("=" * 40)
        print(f"HTTP status: {response.status_code}")
        print(f"Response size (bytes): {response_size_bytes}")
        print(f"Number of records: {len(data)}")
        print(f"Oldest timestamp: {oldest}")
        print(f"Newest timestamp: {newest}\n")

        if data:
            print("First record (pretty):")
            print(json.dumps(data[0], indent=2))
            print("\nLast record (pretty):")
            print(json.dumps(data[-1], indent=2))
            print()
    finally:
        conn.close()

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(raw_body, indent=2))
    print(f"Full response written to {args.out}\n")

    print("=" * 40)
    print("SUMMARY")
    print("=" * 40)
    print(f"since parameter used: {args.since!r}")
    print(f"records returned: {len(data)}")
    print(f"oldest timestamp: {oldest}")
    print(f"newest timestamp: {newest}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
