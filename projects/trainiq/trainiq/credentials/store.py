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

Issue #70 (headless/cloud run): the backend behind get/set/delete is now
pluggable. `backend` defaults to Keychain (`KeyringBackend`, wrapping the
exact same module-level `keyring` calls as before — existing tests that
monkeypatch `keyring.set_keyring()` are unaffected) unless the environment
variable `TRAINIQ_CREDENTIAL_BACKEND=env` selects `EnvBackend`
(trainiq/credentials/env_backend.py), which reads
`TRAINIQ_<PROVIDER>_<TYPE>` instead. Every existing call site and every
existing test is unaffected (AC2) — `backend`/`credentials_out` are new,
optional constructor parameters, same precedent as `conn` above.
"""

from __future__ import annotations

import os
import sqlite3
from datetime import datetime, timezone

import keyring
from keyring.errors import PasswordDeleteError

from trainiq.credentials.env_backend import EnvBackend

SERVICE_PREFIX = "trainiq"
ENV_CREDENTIAL_BACKEND = "TRAINIQ_CREDENTIAL_BACKEND"
ENV_CREDENTIALS_OUT = "TRAINIQ_CREDENTIALS_OUT"


def _key(provider: str, credential_type: str) -> str:
    return f"{SERVICE_PREFIX}.{provider}.{credential_type}"


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class KeyringBackend:
    """Wraps the pre-#70 Keychain calls verbatim, through the
    module-level `keyring` import above — not an injected `keyring`
    instance — so every existing test's `keyring.set_keyring(...)`
    monkeypatch keeps working exactly as before."""

    def __init__(self, username: str):
        self._username = username

    def get(self, provider: str, credential_type: str) -> str | None:
        return keyring.get_password(_key(provider, credential_type), self._username)

    def set(self, provider: str, credential_type: str, value: str) -> None:
        keyring.set_password(_key(provider, credential_type), self._username, value)

    def delete(self, provider: str, credential_type: str) -> None:
        try:
            keyring.delete_password(_key(provider, credential_type), self._username)
        except PasswordDeleteError:
            pass  # already absent — deleting a non-existent credential is not an error


def _default_backend(username: str, credentials_out: str | None = None):
    if os.environ.get(ENV_CREDENTIAL_BACKEND) == "env":
        out_path = credentials_out if credentials_out is not None else os.environ.get(ENV_CREDENTIALS_OUT)
        return EnvBackend(out_path=out_path)
    return KeyringBackend(username)


class CredentialStore:
    """One Keychain item per (provider, credential_type) pair.

    Deliberately narrow surface area: get / set / delete / exists. No bulk
    export, no "get all credentials for a provider" — each secret is
    addressed individually so a single provider's credential can be rotated
    or revoked (Provider State Machine's Recovery Required state, ADR-010)
    without touching any other stored secret.
    """

    def __init__(
        self,
        username: str = "trainiq-user",
        conn: sqlite3.Connection | None = None,
        backend=None,
        credentials_out: str | None = None,
    ):
        # keyring addresses secrets by (service_name, username); TrainIQ is
        # single-user, so username is a fixed, documented constant rather
        # than something the caller has to think about.
        self._username = username
        # Optional: when provided, credential lifecycle events also update
        # the non-secret credentials_metadata bookkeeping table. Optional
        # rather than required so existing pure-Keychain tests are untouched.
        self._conn = conn
        # Issue #70: `backend` lets a caller inject one directly (tests);
        # `credentials_out` is the simpler case — only overrides where the
        # env backend writes rotated credentials, everything else about
        # backend selection is unchanged (`_default_backend`).
        self._backend = backend if backend is not None else _default_backend(username, credentials_out)

    def set(self, provider: str, credential_type: str, value: str) -> None:
        if not value:
            raise ValueError("Refusing to store an empty credential value")
        self._backend.set(provider, credential_type, value)
        self._mark_connected(provider)

    def get(self, provider: str, credential_type: str) -> str | None:
        return self._backend.get(provider, credential_type)

    def exists(self, provider: str, credential_type: str) -> bool:
        return self.get(provider, credential_type) is not None

    def delete(self, provider: str, credential_type: str) -> None:
        self._backend.delete(provider, credential_type)
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
