#!/usr/bin/env python3
"""
scripts/peloton_smart_sync.py — single command for keeping Peloton data
current with as little manual involvement as the platform allows.

What this automates: checking whether the stored manual bearer token
(`PelotonConnector`'s recovery-path credential) is still valid by decoding
its own `exp` claim — never a guessed/fixed lifetime. If it's still good,
this runs the real sync (`python3 -m trainiq.app`) immediately, no prompts,
no browser. If it's expired or missing, it opens your browser to Peloton's
normal login page, prints exactly where to find a fresh bearer token in
DevTools, waits for you to paste it, stores it, then runs the sync — one
command either way.

What this deliberately does NOT automate: the login itself. Two live tests
against this account ruled that out for good reasons, not laziness — see
BACKLOG.md BL-008, "PKCE OAuth flow investigated and abandoned
(2026-09-29)": Peloton's automated `/auth/login` is confirmed blocked
(403), and a PKCE/OAuth alternative was live-tested and abandoned after
(a) the site's own frontend consumed the authorization code before a
script could react, and (b) a further attempt triggered what looked like
bot-detection (an indefinite hang on the login page). Scripting the login
form itself carries the same risk, just via a different mechanism. This
script never touches your password and never drives a login form — you do
that part yourself, in a real browser, same as opening Peloton's site
normally.

Run:
    python3 scripts/peloton_smart_sync.py
"""

from __future__ import annotations

import base64
import getpass
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from trainiq.connectors.peloton import CRED_MANUAL_BEARER_TOKEN, PROVIDER
from trainiq.credentials.store import CredentialStore
from trainiq.storage.schema import open_db

APP_SUPPORT_DIR = Path.home() / "Library" / "Application Support" / "TrainIQ"
DB_PATH = APP_SUPPORT_DIR / "trainiq.db"
LOGIN_URL = "https://members.onepeloton.com/login"
EXPIRY_BUFFER_S = 3600  # treat a token with <1h of life left as needing renewal


def _decode_jwt_exp(token: str) -> int | None:
    parts = token.split(".")
    if len(parts) != 3:
        return None
    payload_b64 = parts[1] + "=" * (-len(parts[1]) % 4)
    try:
        payload = json.loads(base64.urlsafe_b64decode(payload_b64))
    except Exception:
        return None
    return payload.get("exp")


def _token_is_valid(token: str | None) -> bool:
    if not token:
        return False
    exp = _decode_jwt_exp(token)
    if exp is None:
        # Not a JWT we can introspect — can't prove it's stale, so don't
        # force a needless re-auth over a format assumption.
        return True
    now = int(datetime.now(timezone.utc).timestamp())
    return exp > now + EXPIRY_BUFFER_S


def _prompt_for_fresh_token() -> str:
    print("Peloton bearer token is missing or expiring soon.")
    print("Opening your browser to Peloton's login page...")
    subprocess.run(["open", LOGIN_URL], check=False)
    print()
    print("Steps:")
    print("  1. Log into Peloton normally in the browser window that just opened.")
    print("  2. Open DevTools (Cmd+Option+I) -> Network tab.")
    print("  3. Click into 'Workouts' (or reload) to trigger an API request.")
    print("  4. Click any request to api.onepeloton.com in the list.")
    print("  5. In the Headers panel, find 'authorization: Bearer <token>'.")
    print("  6. Copy everything AFTER 'Bearer ' — not the word Bearer itself.")
    print()
    return getpass.getpass("Paste the token here (hidden input): ").strip()


def main() -> int:
    conn = open_db(DB_PATH)
    credential_store = CredentialStore(conn=conn)
    current_token = credential_store.get(PROVIDER, CRED_MANUAL_BEARER_TOKEN)

    if _token_is_valid(current_token):
        print("Existing Peloton token still valid — no browser step needed.")
    else:
        new_token = _prompt_for_fresh_token()
        if not new_token:
            print("No token entered. Aborting — nothing changed.")
            conn.close()
            return 1
        credential_store.rotate(PROVIDER, CRED_MANUAL_BEARER_TOKEN, new_token)
        print("Token stored.")

    conn.close()  # release before trainiq.app opens its own connection

    print("\nRunning sync...")
    return subprocess.call([sys.executable, "-m", "trainiq.app"])


if __name__ == "__main__":
    sys.exit(main())
