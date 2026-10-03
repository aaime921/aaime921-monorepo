"""
trainiq.credentials.store — Feature 0.2 (+ Epic 0 Hardening Finding 2)

Thin wrapper around `keyring` so provider/connector code never calls
`keyring` directly (Milestone 4 §2). This is what keeps the door open to
swap the backend later without touching connector code, and what enforces
the "one item per provider per credential type" convention.

Constitution alignment: every credential is treated as rotating by default
(Milestone 4 §2, generalizing Strava's refresh-token behavior from Milestone
1) — there is deliberately no "set once" API distinct from "rotate."

Hardening note (Epic 0 self-review, Finding 2): the SQLite `credentials_metadata`
table (Milestone 4 §5) was previously defined but never written to — two
"complete" features that didn't actually talk to each other. `conn` is now
an optional constructor parameter specifically to close that gap without
breaking the existing Keychain-only test suite (which instantiates
`CredentialStore()` with no `conn` and never touches SQLite at all).
`credentials_metadata` stores exclusively non-secret bookkeeping (connected
flag, last-refreshed timestamp) — the actual secret value never touches
SQLite, only Keychain, per Milestone 4 §2's explicit separation.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

import keyring
from keyring.errors import PasswordDeleteError

SERVICE_PREFIX = "trainiq"


def _key(provider: str, credential_type: str) -> str:
    return f"{SERVICE_PREFIX}.{provider}.{credential_type}"


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class CredentialStore:
    """One Keychain item per (provider, credential_type) pair.

    Deliberately narrow surface area: get / set / delete / exists. No bulk
    export, no "get all credentials for a provider" — each secret is
    addressed individually so a single provider's credential can be rotated
    or revoked (Provider State Machine's Recovery Required state, ADR-010)
    without touching any other stored secret.
    """

    def __init__(self, username: str = "trainiq-user", conn: sqlite3.Connection | None = None):
        # keyring addresses secrets by (service_name, username); TrainIQ is
        # single-user, so username is a fixed, documented constant rather
        # than something the caller has to think about.
        self._username = username
        # Optional: when provided, credential lifecycle events also update
        # the non-secret credentials_metadata bookkeeping table. Optional
        # rather than required so existing pure-Keychain tests are untouched.
        self._conn = conn

    def set(self, provider: str, credential_type: str, value: str) -> None:
        if not value:
            raise ValueError("Refusing to store an empty credential value")
        keyring.set_password(_key(provider, credential_type), self._username, value)
        self._mark_connected(provider)

    def get(self, provider: str, credential_type: str) -> str | None:
        return keyring.get_password(_key(provider, credential_type), self._username)

    def exists(self, provider: str, credential_type: str) -> bool:
        return self.get(provider, credential_type) is not None

    def delete(self, provider: str, credential_type: str) -> None:
        try:
            keyring.delete_password(_key(provider, credential_type), self._username)
        except PasswordDeleteError:
            pass  # already absent — deleting a non-existent credential is not an error
        self._mark_disconnected(provider)

    def rotate(self, provider: str, credential_type: str, new_value: str) -> None:
        """Explicit rotation entry point. Functionally identical to `set`,
        but named separately so call sites document intent — every token is
        rotating by default, per Constitution/Milestone 4 §2, and this makes
        that visible at the call site rather than implicit."""
        self.set(provider, credential_type, new_value)

    # --- credentials_metadata bookkeeping (non-secret only) ------------------

    def _mark_connected(self, provider: str) -> None:
        if self._conn is None:
            return
        self._conn.execute(
            """
            INSERT INTO credentials_metadata (provider, connected, last_refreshed_at)
            VALUES (?, 1, ?)
            ON CONFLICT(provider) DO UPDATE SET
                connected = 1, last_refreshed_at = excluded.last_refreshed_at
            """,
            (provider, _iso_now()),
        )
        self._conn.commit()

    def _mark_disconnected(self, provider: str) -> None:
        if self._conn is None:
            return
        self._conn.execute(
            """
            INSERT INTO credentials_metadata (provider, connected, last_refreshed_at)
            VALUES (?, 0, NULL)
            ON CONFLICT(provider) DO UPDATE SET connected = 0
            """,
            (provider,),
        )
        self._conn.commit()
