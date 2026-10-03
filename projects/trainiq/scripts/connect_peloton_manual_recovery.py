#!/usr/bin/env python3
"""
scripts/connect_peloton_manual_recovery.py — one-time setup for Peloton's
manual bearer-token recovery path, bypassing two real gaps the normal app
flow can't currently get through on its own.

Gap 1: trainiq.setup_wizard._setup_email_password_provider() always calls
connector.authenticate() to validate a freshly-entered email/password, and
rolls back (deletes what it just stored) on ANY rejection — indistinguishable
from a genuinely wrong password. Peloton's automated login is confirmed
broken platform-side (403 "Access forbidden. Endpoint no longer accepting
requests", reconfirmed live 2026-09-28 — see
docs/verification/peloton-2026-09-28.md), so the wizard will reject a
CORRECT password every single time. This script stores email/password
directly via CredentialStore, skipping that validation step. Storing them
is still needed even though they'll never be used for a real login while
the connector stays in RecoveryRequired — trainiq.app._build_configured_connectors()
requires both present just to construct PelotonConnector at all.

Gap 2: the real Degraded -> RecoveryRequired escalation is deliberately
slow by design (ADR-038 §4: DEGRADED_ESCALATION_THRESHOLD_DAYS = 10) — a
calibratable timing constant meant to avoid hammering what might be a
transient outage. That reasoning doesn't apply here: the failure has
already been independently, live-verified twice (2026-09-28, both the
automated-login test and the manual-bearer-token test confirming auth
itself works fine, just not the automated login path). This script writes
connector_state directly to RecoveryRequired, using the exact column shape
trainiq.sync.engine._record_lifecycle_outcome() itself writes, with a
`detail` string that says plainly this was set manually and why — never
silently.

Run interactively (prompts for the bearer token via getpass, never accepts
it as a CLI argument, never logs or writes it anywhere but Keychain):

    python3 scripts/connect_peloton_manual_recovery.py

Prerequisite for the token to actually be usable: issue #5 / PR #6 (fixes
download()/normalize() against the real endpoint) merged to main — this
script only prepares credentials and state, it does not run a sync.
"""

from __future__ import annotations

import getpass
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from trainiq.connectors.base import ConnectorState
from trainiq.connectors.peloton import CRED_EMAIL, CRED_PASSWORD, PROVIDER, PelotonConnector
from trainiq.credentials.store import CredentialStore
from trainiq.storage.schema import open_db

APP_SUPPORT_DIR = Path.home() / "Library" / "Application Support" / "TrainIQ"
DEFAULT_DB_PATH = APP_SUPPORT_DIR / "trainiq.db"

RECOVERY_DETAIL = (
    "Manually set to RecoveryRequired by scripts/connect_peloton_manual_recovery.py — "
    "not an organic ADR-038 escalation. Justification: automated login independently "
    "live-verified broken (403) on 2026-09-28, see docs/verification/peloton-2026-09-28.md. "
    "Skipping the normal 10-day Degraded backoff since the failure is already confirmed, "
    "not assumed."
)


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def main() -> int:
    conn = open_db(DEFAULT_DB_PATH)
    credential_store = CredentialStore(conn=conn)

    if credential_store.exists(PROVIDER, CRED_EMAIL) and credential_store.exists(PROVIDER, CRED_PASSWORD):
        print("Peloton email/password already stored — leaving as-is.")
    else:
        print("Peloton email/password not stored yet.")
        answer = input("Store them now? [y/N] ").strip().lower()
        if answer in ("y", "yes"):
            email = input("Email: ").strip()
            password = getpass.getpass("Password: ")
            credential_store.set(PROVIDER, CRED_EMAIL, email)
            credential_store.set(PROVIDER, CRED_PASSWORD, password)
            print("Stored (Keychain, not validated — automated login is known broken).")
        else:
            print("Skipping. Note: without these, trainiq.app won't even construct the "
                  "Peloton connector, so the manual token below won't be reachable yet.")

    row = conn.execute("SELECT state FROM connector_state WHERE provider = ?", (PROVIDER,)).fetchone()
    if row is not None and row["state"] == ConnectorState.RECOVERY_REQUIRED.value:
        print("connector_state already RecoveryRequired — leaving as-is.")
    else:
        now = _iso_now()
        conn.execute(
            """
            INSERT INTO connector_state
                (provider, state, updated_at, detail, state_entered_at, last_attempt_at, attempt_count_in_state, next_eligible_retry_at)
            VALUES (?, ?, ?, ?, ?, NULL, 0, NULL)
            ON CONFLICT(provider) DO UPDATE SET
                state = excluded.state, updated_at = excluded.updated_at, detail = excluded.detail,
                state_entered_at = excluded.state_entered_at, attempt_count_in_state = 0,
                next_eligible_retry_at = NULL
            """,
            (PROVIDER, ConnectorState.RECOVERY_REQUIRED.value, now, RECOVERY_DETAIL, now),
        )
        conn.commit()
        print("connector_state set to RecoveryRequired.")

    print()
    token = getpass.getpass("Paste the manual bearer token (input hidden, never logged): ").strip()
    if not token:
        print("No token entered — nothing submitted. Re-run this script when you have one.")
        conn.close()
        return 1

    connector = PelotonConnector(credential_store)
    connector.submit_manual_recovery(token)
    print("Manual bearer token stored (Keychain).")

    conn.close()
    print()
    print("Next: make sure PR #6 (aaime921/trainiq) is merged — it fixes download()/normalize()")
    print("against the real endpoint. Then run `python3 -m trainiq.app` to actually pull data.")
    print("The token has no auto-refresh (~48h lifetime) — re-run this script to submit a fresh one.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
