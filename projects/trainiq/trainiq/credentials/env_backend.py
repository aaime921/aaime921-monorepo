"""
trainiq.credentials.env_backend — Issue #70

Credential backend for a headless/cloud run (GitHub Actions on
aaime921/trainiq-data): reads `TRAINIQ_<PROVIDER>_<CREDENTIAL_TYPE>` from
the environment instead of Keychain. Selected by
`CredentialStore._default_backend()` when `TRAINIQ_CREDENTIAL_BACKEND=env`
(trainiq/credentials/store.py) — Keychain stays the default everywhere
else (AC2).

Name format matches `scripts/push_secrets_to_github.py`'s existing secret
names exactly (e.g. `TRAINIQ_PELOTON_OAUTH_REFRESH_TOKEN`,
`TRAINIQ_STRAVA_UNOFFICIAL_SESSION_COOKIE`) — provider/credential_type are
simply upper-cased and joined, since both already use underscores.

Rotation (AC3): `set()` never mutates `os.environ` — it writes to an
in-memory overlay for the lifetime of this process, and, if `out_path` is
given, atomically rewrites the WHOLE credentials-out file on every call so
a crash or a later provider's failure can never lose an already-rotated
value (e.g. Peloton's refresh token). The file is created with owner-only
permissions (AC4) and holds secret values by design — the workflow (a
separate ticket) must treat it as secret and delete it after uploading.
"""

from __future__ import annotations

import json
import os


class EnvBackend:
    """`CredentialStore` backend protocol: get/set/delete, same shape as
    the Keychain-backed one in store.py."""

    def __init__(self, out_path: str | None = None):
        self._out_path = out_path
        self._overlay: dict[str, str] = {}
        self._deleted: set[str] = set()
        # Secret-name -> current value, for everything set/rotated this
        # run. Only rotated values are written out, never the full set of
        # names a provider might have (per the architecture doc).
        self.rotated: dict[str, str] = {}

    @staticmethod
    def env_var_name(provider: str, credential_type: str) -> str:
        return f"TRAINIQ_{provider}_{credential_type}".upper()

    def get(self, provider: str, credential_type: str) -> str | None:
        name = self.env_var_name(provider, credential_type)
        if name in self._deleted:
            return None
        if name in self._overlay:
            return self._overlay[name]
        # Empty string behaves like a missing Keychain item (AC1).
        return os.environ.get(name) or None

    def set(self, provider: str, credential_type: str, value: str) -> None:
        name = self.env_var_name(provider, credential_type)
        self._overlay[name] = value
        self._deleted.discard(name)
        self.rotated[name] = value
        self._write_out_path()

    def delete(self, provider: str, credential_type: str) -> None:
        name = self.env_var_name(provider, credential_type)
        self._overlay.pop(name, None)
        self.rotated.pop(name, None)
        self._deleted.add(name)

    def _write_out_path(self) -> None:
        if not self._out_path:
            return
        payload = json.dumps({"version": 1, "credentials": dict(self.rotated)}, indent=2)
        directory = os.path.dirname(self._out_path) or "."
        os.makedirs(directory, exist_ok=True)
        tmp_path = f"{self._out_path}.tmp-{os.getpid()}"
        fd = os.open(tmp_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        try:
            with os.fdopen(fd, "w") as f:
                f.write(payload)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp_path, self._out_path)
        except Exception:
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)
            raise
