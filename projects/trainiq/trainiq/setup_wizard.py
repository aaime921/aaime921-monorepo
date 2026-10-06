"""
trainiq.setup_wizard — RC1-HF-002: First-Time Provider Configuration

Auto-triggered from trainiq.app.main() only when zero connectors are
configured — no separate entry point, per the Chief Architect's explicit
Design Review decision. Kept in its own module specifically so main()
itself stays a short composition function, not a 300-line script.

Every provider's setup follows the same shape: prompt whether to connect
-> collect credentials -> store -> validate against the real connector ->
keep on success, roll back (delete what was just stored) on ANY failure,
including an unexpected exception, not just an expected auth rejection.

Cancellation (Ctrl+C / EOF) at any point stops asking about REMAINING
providers and prints a clear message — it does NOT roll back providers
already successfully configured earlier in the same run. Undoing already-
validated, successful work because the user later got tired of typing
would be a surprising, wrong behavior, not a safety measure.

Strava's OAuth uses manual authorization-code paste, not a local HTTP
listener — zero new networking, zero new dependencies, an entirely
reasonable trade-off for a setup step run once, per the Chief Architect's
explicit decision.

RC1-HF-003: Eufy's device ID is discovered automatically from the login
response (verified live against a real account — no manual entry, no
separate device-list call needed). If more than one device is found, the
user is shown a short numbered choice; if exactly one, it's selected
without asking.
"""

from __future__ import annotations

import getpass
from pathlib import Path
from typing import Optional

from stravalib import Client as StravalibClient
from stravalib import exc as stravalib_exc

from trainiq.config import set_eufy_device_id
from trainiq.connectors.eufy import CRED_EMAIL as EUFY_CRED_EMAIL
from trainiq.connectors.eufy import CRED_PASSWORD as EUFY_CRED_PASSWORD
from trainiq.connectors.eufy import PROVIDER as EUFY_PROVIDER
from trainiq.connectors.eufy import EufyConnector
from trainiq.connectors.peloton import CRED_EMAIL as PELOTON_CRED_EMAIL
from trainiq.connectors.peloton import CRED_PASSWORD as PELOTON_CRED_PASSWORD
from trainiq.connectors.peloton import PROVIDER as PELOTON_PROVIDER
from trainiq.connectors.peloton import PelotonConnector
from trainiq.connectors.strava import CRED_ACCESS_TOKEN as STRAVA_CRED_ACCESS_TOKEN
from trainiq.connectors.strava import CRED_EXPIRES_AT as STRAVA_CRED_EXPIRES_AT
from trainiq.connectors.strava import CRED_REFRESH_TOKEN as STRAVA_CRED_REFRESH_TOKEN
from trainiq.connectors.strava import PROVIDER as STRAVA_PROVIDER
from trainiq.connectors.strava import _client_id_and_secret
from trainiq.connectors.strava_unofficial import StravaUnofficialConnector
from trainiq.credentials.store import CredentialStore


class SetupCancelled(Exception):
    """Raised internally when the user cancels via Ctrl+C/EOF, caught once
    at the top of run_first_time_setup() — never propagates out of this
    module."""


def _prompt_yes_no(question: str) -> bool:
    try:
        answer = input(f"{question} [y/N] ").strip().lower()
    except (KeyboardInterrupt, EOFError, OSError):
        # OSError specifically covers a genuinely non-interactive launch
        # (no attached terminal at all, e.g. a future background-scheduler
        # invocation) — not just an interactive Ctrl+C/EOF. Discovered via
        # a test environment surfacing it first, but the underlying
        # scenario (no stdin available) is a real one, not a test artifact.
        raise SetupCancelled()
    return answer in ("y", "yes")


def _prompt_text(question: str) -> str:
    try:
        return input(f"{question}: ").strip()
    except (KeyboardInterrupt, EOFError, OSError):
        raise SetupCancelled()


def _prompt_password(question: str) -> str:
    try:
        return getpass.getpass(f"{question}: ")
    except (KeyboardInterrupt, EOFError, OSError):
        raise SetupCancelled()


def _setup_strava(credential_store: CredentialStore, stravalib_client: Optional[StravalibClient] = None) -> bool:
    """Returns True if Strava ended up connected. Nothing is ever stored
    until exchange_code_for_token() succeeds — there is no "store then
    roll back" case for Strava, unlike Peloton/Eufy, since validation and
    the only write both happen at the same, single successful step."""
    print("\n--- Strava ---")
    if not _prompt_yes_no("Connect Strava now?"):
        print("Strava: skipped")
        return False

    try:
        client_id, client_secret = _client_id_and_secret()
    except RuntimeError as exc:
        print(f"Strava: skipped — {exc}")
        return False

    client = stravalib_client if stravalib_client is not None else StravalibClient()
    auth_url = client.authorization_url(
        client_id=int(client_id), redirect_uri="http://localhost",
        scope=["read", "activity:read_all"],
    )
    print(f"Open this URL in your browser and approve access:\n  {auth_url}")
    print("After approving, copy the 'code' value from the redirect URL's address bar.")
    code = _prompt_text("Paste the authorization code here")

    try:
        token = client.exchange_code_for_token(client_id=int(client_id), client_secret=client_secret, code=code)
    except stravalib_exc.AuthError as exc:
        print(f"Strava: connection failed — {exc}")
        return False

    credential_store.set(STRAVA_PROVIDER, STRAVA_CRED_REFRESH_TOKEN, token.refresh_token)
    credential_store.set(STRAVA_PROVIDER, STRAVA_CRED_ACCESS_TOKEN, token.access_token)
    credential_store.set(STRAVA_PROVIDER, STRAVA_CRED_EXPIRES_AT, str(token.expires_at))
    print("Strava: connected")
    return True


def _setup_strava_unofficial(credential_store: CredentialStore, connector=None) -> bool:
    """Returns True if the unofficial (session-cookie) Strava connector
    ended up connected. Independent of _setup_strava() — this step is
    offered unconditionally, not gated on the official step's outcome, per
    #18's 'both connectors can coexist' design and the Architect's
    resolution of the requirements doc's open question. Unlike Peloton/
    Eufy, there is no separate 'store then roll back' case: the
    connector's own submit_manual_recovery() validates via a live request
    BEFORE persisting anything and is also the only write path — same
    relationship _setup_strava() already has with exchange_code_for_token().
    """
    print("\n--- Strava (unofficial, session cookie) ---")
    if not _prompt_yes_no("Connect Strava via session cookie now?"):
        print("Strava (unofficial): skipped")
        return False

    conn = connector if connector is not None else StravaUnofficialConnector(credential_store)
    print(conn.request_manual_recovery())
    cookie_value = _prompt_text("Paste the _strava4_session cookie value here")

    try:
        ok = conn.submit_manual_recovery(cookie_value)
    except Exception as exc:  # noqa: BLE001 — deliberate: roll back on ANYTHING unexpected, matching Peloton/Eufy's rule
        print(f"Strava (unofficial): connection failed unexpectedly ({type(exc).__name__}) — nothing saved")
        return False

    if not ok:
        print("Strava (unofficial): cookie rejected. Nothing saved.")
        return False

    print("Strava (unofficial): connected")
    return True


def _setup_email_password_provider(
    credential_store: CredentialStore,
    provider: str,
    cred_email: str,
    cred_password: str,
    display_name: str,
    build_connector,
) -> bool:
    """Shared shape for Peloton and Eufy: prompt, store, validate, keep or
    roll back on ANY failure — an authentication rejection OR an
    unexpected exception, per the Chief Architect's explicit instruction
    that the rollback must not be conditional on the failure being an
    anticipated one."""
    print(f"\n--- {display_name} ---")
    if not _prompt_yes_no(f"Connect {display_name} now?"):
        print(f"{display_name}: skipped")
        return False

    email = _prompt_text("Email")
    password = _prompt_password("Password")

    credential_store.set(provider, cred_email, email)
    credential_store.set(provider, cred_password, password)

    try:
        connector = build_connector()
        ok = connector.authenticate()
    except Exception as exc:  # noqa: BLE001 — deliberate: roll back on ANYTHING unexpected, not just a clean auth rejection
        credential_store.delete(provider, cred_email)
        credential_store.delete(provider, cred_password)
        print(f"{display_name}: connection failed unexpectedly ({type(exc).__name__}) — nothing saved")
        return False

    if not ok:
        credential_store.delete(provider, cred_email)
        credential_store.delete(provider, cred_password)
        print(f"{display_name}: login rejected — check your email and password. Nothing saved.")
        return False

    print(f"{display_name}: connected")
    return True


def _setup_peloton(credential_store: CredentialStore, session=None) -> bool:
    return _setup_email_password_provider(
        credential_store, PELOTON_PROVIDER, PELOTON_CRED_EMAIL, PELOTON_CRED_PASSWORD, "Peloton",
        build_connector=lambda: PelotonConnector(credential_store, session=session),
    )


def _setup_eufy(credential_store: CredentialStore, config_path: Path, session=None) -> bool:
    """RC1-HF-003: no longer prompts for a device ID. Verified live that
    the login response itself includes a `devices` array — the connector
    discovers it automatically, this wizard step only decides WHICH
    device to use when more than one is present."""
    print("\n--- Eufy ---")
    if not _prompt_yes_no("Connect Eufy now?"):
        print("Eufy: skipped")
        return False

    email = _prompt_text("Email")
    password = _prompt_password("Password")

    credential_store.set(EUFY_PROVIDER, EUFY_CRED_EMAIL, email)
    credential_store.set(EUFY_PROVIDER, EUFY_CRED_PASSWORD, password)

    try:
        connector = EufyConnector(credential_store, session=session)
        ok = connector.authenticate()
    except Exception as exc:  # noqa: BLE001 — same deliberate broad catch as Peloton
        credential_store.delete(EUFY_PROVIDER, EUFY_CRED_EMAIL)
        credential_store.delete(EUFY_PROVIDER, EUFY_CRED_PASSWORD)
        print(f"Eufy: connection failed unexpectedly ({type(exc).__name__}) — nothing saved")
        return False

    if not ok:
        credential_store.delete(EUFY_PROVIDER, EUFY_CRED_EMAIL)
        credential_store.delete(EUFY_PROVIDER, EUFY_CRED_PASSWORD)
        print("Eufy: login rejected — check your email and password. Nothing saved.")
        return False

    devices = connector.discovered_devices()
    if not devices:
        credential_store.delete(EUFY_PROVIDER, EUFY_CRED_EMAIL)
        credential_store.delete(EUFY_PROVIDER, EUFY_CRED_PASSWORD)
        print("Eufy: connected, but no devices were found on this account. Nothing saved.")
        return False

    # Soft heuristic, not a hard requirement — prefer devices whose name
    # looks like a scale, but fall back to the full list if that filter
    # finds nothing (naming conventions aren't confirmed beyond the one
    # live example this connector was verified against).
    scale_like = [d for d in devices if "scale" in str(d.get("name", "")).lower()]
    candidates = scale_like if scale_like else devices

    if len(candidates) == 1:
        chosen = candidates[0]
    else:
        print("Multiple devices found on this account:")
        for i, device in enumerate(candidates, start=1):
            print(f"  {i}. {device.get('name', 'Unknown device')} ({device.get('id')})")
        chosen = None
        while chosen is None:
            choice = _prompt_text(f"Choose a device [1-{len(candidates)}]")
            if choice.isdigit() and 1 <= int(choice) <= len(candidates):
                chosen = candidates[int(choice) - 1]
            else:
                print("Invalid choice, try again.")

    device_id = chosen.get("id")
    if not device_id:
        credential_store.delete(EUFY_PROVIDER, EUFY_CRED_EMAIL)
        credential_store.delete(EUFY_PROVIDER, EUFY_CRED_PASSWORD)
        print("Eufy: the selected device had no usable id. Nothing saved.")
        return False

    connector.select_device(device_id)
    set_eufy_device_id(config_path, device_id)
    print(f"Eufy: connected ({chosen.get('name', device_id)})")
    return True


def _run_provider_setup_steps(credential_store: CredentialStore, config_path: Path) -> None:
    """Shared body for both run_first_time_setup() and run_configure(): the
    four per-provider setup steps, in the same fixed order, under the same
    SetupCancelled propagation — exactly one implementation of 'ask about
    each provider' for both paths to share, so a behavior change to one
    provider's prompt affects both identically, by construction."""
    _setup_strava(credential_store)
    _setup_strava_unofficial(credential_store)
    _setup_peloton(credential_store)
    _setup_eufy(credential_store, config_path)


def run_first_time_setup(credential_store: CredentialStore, config_path: Path) -> None:
    print("No providers are configured yet. Let's connect at least one.\n")
    try:
        _run_provider_setup_steps(credential_store, config_path)
    except SetupCancelled:
        print("\nSetup cancelled.")
        return
    print("\nSetup complete.")


def run_configure(
    credential_store: CredentialStore,
    config_path: Path,
    status_lines: list[str],
) -> None:
    """Explicit add/reconfigure entry point, triggered by `--configure`
    regardless of how many connectors are currently configured. Shows the
    already-computed per-provider status as a header, then runs the same
    four prompts first-time setup uses. Answering "y" to an
    already-configured provider naturally reconfigures it —
    credential_store.set() overwrites the existing entry, and every
    _setup_* function already validates-then-stores (or rolls back on
    failure) regardless of prior state. Answering "n" leaves that
    provider's existing credentials untouched."""
    print("Current connectors:")
    for line in status_lines:
        print(f"  {line}")
    print()
    try:
        _run_provider_setup_steps(credential_store, config_path)
    except SetupCancelled:
        print("\nConfiguration cancelled.")
        return
    print("\nConfiguration complete.")
