#!/usr/bin/env python3
"""
scripts/setup_peloton_oauth.py — one-time setup for PelotonConnector's
OAuth+PKCE auth path (issue #7).

Permanent CLI entry point (same category as scripts/import_peloton_csv.py,
not a delete-after-use POC like scripts/debug_peloton.py). This is the
human-in-the-loop step `PelotonConnector.complete_oauth_setup()` requires:
a real person logs into Peloton in a browser and pastes back the
authorization code this script's printed URL produces. See
docs/setup/peloton-oauth.md for the full walkthrough, including why
JavaScript must be blocked for members.onepeloton.com first.

Explicitly does NOT script or automate the login form itself — per issue
#7's requirements doc, that step stays manual by design.

Usage:
    python3 scripts/setup_peloton_oauth.py
"""

from __future__ import annotations

import sys
import urllib.parse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from trainiq.connectors.peloton import (
    OAUTH_AUDIENCE,
    OAUTH_AUTHORIZE_URL,
    OAUTH_CLIENT_ID,
    OAUTH_REDIRECT_URI,
    OAUTH_SCOPE,
    PelotonConnector,
    PelotonOAuthRejected,
    generate_pkce_pair,
)
from trainiq.credentials.store import CredentialStore
from trainiq.sync.engine import TransientError

APP_SUPPORT_DIR = Path.home() / "Library" / "Application Support" / "TrainIQ"
DB_PATH = APP_SUPPORT_DIR / "trainiq.db"


def _build_authorization_url(code_challenge: str) -> str:
    params = {
        "client_id": OAUTH_CLIENT_ID,
        "redirect_uri": OAUTH_REDIRECT_URI,
        "response_type": "code",
        "scope": OAUTH_SCOPE,
        "audience": OAUTH_AUDIENCE,
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
    }
    return f"{OAUTH_AUTHORIZE_URL}?{urllib.parse.urlencode(params)}"


def _extract_authorization_code(raw_input: str) -> str:
    """Accept either a bare authorization code or the full redirect URL
    it's embedded in — pasting the whole address-bar contents is the
    natural thing to do and is what most people will actually paste (see
    issue #7's live bug report), not just the isolated query parameter."""
    stripped = raw_input.strip()
    parsed = urllib.parse.urlparse(stripped)
    if parsed.scheme and parsed.netloc:
        code_values = urllib.parse.parse_qs(parsed.query).get("code")
        if code_values:
            return code_values[0]
    return stripped


def main() -> int:
    print("=" * 72)
    print("Peloton OAuth setup (issue #7)")
    print("=" * 72)
    print(
        "\nBEFORE continuing, block JavaScript for members.onepeloton.com — "
        "see docs/setup/peloton-oauth.md for why and exactly how. Skipping "
        "this step means the site's own callback page will consume the "
        "authorization code before you can copy it.\n"
    )
    input("Press Enter once JavaScript is blocked and you're ready to continue: ")

    code_verifier, code_challenge = generate_pkce_pair()
    auth_url = _build_authorization_url(code_challenge)

    print("\nOpen this URL in your browser and log in:")
    print(f"  {auth_url}")
    print(
        "\nAfter logging in, the browser will try to redirect and fail to "
        "load (expected, since JavaScript is blocked). Copy either just the "
        "'code' query parameter's value, or the whole address bar contents "
        "— either works."
    )
    raw_input_value = input("\nPaste the 'code' value or full redirect URL here: ")
    authorization_code = _extract_authorization_code(raw_input_value)
    if not authorization_code:
        print("No code entered — nothing to exchange. Exiting.")
        return 1

    APP_SUPPORT_DIR.mkdir(parents=True, exist_ok=True)
    from trainiq.storage.schema import open_db

    conn = open_db(DB_PATH)
    try:
        credential_store = CredentialStore(conn=conn)
        connector = PelotonConnector(credential_store)
        try:
            connector.complete_oauth_setup(authorization_code, code_verifier)
        except PelotonOAuthRejected as exc:
            print(f"\nThe authorization code was rejected: {exc}")
            print("Codes are single-use and short-lived — restart this script and try again.")
            return 1
        except TransientError as exc:
            print(f"\nTransient error talking to Peloton's token endpoint: {exc}")
            print("Restart this script and try again in a moment.")
            return 1
    finally:
        conn.close()

    print("\nPeloton OAuth setup complete. Scheduled syncs will now refresh "
          "access tokens silently, with the manual bearer-token path kept "
          "as a fallback if this ever stops working.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
