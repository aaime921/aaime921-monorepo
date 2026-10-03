#!/usr/bin/env python3
"""
scripts/debug_peloton.py — BL-008 live verification (v2: genuinely non-mutating)

CORRECTION from v1: the previous version called connector.authenticate(),
which — if no valid session existed — would silently fall through to
_login(), performing a REAL Peloton login and rotating credentials
(Keychain write + credentials_metadata write). This version NEVER calls
authenticate() or _login(). It replicates the connector's own
session-validity check (same logic as
PelotonConnector._authenticate_with_automated_login(), lines checking
session_id + expires_at) by reading credentials directly, and only
proceeds if an existing session is already valid. If not, it stops with
a clear message — it does not attempt to establish one.

Database access: opens the SQLite file in TRUE read-only mode via a
`file:...?mode=ro` URI, not the application's normal open_db() (which
calls migrate() unconditionally — a write path this diagnostic has no
business exercising). Any accidental write attempt will raise an error
at the SQLite level, not just "probably not happen." CredentialStore's
own .get() method is confirmed, by direct inspection, to be a pure
keyring read with zero SQLite interaction — only .set()/.rotate() touch
`conn`, and neither is ever called by this script.

Explicitly does NOT:
  - call PelotonConnector.authenticate() or _login() — ever.
  - rotate, set, or write any credential.
  - call SynchronizationEngine.run_once() or touch any sync/persistence table.
  - go through PelotonConnector.download()'s own parsing — the raw
    response is dumped before any extraction, so the wrapper structure
    (data vs. something else) can be inspected directly.
  - implement pagination or retry logic — this is observation only.
  - test the `after` parameter — the first experiment is unfiltered,
    by design, per the agreed investigation sequence.

Usage:
    python3 scripts/debug_peloton.py
"""

from __future__ import annotations

import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from trainiq.connectors.peloton import (
    CRED_SESSION_EXPIRES_AT,
    CRED_SESSION_ID,
    PROVIDER,
    PelotonConnector,
)
from trainiq.credentials.store import CredentialStore

APP_SUPPORT_DIR = Path.home() / "Library" / "Application Support" / "TrainIQ"
DB_PATH = APP_SUPPORT_DIR / "trainiq.db"
OUT_PATH = Path("debug") / "peloton_download_response.json"

_PAGINATION_KEYS_TO_CHECK = ("page", "offset", "limit", "total", "next", "cursor", "has_more", "page_count")
_WORKOUT_FIELDS_TO_CHECK = (
    "id", "start_time", "duration", "fitness_discipline",
    "avg_heart_rate", "max_heart_rate", "avg_power", "max_power", "distance", "calories",
)


def _open_readonly_connection(db_path: Path) -> sqlite3.Connection:
    """True read-only — bypasses open_db()/migrate() entirely, so this
    diagnostic cannot trigger a migration write. Any attempted write
    against this connection raises sqlite3.OperationalError, by SQLite's
    own enforcement, not just by this script's discipline."""
    if not db_path.exists():
        print(f"No database found at {db_path} — the app has never been run. Nothing to verify.")
        sys.exit(1)
    uri = f"file:{db_path}?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def _check_existing_session(credential_store: CredentialStore) -> tuple[bool, dict[str, str]]:
    """Replicates PelotonConnector._authenticate_with_automated_login()'s
    OWN validity check, read-only — never calls that method, never falls
    through to _login(). Returns (is_valid, auth_header_if_valid)."""
    session_id = credential_store.get(PROVIDER, CRED_SESSION_ID)
    expires_at_raw = credential_store.get(PROVIDER, CRED_SESSION_EXPIRES_AT)
    expires_at = int(expires_at_raw) if expires_at_raw else 0
    now = int(datetime.now(timezone.utc).timestamp())

    print("=" * 40)
    print("AUTHENTICATION")
    print("=" * 40)
    print(f"Existing session_id found: {session_id is not None}")
    print(f"Existing session_expires_at found: {expires_at_raw is not None}")
    if expires_at_raw:
        print(f"Session expires_at: {expires_at} (now: {now}, valid for {expires_at - now}s more)")

    is_valid = bool(session_id) and expires_at > now + 60
    print(f"Considered usable: {is_valid}")
    print("Login attempted: NO (this script never calls authenticate() or _login())")
    print("Credential rotation: NONE — confirmed, since only .set()/.rotate() write, and neither is called")
    print()

    if not is_valid:
        return False, {}
    return True, {"Cookie": f"peloton_session_id={session_id}"}


def main() -> int:
    ro_conn = _open_readonly_connection(DB_PATH)
    try:
        credential_store = CredentialStore(conn=ro_conn)
        is_valid, auth_header = _check_existing_session(credential_store)
    finally:
        ro_conn.close()

    if not is_valid:
        print(
            "No valid, non-expired Peloton session is currently available.\n"
            "This diagnostic will NOT attempt to log in on your behalf.\n"
            "Establish a session first through the normal application flow "
            "(python3 -m trainiq.app, or the setup wizard), then re-run this script."
        )
        return 1

    connector = PelotonConnector(credential_store)  # construction only — no I/O, no auth call
    url = f"{connector._base_url}/api/me/workouts"
    request_headers = {**auth_header, "peloton-platform": "web"}

    print("=" * 40)
    print("HTTP REQUEST")
    print("=" * 40)
    print(f"Method: GET")
    print(f"URL: {url}")
    print(f"Headers: {{'Cookie': '<redacted>', 'peloton-platform': 'web'}}")
    print("Query parameters: {} (unfiltered — first experiment, no --since by design)\n")

    response = connector._session.get(url, headers=request_headers, params={})

    print("=" * 40)
    print("HTTP RESPONSE")
    print("=" * 40)
    print(f"HTTP status: {response.status_code}")
    content_type = response.headers.get("Content-Type") if hasattr(response, "headers") else None
    print(f"Content-Type: {content_type}")
    retry_after = response.headers.get("Retry-After") if hasattr(response, "headers") else None
    rate_limit_headers = {
        k: v for k, v in (getattr(response, "headers", {}) or {}).items()
        if "rate" in k.lower() or "retry" in k.lower()
    }
    print(f"Retry-After header: {retry_after}")
    print(f"Other rate-limit-related headers: {rate_limit_headers if rate_limit_headers else '(none observed)'}")
    response_size_bytes = len(response.content) if hasattr(response, "content") else 0
    print(f"Response body size (bytes): {response_size_bytes}\n")

    if response.status_code == 429:
        print("*** RATE LIMITED. Stopping — no retry, per instruction. ***")
        return 1
    if response.status_code != 200:
        print(f"*** Non-200 status ({response.status_code}). Stopping — no retry, no assumption about cause. ***")
        try:
            print(f"Response body: {response.text[:2000]}")
        except Exception:
            pass
        return 1

    raw_body = response.json()

    print("=" * 40)
    print("RESPONSE STRUCTURE")
    print("=" * 40)
    print(f"Top-level JSON type: {type(raw_body).__name__}")
    if isinstance(raw_body, dict):
        print(f"Top-level keys: {list(raw_body.keys())}")
        data = raw_body.get("data")
        print(f"'data' present: {data is not None}")
        if data is not None:
            print(f"Type of raw_body['data']: {type(data).__name__}")
        records = data if isinstance(data, list) else []
    else:
        print("Top-level value is NOT a dict — 'data' assumption cannot even apply. Full structure needed.")
        records = raw_body if isinstance(raw_body, list) else []
    print(f"Number of returned workout records: {len(records)}\n")

    print("=" * 40)
    print("PAGINATION DISCOVERY (observation only — no implementation)")
    print("=" * 40)
    found_pagination_keys = {}
    if isinstance(raw_body, dict):
        for key in _PAGINATION_KEYS_TO_CHECK:
            if key in raw_body:
                found_pagination_keys[key] = raw_body[key]
    if found_pagination_keys:
        print(f"Pagination-related keys found at top level: {found_pagination_keys}")
    else:
        print("No pagination metadata observed in this response.")
        print("(This does NOT by itself mean the endpoint is non-paginated — absence of evidence, not evidence of absence.)")
    print()

    if records:
        first = records[0]
        print("=" * 40)
        print("FIRST WORKOUT — FIELD-BY-FIELD INSPECTION")
        print("=" * 40)
        for field in _WORKOUT_FIELDS_TO_CHECK:
            present = field in first
            value = first.get(field)
            print(f"  {field}: present={present}, type={type(value).__name__ if present else 'N/A'}, value={value!r}")
        print()

        start_time = first.get("start_time")
        print("=" * 40)
        print("CRITICAL start_time CHECK")
        print("=" * 40)
        print(f"start_time Python type: {type(start_time).__name__}")
        if not isinstance(start_time, str):
            print(
                "\nWARNING: Peloton start_time is not a string; current resume-cursor "
                "implementation may be unsafe.\n"
            )
        print()

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(json.dumps(raw_body, indent=2))
    print(f"Full raw response written to {OUT_PATH}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
