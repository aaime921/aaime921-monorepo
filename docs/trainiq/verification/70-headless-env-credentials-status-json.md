# QA verification: #70 Headless TrainIQ (PR #76)

Branch `claude/magical-euler-flp7ru`. Full suite: 677 passed, 2 failed (`test_peloton_csv_import.py`, needs
`/home/claude/peloton_work/aimea75_workouts.csv`; pre-existing, unrelated). QA added 5 end-to-end cases
(real `StravaUnofficialConnector` + 401/403 fake session, env backend, env-configured paths): all pass.
Their source is in the appendix (not committed to the PR branch per QA rules; Developer may adopt it).

| AC | Result | Reason |
|---|---|---|
| 1 env backend get/exists/missing | PASS | `test_env_credentials.py`; names match `push_secrets_to_github.py`; empty var = missing |
| 2 Keychain default, suite unchanged | PASS | 677 existing+new pass; `KeyringBackend` default |
| 3 rotation written, survives partial failure | PASS | QA test: Peloton rotation + Strava 401 → file holds rotated token, exit 3 |
| 4 0600, no secrets in logs/status.json | PASS | perms 0600 on creds-out and status.json; cookie/rotated token absent from status.json |
| 5 configurable paths, Linux default | PASS | `test_paths.py`; QA run used only env paths, no `~/Library` |
| 6 never prompts | PASS | `input` patched to fail and stdin=None: no prompt, clean exit 1 |
| 7 exit codes 0/3/1 | PASS | ok=0, partial=3, total failure=1, usage=2; documented in `headless.py` |
| 8 status.json always written | PASS | written on ok/partial/failed/crash; null last_activity_time not fabricated |
| 9 Strava cookie expired → auth_expired, partial | PASS | real connector, 401 and 403: `auth_expired` + warning, Peloton ok, exit 3, no traceback |
| 10 Mac unchanged | PASS | darwin defaults asserted in `test_paths.py`; Keychain path untouched; config seeding headless-only |
| 11 tests cover required areas | PASS | env backend, rotation file content+perms, auth-expired w/ others ok, exit codes |

Note: with `TRAINIQ_STRAVA_UNOFFICIAL_SESSION_EXPIRES_AT` absent, `authenticate()` treats the cookie as expired
(expiry=0), so the workflow must supply that secret alongside the cookie. Not an AC failure; flag for the workflow ticket.

## Appendix: QA tests (`projects/trainiq/tests/test_headless_qa70.py`)
```python
"""QA-added end-to-end checks for issue #70 (real StravaUnofficialConnector,
env backend, no keyring, no monkeypatched app dirs)."""
import json
import os
import stat
import time

import pytest

import trainiq.app as app_module
from trainiq.connectors import strava_unofficial as su
from trainiq.headless import EXIT_OK, EXIT_PARTIAL, EXIT_TOTAL_FAILURE
from tests.test_headless import _make_fake_ok
from tests.test_strava_unofficial_connector import FakeResponse, FakeStravaUnofficialSession


@pytest.fixture
def env_run(tmp_path, monkeypatch):
    for k in list(os.environ):
        if k.startswith("TRAINIQ_"):
            monkeypatch.delenv(k)
    monkeypatch.setenv("TRAINIQ_CREDENTIAL_BACKEND", "env")
    monkeypatch.setenv("TRAINIQ_DB_PATH", str(tmp_path / "d" / "t.db"))
    monkeypatch.setenv("TRAINIQ_CONFIG_PATH", str(tmp_path / "c" / "config.json"))
    monkeypatch.setenv("TRAINIQ_LOG_DIR", str(tmp_path / "logs"))
    monkeypatch.setenv("TRAINIQ_STRAVA_UNOFFICIAL_SESSION_COOKIE", "STALE-COOKIE-SECRET")
    monkeypatch.setenv("TRAINIQ_STRAVA_UNOFFICIAL_SESSION_EXPIRES_AT", str(int(time.time()) + 86400 * 5))
    return tmp_path


def _patch_strava(monkeypatch, status):
    sess = FakeStravaUnofficialSession()
    for _ in range(5):
        sess.script_get_response(FakeResponse(status))
    real = su.StravaUnofficialConnector

    class _Patched(real):
        def __init__(self, store, **kw):
            super().__init__(store, session=sess, **kw)

    monkeypatch.setattr(app_module, "StravaUnofficialConnector", _Patched)


@pytest.mark.parametrize("status", [401, 403])
def test_expired_strava_cookie_real_connector_is_partial_with_peloton_ok(env_run, monkeypatch, status):
    monkeypatch.setenv("TRAINIQ_PELOTON_EMAIL", "a@b.c")
    monkeypatch.setenv("TRAINIQ_PELOTON_PASSWORD", "pw")
    _patch_strava(monkeypatch, status)
    monkeypatch.setattr(app_module, "PelotonConnector", _make_fake_ok("peloton"))
    out = env_run / "rot.json"

    code = app_module.main(argv=["--headless", "--credentials-out", str(out)])

    assert code == EXIT_PARTIAL
    st = json.loads((env_run / "logs" / "status.json").read_text())
    assert st["providers"]["strava_unofficial"]["status"] == "auth_expired"
    assert st["providers"]["strava_unofficial"]["warning"]
    assert st["providers"]["peloton"]["status"] == "ok"
    assert st["providers"]["peloton"]["last_activity_time"] is None  # never fabricated
    assert "STALE-COOKIE-SECRET" not in json.dumps(st)


def test_only_strava_expired_is_total_failure_not_crash(env_run, monkeypatch):
    _patch_strava(monkeypatch, 401)
    code = app_module.main(argv=["--headless"])
    assert code == EXIT_TOTAL_FAILURE
    st = json.loads((env_run / "logs" / "status.json").read_text())
    assert st["providers"]["strava_unofficial"]["status"] == "auth_expired"


def test_rotation_persisted_even_when_later_provider_fails(env_run, monkeypatch):
    """AC3: Peloton-style rotation happens, then another provider fails;
    credentials-out still holds the rotated value, 0600."""
    monkeypatch.setenv("TRAINIQ_PELOTON_EMAIL", "a@b.c")
    monkeypatch.setenv("TRAINIQ_PELOTON_PASSWORD", "pw")
    _patch_strava(monkeypatch, 401)

    class Rot(_make_fake_ok("peloton")):
        def __init__(self, store):
            super().__init__(store)
            self._s = store

        def authenticate(self):
            self._s.set("peloton", "oauth_refresh_token", "ROTATED-REFRESH")
            return True

    monkeypatch.setattr(app_module, "PelotonConnector", Rot)
    out = env_run / "sub" / "rot.json"
    code = app_module.main(argv=["--headless", "--credentials-out", str(out)])
    assert code == EXIT_PARTIAL
    data = json.loads(out.read_text())
    assert data["credentials"]["TRAINIQ_PELOTON_OAUTH_REFRESH_TOKEN"] == "ROTATED-REFRESH"
    assert stat.S_IMODE(out.stat().st_mode) == 0o600
    assert "ROTATED-REFRESH" not in (env_run / "logs" / "status.json").read_text()


def test_headless_never_reads_stdin(env_run, monkeypatch):
    import builtins
    monkeypatch.setattr(builtins, "input", lambda *a: pytest.fail("prompted"))
    monkeypatch.setattr("sys.stdin", None)
    assert app_module.main(argv=["--headless"]) == EXIT_TOTAL_FAILURE
```
