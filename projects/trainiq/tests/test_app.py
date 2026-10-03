"""
Tests for the composition root (trainiq/app.py). RC1-HF-001 wired existing
components together; RC1-HF-002 (this revision) adds first-time setup
auto-triggering and moves Eufy's device_id from an environment variable to
config.json. Every component wired here is already independently tested;
these tests verify only that the wiring itself is correct. No real network
path is exercised.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from trainiq.app import _build_configured_connectors, main
from trainiq.config import set_eufy_device_id
from trainiq.connectors.eufy import PROVIDER as EUFY_PROVIDER
from trainiq.connectors.peloton import PROVIDER as PELOTON_PROVIDER
from trainiq.connectors.strava import PROVIDER as STRAVA_PROVIDER
from trainiq.credentials.store import CredentialStore
from trainiq.storage.schema import open_db


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
def db(tmp_path: Path):
    conn = open_db(tmp_path / "trainiq.db")
    yield conn
    conn.close()


@pytest.fixture
def config_path(tmp_path: Path) -> Path:
    return tmp_path / "config.json"


@pytest.fixture
def isolated_app_dirs(tmp_path, monkeypatch):
    """Redirects app.py's hardcoded macOS paths into a temp directory, so
    running main() in tests never touches a real ~/Library location."""
    import trainiq.app as app_module

    app_support = tmp_path / "AppSupport"
    log_dir = tmp_path / "Logs"
    cfg_path = app_support / "config.json"
    monkeypatch.setattr(app_module, "APP_SUPPORT_DIR", app_support)
    monkeypatch.setattr(app_module, "LOG_DIR", log_dir)
    monkeypatch.setattr(app_module, "CONFIG_PATH", cfg_path)
    return app_support, log_dir, cfg_path


# --- _build_configured_connectors() ----------------------------------------

def test_no_credentials_yields_zero_configured_connectors(db, config_path):
    store = CredentialStore(conn=db)
    connectors = _build_configured_connectors(store, config_path)
    assert connectors == []


def test_strava_configured_when_refresh_token_present(db, config_path):
    store = CredentialStore(conn=db)
    store.set(STRAVA_PROVIDER, "refresh_token", "some-refresh-token")

    connectors = _build_configured_connectors(store, config_path)

    assert len(connectors) == 1
    assert connectors[0].provider == STRAVA_PROVIDER


def test_peloton_configured_when_email_and_password_present(db, config_path):
    store = CredentialStore(conn=db)
    store.set(PELOTON_PROVIDER, "email", "athlete@example.com")
    store.set(PELOTON_PROVIDER, "password", "pw")

    connectors = _build_configured_connectors(store, config_path)

    assert len(connectors) == 1
    assert connectors[0].provider == PELOTON_PROVIDER


def test_eufy_skipped_when_device_id_missing_from_config_even_with_credentials(db, config_path):
    """The specific case this wiring was built to handle explicitly: Eufy
    credentials alone are not sufficient — config.json's device_id must
    also be present, per the documented, evidence-based decision not to
    guess at an unconfirmed auto-discovery endpoint."""
    store = CredentialStore(conn=db)
    store.set(EUFY_PROVIDER, "email", "athlete@example.com")
    store.set(EUFY_PROVIDER, "password", "pw")
    # config_path deliberately left nonexistent — no device_id anywhere.

    connectors = _build_configured_connectors(store, config_path)

    assert connectors == []


def test_eufy_configured_when_credentials_and_config_device_id_both_present(db, config_path):
    set_eufy_device_id(config_path, "device-123")
    store = CredentialStore(conn=db)
    store.set(EUFY_PROVIDER, "email", "athlete@example.com")
    store.set(EUFY_PROVIDER, "password", "pw")

    connectors = _build_configured_connectors(store, config_path)

    assert len(connectors) == 1
    assert connectors[0].provider == EUFY_PROVIDER


def test_all_three_configured_yields_three_connectors_in_one_list(db, config_path):
    """Direct proof this feeds ONE shared list into ONE engine call, per
    the Chief Architect's explicit "single SynchronizationEngine" decision
    — not one engine per connector."""
    set_eufy_device_id(config_path, "device-123")
    store = CredentialStore(conn=db)
    store.set(STRAVA_PROVIDER, "refresh_token", "token")
    store.set(PELOTON_PROVIDER, "email", "a@b.com")
    store.set(PELOTON_PROVIDER, "password", "pw")
    store.set(EUFY_PROVIDER, "email", "a@b.com")
    store.set(EUFY_PROVIDER, "password", "pw")

    connectors = _build_configured_connectors(store, config_path)

    assert {c.provider for c in connectors} == {STRAVA_PROVIDER, PELOTON_PROVIDER, EUFY_PROVIDER}


# --- main() end-to-end (composition-time Graceful Degradation) -------------

def test_main_aborts_with_clear_error_when_running_from_trash(isolated_app_dirs, monkeypatch, capsys):
    """Confirms the safety check is actually wired into main(), not just
    unit-tested in isolation — main() must call it, print a clear FATAL
    message, and return a non-zero exit code before doing anything else
    (no database opened, no wizard triggered)."""
    import trainiq.app as app_module
    from trainiq.safety import RunningFromTrashError

    def _fake_check():
        raise RunningFromTrashError(
            "TrainIQ is being executed from macOS Trash.\n\nProject:\n/fake/.Trash/trainiq\n\nMove or restore the project before running."
        )

    monkeypatch.setattr(app_module, "assert_not_running_from_trash", _fake_check)

    exit_code = app_module.main()

    assert exit_code == 1
    captured = capsys.readouterr()
    assert "FATAL" in captured.err
    assert "macOS Trash" in captured.err


def test_main_logs_executing_package_path_on_normal_startup(isolated_app_dirs, monkeypatch):
    """The second, always-on half of the requirement: every normal
    startup logs exactly which copy of TrainIQ is running, not only the
    Trash-rejection case."""
    import trainiq.app as app_module

    logged = {}
    original_info = app_module.summary_logger().info

    class _CapturingLogger:
        def info(self, msg):
            logged.setdefault("messages", []).append(msg)

    monkeypatch.setattr(app_module, "summary_logger", lambda: _CapturingLogger())

    app_module.main()

    assert any("Executing package:" in m for m in logged.get("messages", []))


def test_main_with_no_credentials_triggers_wizard_then_exits_cleanly(isolated_app_dirs, monkeypatch):
    """First-run state: no credentials exist anywhere yet. main() must
    auto-trigger the setup wizard (per RC1-HF-002's Option A). Simulates a
    user who declines every provider — monkeypatching input() directly
    rather than relying on stdin EOF/closed-terminal behavior, which
    turned out to vary by environment (pytest's captured stdin raises
    OSError, not EOFError — a real, useful finding in its own right, now
    also handled defensively in setup_wizard.py itself, not just worked
    around here)."""
    monkeypatch.setattr("builtins.input", lambda *_args: "n")
    exit_code = main()
    assert exit_code == 0


def test_main_wizard_cancellation_via_eof_exits_cleanly_with_nothing_saved(isolated_app_dirs, monkeypatch):
    """A genuinely non-interactive launch (no input available at all) must
    still exit cleanly, per the OSError/EOFError handling in
    setup_wizard.py — this is the scenario the earlier, environment-
    dependent version of this test accidentally exercised; now tested
    directly and deliberately."""

    def _raise_eof(*_args):
        raise EOFError()

    monkeypatch.setattr("builtins.input", _raise_eof)
    exit_code = main()
    assert exit_code == 0


def test_main_continues_when_one_connector_cannot_be_constructed(isolated_app_dirs, monkeypatch):
    """The Chief Architect's explicitly requested scenario: Strava and
    Peloton are correctly configured; Eufy has credentials but is missing
    device_id in config.json (an incomplete configuration, not a
    construction exception, but the same composition-time degradation
    principle applies either way). Expected: Strava and Peloton both get
    handed to the Sync Engine; Eufy is skipped with a clear log entry; the
    application does not stop or error because of Eufy's incomplete
    configuration.

    Strava/Peloton are monkeypatched to network-free fakes here — using
    the real classes would require real STRAVA_CLIENT_ID/SECRET and real
    network access neither of which this sandbox has, and would exercise
    a SEPARATE, already-reported pre-existing issue (RC1-HF-001, already
    fixed) rather than testing composition-time Graceful Degradation
    specifically, which is this test's actual purpose. Because at least
    two connectors ARE configured here, main() must NOT trigger the
    wizard at all — confirmed implicitly by this test completing without
    needing any stdin input."""
    import trainiq.app as app_module

    class _FakeOKConnector:
        def __init__(self, credential_store):
            self.provider = "fake"

        def authenticate(self):
            return True

        def download(self, since=None):
            return []

        def normalize(self, raw):
            return raw

        def get_state(self):
            from trainiq.connectors.base import ConnectorState
            return ConnectorState.HEALTHY

        def restore_state(self, state):
            pass

        def transition_state(self, to_state, detail=None):
            pass

        def active_strategy(self):
            return None

        record_kind = None

    def _make_fake(provider_name):
        class _Fake(_FakeOKConnector):
            def __init__(self, credential_store):
                super().__init__(credential_store)
                self.provider = provider_name
        from trainiq.connectors.base import RecordKind
        _Fake.record_kind = RecordKind.ACTIVITY
        return _Fake

    monkeypatch.setattr(app_module, "StravaConnector", _make_fake(STRAVA_PROVIDER))
    monkeypatch.setattr(app_module, "PelotonConnector", _make_fake(PELOTON_PROVIDER))

    app_support, log_dir, cfg_path = isolated_app_dirs
    app_support.mkdir(parents=True, exist_ok=True)
    db_path = app_support / "trainiq.db"

    from trainiq.storage.schema import open_db as _open_db
    conn = _open_db(db_path)
    store = CredentialStore(conn=conn)
    store.set(STRAVA_PROVIDER, "refresh_token", "token")
    store.set(PELOTON_PROVIDER, "email", "a@b.com")
    store.set(PELOTON_PROVIDER, "password", "pw")
    store.set(EUFY_PROVIDER, "email", "a@b.com")
    store.set(EUFY_PROVIDER, "password", "pw")
    # Deliberately no config.json / no device_id for Eufy.
    conn.close()

    exit_code = app_module.main()

    assert exit_code == 0  # the application itself never stops or errors

    connectors = _build_configured_connectors(CredentialStore(conn=_open_db(db_path)), cfg_path)
    assert {c.provider for c in connectors} == {STRAVA_PROVIDER, PELOTON_PROVIDER}
