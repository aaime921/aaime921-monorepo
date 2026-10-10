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

from trainiq.app import _build_configured_connectors, _parse_args, _run_strava_streams_enrichment, main
from trainiq.config import set_eufy_device_id
from trainiq.connectors.eufy import PROVIDER as EUFY_PROVIDER
from trainiq.connectors.peloton import PROVIDER as PELOTON_PROVIDER
from trainiq.connectors.strava import PROVIDER as STRAVA_PROVIDER
from trainiq.connectors.strava_unofficial import CRED_STRAVA_SESSION_COOKIE as STRAVA_UNOFFICIAL_CRED_SESSION_COOKIE
from trainiq.connectors.strava_unofficial import CRED_STRAVA_SESSION_EXPIRES_AT
from trainiq.connectors.strava_unofficial import PROVIDER as STRAVA_UNOFFICIAL_PROVIDER
from trainiq.connectors.strava_unofficial import StravaUnofficialConnector
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


def test_strava_unofficial_configured_when_session_cookie_present(db, config_path):
    store = CredentialStore(conn=db)
    store.set(STRAVA_UNOFFICIAL_PROVIDER, STRAVA_UNOFFICIAL_CRED_SESSION_COOKIE, "some-session-cookie")

    connectors = _build_configured_connectors(store, config_path)

    assert len(connectors) == 1
    assert connectors[0].provider == STRAVA_UNOFFICIAL_PROVIDER


def test_strava_unofficial_skipped_when_no_session_cookie_stored(db, config_path):
    store = CredentialStore(conn=db)

    connectors = _build_configured_connectors(store, config_path)

    assert connectors == []


def test_strava_unofficial_construction_failure_is_caught_and_skipped(db, config_path, monkeypatch):
    """Graceful Degradation: a construction failure for this connector must
    not raise and must not prevent other configured connectors from being
    returned."""
    import trainiq.app as app_module

    store = CredentialStore(conn=db)
    store.set(STRAVA_UNOFFICIAL_PROVIDER, STRAVA_UNOFFICIAL_CRED_SESSION_COOKIE, "some-session-cookie")
    store.set(STRAVA_PROVIDER, "refresh_token", "token")

    def _exploding_constructor(credential_store):
        raise RuntimeError("simulated construction failure")

    monkeypatch.setattr(app_module, "StravaUnofficialConnector", _exploding_constructor)

    connectors = _build_configured_connectors(store, config_path)

    assert {c.provider for c in connectors} == {STRAVA_PROVIDER}


def test_strava_unofficial_coexists_with_official_strava(db, config_path):
    store = CredentialStore(conn=db)
    store.set(STRAVA_PROVIDER, "refresh_token", "token")
    store.set(STRAVA_UNOFFICIAL_PROVIDER, STRAVA_UNOFFICIAL_CRED_SESSION_COOKIE, "some-session-cookie")

    connectors = _build_configured_connectors(store, config_path)

    assert {c.provider for c in connectors} == {STRAVA_PROVIDER, STRAVA_UNOFFICIAL_PROVIDER}


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

    exit_code = app_module.main(argv=[])

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

    app_module.main(argv=[])

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
    exit_code = main(argv=[])
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
    exit_code = main(argv=[])
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

    exit_code = app_module.main(argv=[])

    assert exit_code == 0  # the application itself never stops or errors

    connectors = _build_configured_connectors(CredentialStore(conn=_open_db(db_path)), cfg_path)
    assert {c.provider for c in connectors} == {STRAVA_PROVIDER, PELOTON_PROVIDER}


# --- _Reporter ---------------------------------------------------------------

def test_reporter_logs_and_collects_lines_in_order():
    import trainiq.app as app_module

    logged = []

    class _CapturingLogger:
        def info(self, msg):
            logged.append(("info", msg))

        def warning(self, msg):
            logged.append(("warning", msg))

    report = app_module._Reporter(_CapturingLogger())
    report.info("first")
    report.warning("second")

    assert logged == [("info", "first"), ("warning", "second")]
    assert report.lines == ["first", "second"]


# --- Console feedback on every run (AC2/AC3) --------------------------------

class _FakeOKConnectorForSync:
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
        from trainiq.connectors.base import ConnectorState
        return ConnectorState.HEALTHY

    def restore_state(self, state):
        pass

    def transition_state(self, to_state, detail=None):
        pass

    def active_strategy(self):
        return None

    record_kind = None


def _make_fake_sync_connector(provider_name):
    class _Fake(_FakeOKConnectorForSync):
        def __init__(self, credential_store):
            super().__init__(credential_store)
            self.provider = provider_name
    from trainiq.connectors.base import RecordKind
    _Fake.record_kind = RecordKind.ACTIVITY
    return _Fake


def test_main_prints_console_summary_of_connector_status_and_sync_results(isolated_app_dirs, monkeypatch, capsys):
    """AC2/AC3: a normal run (not first-time setup, at least one connector
    already configured) must print, to stdout, per-connector configured/
    skipped status and sync results, plus where the full logs live — this
    information previously only ever reached summary.log/diagnostic.log."""
    import trainiq.app as app_module

    monkeypatch.setattr(app_module, "StravaConnector", _make_fake_sync_connector(STRAVA_PROVIDER))

    app_support, log_dir, cfg_path = isolated_app_dirs
    app_support.mkdir(parents=True, exist_ok=True)
    db_path = app_support / "trainiq.db"

    from trainiq.storage.schema import open_db as _open_db
    conn = _open_db(db_path)
    store = CredentialStore(conn=conn)
    store.set(STRAVA_PROVIDER, "refresh_token", "token")
    conn.close()

    exit_code = app_module.main(argv=[])

    assert exit_code == 0
    captured = capsys.readouterr()
    assert "Strava: configured" in captured.out
    assert "Peloton: skipped (not connected)" in captured.out
    assert "strava: downloaded 0, inserted 0, updated 0, malformed 0, skipped 0" in captured.out
    assert str(log_dir / "summary.log") in captured.out
    assert str(log_dir / "diagnostic.log") in captured.out


# --- --configure flag --------------------------------------------------------

def test_main_console_summary_includes_flagged_count(isolated_app_dirs, monkeypatch, capsys):
    """AC2/AC4 (issue #44): the console summary line must include
    `flagged N implausible`, matching summary.log exactly, instead of the
    flagged-less line main() used to rebuild on its own. Reuses the
    WEIGH_IN + outlier-reading fixtures from test_sync_engine.py so
    build_canonical_record() actually flags a record, the same way
    test_weigh_in_sync_summary_reports_flagged_count does for summary.log."""
    import trainiq.app as app_module
    from trainiq.connectors.base import RecordKind
    from tests.test_sync_engine import _OUTLIER_READING, _baseline_weigh_ins

    class _FakeWeighInConnector:
        supports_incremental_sync = True
        record_kind = RecordKind.WEIGH_IN

        def __init__(self, credential_store):
            self.provider = "testscale"

        def authenticate(self):
            return True

        def download(self, since=None):
            return _baseline_weigh_ins(5) + [dict(_OUTLIER_READING)]

        def normalize(self, raw):
            return {
                "external_id": raw["external_id"],
                "timestamp": raw["timestamp"],
                "weight_kg": raw.get("weight_kg"),
                "body_fat_pct": raw.get("body_fat_pct"),
            }

        def extract_resume_cursor(self, normalized):
            value = normalized.get("timestamp")
            return str(value) if value is not None else None

        def get_state(self):
            from trainiq.connectors.base import ConnectorState
            return ConnectorState.HEALTHY

        def restore_state(self, state):
            pass

        def transition_state(self, to_state, detail=None):
            pass

        def active_strategy(self):
            return None

    monkeypatch.setattr(app_module, "StravaConnector", _FakeWeighInConnector)

    app_support, log_dir, cfg_path = isolated_app_dirs
    app_support.mkdir(parents=True, exist_ok=True)
    db_path = app_support / "trainiq.db"

    from trainiq.storage.schema import open_db as _open_db
    conn = _open_db(db_path)
    store = CredentialStore(conn=conn)
    store.set(STRAVA_PROVIDER, "refresh_token", "token")
    conn.close()

    exit_code = app_module.main(argv=[])

    assert exit_code == 0
    captured = capsys.readouterr()
    assert "flagged 1 implausible" in captured.out
    summary_log_content = (log_dir / "summary.log").read_text()
    assert "flagged 1 implausible" in summary_log_content


def test_main_configure_flag_with_zero_connectors_runs_configure_not_first_time_wizard(isolated_app_dirs, monkeypatch):
    """--configure must drive run_configure(), never run_first_time_setup(),
    even when zero connectors are configured — proven by making
    run_first_time_setup raise if it's ever called."""
    import trainiq.app as app_module

    def _fail_if_called(*_args, **_kwargs):
        raise AssertionError("run_first_time_setup must not be called when --configure is passed")

    monkeypatch.setattr(app_module, "run_first_time_setup", _fail_if_called)
    monkeypatch.setattr("builtins.input", lambda *_args: "n")

    exit_code = app_module.main(argv=["--configure"])

    assert exit_code == 0


def test_main_configure_flag_with_one_connector_shows_header_and_preserves_declined_credentials(
    isolated_app_dirs, monkeypatch, capsys
):
    """--configure with one connector already configured (Strava) must (a)
    show it in the "Current connectors" header, (b) leave its credentials
    untouched when the user declines reconfiguring it, and (c) not also
    trigger run_first_time_setup()."""
    import trainiq.app as app_module

    app_support, log_dir, cfg_path = isolated_app_dirs
    app_support.mkdir(parents=True, exist_ok=True)
    db_path = app_support / "trainiq.db"

    from trainiq.storage.schema import open_db as _open_db
    conn = _open_db(db_path)
    store = CredentialStore(conn=conn)
    store.set(STRAVA_PROVIDER, "refresh_token", "existing-token")
    conn.close()

    def _fail_if_called(*_args, **_kwargs):
        raise AssertionError("run_first_time_setup must not be called when --configure is passed")

    monkeypatch.setattr(app_module, "run_first_time_setup", _fail_if_called)
    monkeypatch.setattr("builtins.input", lambda *_args: "n")

    exit_code = app_module.main(argv=["--configure"])

    assert exit_code == 0
    captured = capsys.readouterr()
    assert "Strava: configured" in captured.out

    store = CredentialStore(conn=_open_db(db_path))
    assert store.get(STRAVA_PROVIDER, "refresh_token") == "existing-token"


# --- _run_strava_streams_enrichment() post-sync hook (issue #50) -----------

class _FakeReporter:
    def __init__(self):
        self.lines: list[str] = []

    def info(self, msg):
        self.lines.append(msg)

    def warning(self, msg):
        self.lines.append(msg)


class _FakeStreamsSession:
    def __init__(self, response_body: dict):
        self._body = response_body
        self.get_calls: list[dict] = []

    def get(self, url, params=None, headers=None):
        self.get_calls.append({"url": url, "params": params, "headers": headers})

        class _Resp:
            status_code = 200

            def json(_self):
                return self._body

        return _Resp()


def _configured_strava_unofficial(db, session) -> StravaUnofficialConnector:
    import time as _time
    from datetime import datetime, timedelta, timezone

    store = CredentialStore(conn=db)
    store.set(STRAVA_UNOFFICIAL_PROVIDER, STRAVA_UNOFFICIAL_CRED_SESSION_COOKIE, "cookie")
    store.set(
        STRAVA_UNOFFICIAL_PROVIDER, CRED_STRAVA_SESSION_EXPIRES_AT,
        str(int((datetime.now(timezone.utc) + timedelta(days=7)).timestamp())),
    )
    return StravaUnofficialConnector(store, session=session)


def test_no_strava_unofficial_connector_is_a_no_op(db):
    reporter = _FakeReporter()

    _run_strava_streams_enrichment(db, connectors=[], reporter=reporter)

    assert reporter.lines == []


def test_enriches_eligible_strava_unofficial_activity_and_reports_summary(db):
    db.execute(
        """
        INSERT INTO normalized_activities
            (provider, external_id, start_time, duration_s, discipline, distance_m,
             avg_hr, max_hr, avg_power, max_power, calories,
             training_load, training_load_method, source_confidence)
        VALUES (?, 's1', '2026-01-05T07:00:00+00:00', 1800, 'running', NULL,
                NULL, NULL, NULL, NULL, NULL, NULL, 'unknown', 1.0)
        """,
        (STRAVA_UNOFFICIAL_PROVIDER,),
    )
    db.execute(
        "INSERT INTO raw_activities (provider, external_id, payload_json, fetched_at) "
        "VALUES (?, 's1', '{}', '2026-01-05T07:00:00+00:00')",
        (STRAVA_UNOFFICIAL_PROVIDER,),
    )
    db.commit()
    session = _FakeStreamsSession({"heartrate": [100, 110], "time": [0, 10]})
    connector = _configured_strava_unofficial(db, session)
    reporter = _FakeReporter()

    _run_strava_streams_enrichment(db, connectors=[connector], reporter=reporter)

    assert len(session.get_calls) == 1
    row = dict(db.execute(
        "SELECT streams_fetch_status FROM normalized_activities WHERE provider = ? AND external_id = 's1'",
        (STRAVA_UNOFFICIAL_PROVIDER,),
    ).fetchone())
    assert row["streams_fetch_status"] == "ok"
    assert any("streams: processed 1" in line for line in reporter.lines)


def test_auth_failure_during_enrichment_is_reported_not_raised(db):
    """Graceful degradation (ADR-009): a 401 mid-enrichment must not crash
    main()'s post-sync step — reported via the reporter, swallowed here."""
    db.execute(
        """
        INSERT INTO normalized_activities
            (provider, external_id, start_time, duration_s, discipline, distance_m,
             avg_hr, max_hr, avg_power, max_power, calories,
             training_load, training_load_method, source_confidence)
        VALUES (?, 's1', '2026-01-05T07:00:00+00:00', 1800, 'running', NULL,
                NULL, NULL, NULL, NULL, NULL, NULL, 'unknown', 1.0)
        """,
        (STRAVA_UNOFFICIAL_PROVIDER,),
    )
    db.commit()

    class _401Session:
        def get(self, url, params=None, headers=None):
            class _Resp:
                status_code = 401
                headers = {}

                def json(_self):
                    return {}

            return _Resp()

    connector = _configured_strava_unofficial(db, _401Session())
    reporter = _FakeReporter()

    _run_strava_streams_enrichment(db, connectors=[connector], reporter=reporter)  # must not raise

    assert any("streams enrichment stopped" in line for line in reporter.lines)


# --- `export` subcommand (issue #71) ---------------------------------------

def test_parse_args_bare_and_configure_have_no_command():
    """Adding the `export` subparser must not change bare `trainiq` or
    `trainiq --configure` — both must keep going through the existing
    sync path (`args.command is None`)."""
    assert _parse_args([]).command is None
    assert _parse_args(["--configure"]).command is None
    assert _parse_args(["--configure"]).configure is True


def test_parse_args_export_requires_out():
    import pytest

    with pytest.raises(SystemExit):
        _parse_args(["export"])


def test_parse_args_export_parses_out_and_as_of():
    args = _parse_args(["export", "--out", "/tmp/somewhere", "--as-of", "2026-10-10"])
    assert args.command == "export"
    assert str(args.out) == "/tmp/somewhere"
    assert args.as_of == "2026-10-10"


def test_main_export_command_writes_files_and_does_not_run_sync(isolated_app_dirs, tmp_path):
    """main(["export", ...]) must drive run_export(), not the connector
    sync path at all — no credential store, no SynchronizationEngine."""
    out_dir = tmp_path / "coach_export"

    exit_code = main(["export", "--out", str(out_dir)])

    assert exit_code == 0
    assert (out_dir / "profile.md").exists()
    assert (out_dir / "recent.json").exists()


def test_main_export_command_respects_as_of(isolated_app_dirs, tmp_path):
    out_dir = tmp_path / "coach_export"

    exit_code = main(["export", "--out", str(out_dir), "--as-of", "2026-01-01"])

    assert exit_code == 0
    assert "As of: 2026-01-01" in (out_dir / "profile.md").read_text()


# --- `export --no-classes` (issue #72) --------------------------------------

def test_parse_args_export_no_classes_defaults_false():
    args = _parse_args(["export", "--out", "/tmp/somewhere"])
    assert args.no_classes is False


def test_parse_args_export_parses_no_classes():
    args = _parse_args(["export", "--out", "/tmp/somewhere", "--no-classes"])
    assert args.no_classes is True


def test_main_export_writes_peloton_classes_unavailable_with_no_peloton_credentials(isolated_app_dirs, tmp_path):
    """No Peloton credentials configured (the default, untouched test
    state) -> `_authenticate_class_catalog` returns None without any
    network call -> the file still writes, stating unavailable (AC 7)."""
    out_dir = tmp_path / "coach_export"

    exit_code = main(["export", "--out", str(out_dir)])

    assert exit_code == 0
    assert "class catalog unavailable" in (out_dir / "peloton_classes.md").read_text()


def test_main_export_no_classes_flag_skips_catalog_authentication(isolated_app_dirs, tmp_path, monkeypatch):
    import trainiq.app as app_module

    def _fail_if_called(*args, **kwargs):
        raise AssertionError("--no-classes must skip _authenticate_class_catalog() entirely")

    monkeypatch.setattr(app_module, "_authenticate_class_catalog", _fail_if_called)
    out_dir = tmp_path / "coach_export"

    exit_code = main(["export", "--out", str(out_dir), "--no-classes"])

    assert exit_code == 0
    assert "class catalog unavailable" in (out_dir / "peloton_classes.md").read_text()
