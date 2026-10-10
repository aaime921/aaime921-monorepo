"""
Tests for Issue #70's headless run: trainiq.headless (status mapping,
exit codes, status.json writer) and its wiring into trainiq.app.main()'s
`--headless` path.
"""

from __future__ import annotations

import json
import stat
from pathlib import Path

import pytest

from trainiq.connectors.base import ConnectorState
from trainiq.credentials.store import CredentialStore
from trainiq.headless import (
    EXIT_OK,
    EXIT_PARTIAL,
    EXIT_TOTAL_FAILURE,
    EXIT_USAGE_ERROR,
    ProviderStatus,
    StatusReport,
    build_status_report,
    last_activity_time,
    write_status_report,
)
from trainiq.storage.schema import open_db
from trainiq.sync.engine import ConnectorSyncResult


# --- StatusReport.outcome()/exit_code() -------------------------------------

def test_all_ok_outcome_and_exit_code():
    report = StatusReport(started_at="t0")
    report.providers["strava"] = ProviderStatus(status="ok")
    report.providers["peloton"] = ProviderStatus(status="ok")
    assert report.outcome() == "ok"
    assert report.exit_code() == EXIT_OK


def test_one_ok_one_auth_expired_is_partial():
    report = StatusReport(started_at="t0")
    report.providers["strava_unofficial"] = ProviderStatus(status="auth_expired")
    report.providers["peloton"] = ProviderStatus(status="ok")
    assert report.outcome() == "partial"
    assert report.exit_code() == EXIT_PARTIAL


def test_all_failed_is_total_failure():
    report = StatusReport(started_at="t0")
    report.providers["strava"] = ProviderStatus(status="failed")
    report.providers["peloton"] = ProviderStatus(status="auth_expired")
    assert report.outcome() == "failed"
    assert report.exit_code() == EXIT_TOTAL_FAILURE


def test_no_providers_at_all_is_total_failure():
    report = StatusReport(started_at="t0")
    assert report.outcome() == "failed"
    assert report.exit_code() == EXIT_TOTAL_FAILURE


def test_to_dict_omits_warning_key_when_none_present():
    status = ProviderStatus(status="ok", records_synced=3, last_activity_time="2026-01-01T00:00:00+00:00")
    d = status.to_dict()
    assert "warning" not in d


def test_to_dict_includes_warning_when_present():
    status = ProviderStatus(status="failed", warning="strava: boom")
    d = status.to_dict()
    assert d["warning"] == "strava: boom"


# --- build_status_report() --------------------------------------------------

class _FakeConnector:
    def __init__(self, provider, record_kind=None):
        self.provider = provider
        self.record_kind = record_kind


@pytest.fixture
def db(tmp_path: Path):
    conn = open_db(tmp_path / "trainiq.db")
    yield conn
    conn.close()


def test_build_status_report_maps_successful_result_to_ok(db):
    connector = _FakeConnector("strava")
    result = ConnectorSyncResult(provider="strava", state=ConnectorState.HEALTHY, records_upserted=5)

    report = build_status_report("t0", db, [connector], [result])

    assert report.providers["strava"].status == "ok"
    assert report.providers["strava"].records_synced == 5
    assert report.providers["strava"].warning is None


def test_build_status_report_maps_auth_failed_to_auth_expired_with_warning(db):
    connector = _FakeConnector("strava_unofficial")
    result = ConnectorSyncResult(
        provider="strava_unofficial", state=ConnectorState.DEGRADED,
        error="strava_unofficial: authenticate() returned False", auth_failed=True,
    )

    report = build_status_report("t0", db, [connector], [result])

    status = report.providers["strava_unofficial"]
    assert status.status == "auth_expired"
    assert status.warning is not None
    assert "strava_unofficial" in status.warning


def test_build_status_report_maps_non_auth_error_to_failed(db):
    connector = _FakeConnector("peloton")
    result = ConnectorSyncResult(provider="peloton", state=ConnectorState.WARNING, error="peloton: rate limited")

    report = build_status_report("t0", db, [connector], [result])

    assert report.providers["peloton"].status == "failed"
    assert report.providers["peloton"].warning == "peloton: rate limited"


def test_build_status_report_never_fabricates_last_activity_time(db):
    """No normalized_activities rows for this provider yet -> null, never
    a fabricated/estimated value."""
    connector = _FakeConnector("strava")
    result = ConnectorSyncResult(provider="strava", state=ConnectorState.HEALTHY)

    report = build_status_report("t0", db, [connector], [result])

    assert report.providers["strava"].last_activity_time is None


def test_last_activity_time_reads_normalized_activities_for_activity_kind(db):
    from trainiq.connectors.base import RecordKind

    db.execute(
        """
        INSERT INTO normalized_activities
            (provider, external_id, start_time, duration_s, discipline, distance_m,
             avg_hr, max_hr, avg_power, max_power, calories,
             training_load, training_load_method, source_confidence)
        VALUES ('strava', 'a1', '2026-02-01T07:00:00+00:00', 1800, 'running', NULL,
                NULL, NULL, NULL, NULL, NULL, NULL, 'unknown', 1.0)
        """
    )
    db.commit()

    assert last_activity_time(db, "strava", RecordKind.ACTIVITY) == "2026-02-01T07:00:00+00:00"


def test_last_activity_time_reads_weigh_ins_for_weigh_in_kind(db):
    from trainiq.connectors.base import RecordKind

    db.execute(
        """
        INSERT INTO weigh_ins (provider, external_id, timestamp, weight_kg, body_fat_pct, muscle_mass_pct)
        VALUES ('eufy', 'w1', '2026-02-02T08:00:00+00:00', 70.0, NULL, NULL)
        """
    )
    db.commit()

    assert last_activity_time(db, "eufy", RecordKind.WEIGH_IN) == "2026-02-02T08:00:00+00:00"


# --- write_status_report() --------------------------------------------------

def test_write_status_report_is_valid_json_with_owner_only_permissions(tmp_path):
    report = StatusReport(started_at="t0", finished_at="t1")
    report.providers["strava"] = ProviderStatus(status="ok", records_synced=1)
    path = tmp_path / "status.json"

    write_status_report(report, path)

    assert path.exists()
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    payload = json.loads(path.read_text())
    assert payload["version"] == 1
    assert payload["outcome"] == "ok"
    assert payload["exit_code"] == EXIT_OK
    assert payload["providers"]["strava"]["status"] == "ok"


def test_write_status_report_never_contains_a_known_secret_value(tmp_path):
    report = StatusReport(started_at="t0", finished_at="t1")
    report.providers["strava_unofficial"] = ProviderStatus(
        status="auth_expired",
        warning="strava_unofficial: authentication expired or was rejected; "
                "re-authenticate and refresh its TRAINIQ_STRAVA_UNOFFICIAL_* credential(s)",
    )
    path = tmp_path / "status.json"

    write_status_report(report, path)

    raw = path.read_text()
    assert "super-secret-cookie-value-12345" not in raw


# --- Integration: trainiq.app.main(["--headless", ...]) --------------------

@pytest.fixture(autouse=True)
def in_memory_keyring():
    import keyring
    from keyring.backends.fail import Keyring as FailKeyring

    class _InMemoryKeyring(FailKeyring):
        priority = 1

        def __init__(self):
            self._store: dict[tuple[str, str], str] = {}

        def set_password(self, service, username, password):
            self._store[(service, username)] = password

        def get_password(self, service, username):
            return self._store.get((service, username))

        def delete_password(self, service, username):
            key = (service, username)
            if key not in self._store:
                from keyring.errors import PasswordDeleteError
                raise PasswordDeleteError("not found")
            del self._store[key]

    original = keyring.get_keyring()
    keyring.set_keyring(_InMemoryKeyring())
    yield
    keyring.set_keyring(original)


@pytest.fixture
def isolated_app_dirs(tmp_path, monkeypatch):
    import trainiq.app as app_module

    app_support = tmp_path / "AppSupport"
    log_dir = tmp_path / "Logs"
    cfg_path = app_support / "config.json"
    monkeypatch.setattr(app_module, "APP_SUPPORT_DIR", app_support)
    monkeypatch.setattr(app_module, "LOG_DIR", log_dir)
    monkeypatch.setattr(app_module, "CONFIG_PATH", cfg_path)
    return app_support, log_dir, cfg_path


class _FakeHeadlessConnector:
    supports_incremental_sync = True

    def __init__(self, credential_store):
        self.provider = "fake"

    def authenticate(self):
        return True

    def download(self, since=None):
        return []

    def normalize(self, raw):
        return raw

    def get_state(self):
        return ConnectorState.HEALTHY

    def restore_state(self, state):
        pass

    def transition_state(self, to_state, detail=None):
        pass

    def active_strategy(self):
        return None

    record_kind = None


def _make_fake_ok(provider_name):
    class _Fake(_FakeHeadlessConnector):
        def __init__(self, credential_store):
            super().__init__(credential_store)
            self.provider = provider_name
    from trainiq.connectors.base import RecordKind
    _Fake.record_kind = RecordKind.ACTIVITY
    return _Fake


def _make_fake_auth_failing(provider_name):
    class _Fake(_FakeHeadlessConnector):
        def __init__(self, credential_store):
            super().__init__(credential_store)
            self.provider = provider_name

        def authenticate(self):
            return False

        def get_state(self):
            return ConnectorState.DEGRADED
    from trainiq.connectors.base import RecordKind
    _Fake.record_kind = RecordKind.ACTIVITY
    return _Fake


def _read_status_json(log_dir: Path) -> dict:
    return json.loads((log_dir / "status.json").read_text())


def test_headless_all_providers_ok_exits_zero(isolated_app_dirs, monkeypatch):
    import trainiq.app as app_module
    from trainiq.connectors.strava import PROVIDER as STRAVA_PROVIDER

    monkeypatch.setattr(app_module, "StravaConnector", _make_fake_ok(STRAVA_PROVIDER))
    app_support, log_dir, cfg_path = isolated_app_dirs
    app_support.mkdir(parents=True, exist_ok=True)
    conn = open_db(app_support / "trainiq.db")
    CredentialStore(conn=conn).set(STRAVA_PROVIDER, "refresh_token", "token")
    conn.close()

    exit_code = app_module.main(argv=["--headless"])

    assert exit_code == EXIT_OK
    status = _read_status_json(log_dir)
    assert status["outcome"] == "ok"
    assert status["providers"][STRAVA_PROVIDER]["status"] == "ok"


def test_headless_one_auth_expired_one_ok_is_partial_no_crash(isolated_app_dirs, monkeypatch):
    """AC9: an auth-expired provider (standing in for Strava's cookie
    expiry) must not stop the other configured provider from syncing, and
    must exit with the dedicated partial code, not raise."""
    import trainiq.app as app_module
    from trainiq.connectors.peloton import PROVIDER as PELOTON_PROVIDER
    from trainiq.connectors.strava import PROVIDER as STRAVA_PROVIDER

    monkeypatch.setattr(app_module, "StravaConnector", _make_fake_auth_failing(STRAVA_PROVIDER))
    monkeypatch.setattr(app_module, "PelotonConnector", _make_fake_ok(PELOTON_PROVIDER))
    app_support, log_dir, cfg_path = isolated_app_dirs
    app_support.mkdir(parents=True, exist_ok=True)
    conn = open_db(app_support / "trainiq.db")
    store = CredentialStore(conn=conn)
    store.set(STRAVA_PROVIDER, "refresh_token", "token")
    store.set(PELOTON_PROVIDER, "email", "a@b.com")
    store.set(PELOTON_PROVIDER, "password", "pw")
    conn.close()

    exit_code = app_module.main(argv=["--headless"])

    assert exit_code == EXIT_PARTIAL
    status = _read_status_json(log_dir)
    assert status["outcome"] == "partial"
    assert status["providers"][STRAVA_PROVIDER]["status"] == "auth_expired"
    assert status["providers"][STRAVA_PROVIDER]["warning"]
    assert status["providers"][PELOTON_PROVIDER]["status"] == "ok"


def test_headless_all_providers_failing_exits_total_failure(isolated_app_dirs, monkeypatch):
    import trainiq.app as app_module
    from trainiq.connectors.strava import PROVIDER as STRAVA_PROVIDER

    monkeypatch.setattr(app_module, "StravaConnector", _make_fake_auth_failing(STRAVA_PROVIDER))
    app_support, log_dir, cfg_path = isolated_app_dirs
    app_support.mkdir(parents=True, exist_ok=True)
    conn = open_db(app_support / "trainiq.db")
    CredentialStore(conn=conn).set(STRAVA_PROVIDER, "refresh_token", "token")
    conn.close()

    exit_code = app_module.main(argv=["--headless"])

    assert exit_code == EXIT_TOTAL_FAILURE
    status = _read_status_json(log_dir)
    assert status["outcome"] == "failed"


def test_headless_no_connectors_configured_exits_total_failure_and_never_prompts(isolated_app_dirs, monkeypatch):
    import trainiq.app as app_module

    def _fail_if_called(*_args, **_kwargs):
        raise AssertionError("headless must never call input()")

    monkeypatch.setattr("builtins.input", _fail_if_called)

    exit_code = app_module.main(argv=["--headless"])

    assert exit_code == EXIT_TOTAL_FAILURE
    app_support, log_dir, cfg_path = isolated_app_dirs
    status = _read_status_json(log_dir)
    assert status["outcome"] == "failed"
    assert any("no connectors configured" in w for w in status["warnings"])


def test_headless_never_triggers_first_time_setup_wizard(isolated_app_dirs, monkeypatch):
    import trainiq.app as app_module

    def _fail_if_called(*_args, **_kwargs):
        raise AssertionError("run_first_time_setup must never run in --headless mode")

    monkeypatch.setattr(app_module, "run_first_time_setup", _fail_if_called)

    exit_code = app_module.main(argv=["--headless"])

    assert exit_code == EXIT_TOTAL_FAILURE  # nothing configured, but no wizard ran to get there


def test_headless_combined_with_configure_is_a_usage_error(isolated_app_dirs):
    import trainiq.app as app_module

    exit_code = app_module.main(argv=["--headless", "--configure"])

    assert exit_code == EXIT_USAGE_ERROR


def test_headless_env_var_equivalent_to_flag(isolated_app_dirs, monkeypatch):
    import trainiq.app as app_module

    monkeypatch.setenv("TRAINIQ_HEADLESS", "1")

    def _fail_if_called(*_args, **_kwargs):
        raise AssertionError("headless (via env var) must never call input()")

    monkeypatch.setattr("builtins.input", _fail_if_called)

    exit_code = app_module.main(argv=[])

    assert exit_code == EXIT_TOTAL_FAILURE  # nothing configured; the point is it never prompted


def test_headless_unexpected_crash_still_writes_status_json(isolated_app_dirs, monkeypatch):
    import trainiq.app as app_module
    from trainiq.connectors.strava import PROVIDER as STRAVA_PROVIDER

    monkeypatch.setattr(app_module, "StravaConnector", _make_fake_ok(STRAVA_PROVIDER))

    class _ExplodingEngine:
        def __init__(self, *args, **kwargs):
            pass

        def run_once(self, connectors):
            raise RuntimeError("simulated crash mid-run")

    monkeypatch.setattr(app_module, "SynchronizationEngine", _ExplodingEngine)

    app_support, log_dir, cfg_path = isolated_app_dirs
    app_support.mkdir(parents=True, exist_ok=True)
    conn = open_db(app_support / "trainiq.db")
    CredentialStore(conn=conn).set(STRAVA_PROVIDER, "refresh_token", "token")
    conn.close()

    exit_code = app_module.main(argv=["--headless"])

    assert exit_code == EXIT_TOTAL_FAILURE
    status = _read_status_json(log_dir)
    assert status["outcome"] == "failed"
    assert any("simulated crash mid-run" in w for w in status["warnings"])


def test_headless_credentials_out_flag_writes_rotated_credentials(isolated_app_dirs, monkeypatch, tmp_path):
    """A rotated credential under the env backend must survive to the
    credentials-out file — status.json itself must never carry the
    secret value (AC4)."""
    import trainiq.app as app_module
    from trainiq.connectors.strava import PROVIDER as STRAVA_PROVIDER

    monkeypatch.setenv("TRAINIQ_CREDENTIAL_BACKEND", "env")
    monkeypatch.setenv(f"TRAINIQ_{STRAVA_PROVIDER}_REFRESH_TOKEN".upper(), "super-secret-cookie-value-12345")

    class _RotatingConnector(_FakeHeadlessConnector):
        def __init__(self, credential_store):
            super().__init__(credential_store)
            self.provider = STRAVA_PROVIDER
            self._store = credential_store

        def authenticate(self):
            self._store.rotate(STRAVA_PROVIDER, "refresh_token", "rotated-secret-value-67890")
            return True
    from trainiq.connectors.base import RecordKind
    _RotatingConnector.record_kind = RecordKind.ACTIVITY

    monkeypatch.setattr(app_module, "StravaConnector", _RotatingConnector)

    app_support, log_dir, cfg_path = isolated_app_dirs
    credentials_out = tmp_path / "rotated.json"

    exit_code = app_module.main(argv=["--headless", "--credentials-out", str(credentials_out)])

    assert exit_code == EXIT_OK
    assert "rotated-secret-value-67890" in credentials_out.read_text()
    status = _read_status_json(log_dir)
    assert "rotated-secret-value-67890" not in json.dumps(status)
    assert "super-secret-cookie-value-12345" not in json.dumps(status)
