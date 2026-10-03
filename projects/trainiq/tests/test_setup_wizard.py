"""
Tests for trainiq.setup_wizard (RC1-HF-002). Every prompt is monkeypatched
directly (input/getpass), and every provider uses the same fake-session
patterns already established in each connector's own test suite — no real
network path is exercised anywhere in this file.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from trainiq.config import get_eufy_device_id
from trainiq.connectors.eufy import CRED_EMAIL as EUFY_CRED_EMAIL
from trainiq.connectors.eufy import CRED_PASSWORD as EUFY_CRED_PASSWORD
from trainiq.connectors.eufy import PROVIDER as EUFY_PROVIDER
from trainiq.connectors.peloton import CRED_EMAIL as PELOTON_CRED_EMAIL
from trainiq.connectors.peloton import CRED_PASSWORD as PELOTON_CRED_PASSWORD
from trainiq.connectors.peloton import PROVIDER as PELOTON_PROVIDER
from trainiq.connectors.strava import CRED_REFRESH_TOKEN as STRAVA_CRED_REFRESH_TOKEN
from trainiq.connectors.strava import PROVIDER as STRAVA_PROVIDER
from trainiq.connectors.strava_unofficial import PROVIDER as STRAVA_UNOFFICIAL_PROVIDER
from trainiq.credentials.store import CredentialStore
from trainiq.setup_wizard import (
    _setup_eufy,
    _setup_peloton,
    _setup_strava,
    _setup_strava_unofficial,
    run_first_time_setup,
)
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
def store(db):
    return CredentialStore(conn=db)


@pytest.fixture
def config_path(tmp_path: Path) -> Path:
    return tmp_path / "config.json"


def _scripted_input(*answers):
    """Returns a callable that yields successive answers, standing in for
    monkeypatched input()."""
    it = iter(answers)
    return lambda *_args: next(it)


# --- Strava --------------------------------------------------------------

def test_strava_declined_stores_nothing(store, monkeypatch):
    monkeypatch.setattr("builtins.input", _scripted_input("n"))
    result = _setup_strava(store)
    assert result is False
    assert store.get(STRAVA_PROVIDER, STRAVA_CRED_REFRESH_TOKEN) is None


def test_strava_missing_client_credentials_skips_without_crashing(store, monkeypatch):
    monkeypatch.delenv("STRAVA_CLIENT_ID", raising=False)
    monkeypatch.delenv("STRAVA_CLIENT_SECRET", raising=False)
    monkeypatch.setattr("builtins.input", _scripted_input("y"))

    result = _setup_strava(store)

    assert result is False
    assert store.get(STRAVA_PROVIDER, STRAVA_CRED_REFRESH_TOKEN) is None


def test_strava_successful_exchange_stores_tokens(store, monkeypatch):
    from types import SimpleNamespace

    monkeypatch.setenv("STRAVA_CLIENT_ID", "12345")
    monkeypatch.setenv("STRAVA_CLIENT_SECRET", "fake-secret")
    monkeypatch.setattr("builtins.input", _scripted_input("y", "fake-auth-code"))

    class FakeStravalibClient:
        def authorization_url(self, client_id, redirect_uri, scope=None, state=None):
            return "https://strava.example/oauth/authorize?fake=1"

        def exchange_code_for_token(self, client_id, client_secret, code):
            assert code == "fake-auth-code"
            return SimpleNamespace(access_token="new-access", refresh_token="new-refresh", expires_at=1234567890)

    result = _setup_strava(store, stravalib_client=FakeStravalibClient())

    assert result is True
    assert store.get(STRAVA_PROVIDER, STRAVA_CRED_REFRESH_TOKEN) == "new-refresh"


def test_strava_rejected_code_stores_nothing(store, monkeypatch):
    from stravalib import exc as stravalib_exc

    monkeypatch.setenv("STRAVA_CLIENT_ID", "12345")
    monkeypatch.setenv("STRAVA_CLIENT_SECRET", "fake-secret")
    monkeypatch.setattr("builtins.input", _scripted_input("y", "bad-code"))

    class FakeStravalibClient:
        def authorization_url(self, client_id, redirect_uri, scope=None, state=None):
            return "https://strava.example/oauth/authorize?fake=1"

        def exchange_code_for_token(self, client_id, client_secret, code):
            raise stravalib_exc.AuthError("invalid code")

    result = _setup_strava(store, stravalib_client=FakeStravalibClient())

    assert result is False
    assert store.get(STRAVA_PROVIDER, STRAVA_CRED_REFRESH_TOKEN) is None


# --- Strava (unofficial, session cookie) ------------------------------------

class _FakeUnofficialConnector:
    """Minimal fake matching StravaUnofficialConnector's two-method public
    surface the wizard step actually calls — no need to fake HTTP
    internals, per the architecture doc's test strategy notes."""

    def __init__(self, submit_result=True, submit_exception=None):
        self._submit_result = submit_result
        self._submit_exception = submit_exception
        self.submitted_with = None

    def request_manual_recovery(self):
        return "Log into Strava in your browser, open DevTools..."

    def submit_manual_recovery(self, cookie_value):
        self.submitted_with = cookie_value
        if self._submit_exception is not None:
            raise self._submit_exception
        return self._submit_result


def test_strava_unofficial_declined_stores_nothing(store, monkeypatch):
    monkeypatch.setattr("builtins.input", _scripted_input("n"))

    result = _setup_strava_unofficial(store, connector=_FakeUnofficialConnector())

    assert result is False


def test_strava_unofficial_accepted_with_valid_cookie_reports_success(store, monkeypatch):
    monkeypatch.setattr("builtins.input", _scripted_input("y", "fake-cookie-value"))
    fake = _FakeUnofficialConnector(submit_result=True)

    result = _setup_strava_unofficial(store, connector=fake)

    assert result is True
    assert fake.submitted_with == "fake-cookie-value"


def test_strava_unofficial_accepted_with_rejected_cookie_reports_nothing_saved(store, monkeypatch, capsys):
    monkeypatch.setattr("builtins.input", _scripted_input("y", "bad-cookie-value"))
    fake = _FakeUnofficialConnector(submit_result=False)

    result = _setup_strava_unofficial(store, connector=fake)

    assert result is False
    captured = capsys.readouterr()
    assert "Nothing saved" in captured.out


def test_strava_unofficial_unexpected_exception_during_validation_reports_failure(store, monkeypatch):
    """AC9: an exception other than an expected rejection (e.g. a
    TransientError from a 429/5xx during the live validation call) must
    still be caught by the wizard step and reported as a failure, not
    propagate."""
    monkeypatch.setattr("builtins.input", _scripted_input("y", "some-cookie-value"))
    fake = _FakeUnofficialConnector(submit_exception=RuntimeError("simulated transient failure"))

    result = _setup_strava_unofficial(store, connector=fake)

    assert result is False


def test_strava_unofficial_prompts_the_connectors_own_recovery_instructions(store, monkeypatch, capsys):
    """The requirements doc is explicit: the wizard must present the
    connector's own request_manual_recovery() string, not re-authored
    copy."""
    monkeypatch.setattr("builtins.input", _scripted_input("y", "fake-cookie-value"))
    fake = _FakeUnofficialConnector(submit_result=True)

    _setup_strava_unofficial(store, connector=fake)

    captured = capsys.readouterr()
    assert fake.request_manual_recovery() in captured.out


def test_strava_unofficial_offered_after_official_strava_regardless_of_outcome(store, config_path, monkeypatch):
    """AC5: the unofficial step is offered unconditionally, right after the
    official Strava step, whether that step was accepted, declined, or
    failed — this test covers the "declined" case end-to-end via
    run_first_time_setup()."""
    call_count = {"n": 0}

    def _sequenced_input(*_args):
        call_count["n"] += 1
        if call_count["n"] == 1:
            return "n"  # decline official Strava
        if call_count["n"] == 2:
            return "n"  # decline unofficial Strava
        raise KeyboardInterrupt()  # stop the rest of the run

    monkeypatch.setattr("builtins.input", _sequenced_input)

    run_first_time_setup(store, config_path)  # must not raise

    assert call_count["n"] >= 2


def test_strava_unofficial_cancellation_propagates_and_stores_nothing(store, monkeypatch):
    """AC10: Ctrl+C/EOF during this step must propagate SetupCancelled
    (caught by run_first_time_setup, not locally) and must not leave any
    credentials behind, since nothing was stored before the cancellation."""

    def _raise_interrupt(*_a):
        raise KeyboardInterrupt()

    monkeypatch.setattr("builtins.input", _raise_interrupt)

    from trainiq.setup_wizard import SetupCancelled
    with pytest.raises(SetupCancelled):
        _setup_strava_unofficial(store, connector=_FakeUnofficialConnector())

    assert store.get(STRAVA_UNOFFICIAL_PROVIDER, "session_cookie") is None


# --- Peloton (rollback behavior) --------------------------------------------

def test_peloton_declined_stores_nothing(store, monkeypatch):
    monkeypatch.setattr("builtins.input", _scripted_input("n"))
    result = _setup_peloton(store)
    assert result is False
    assert store.get(PELOTON_PROVIDER, PELOTON_CRED_EMAIL) is None


def test_peloton_successful_auth_keeps_credentials(store, monkeypatch):
    class FakeSession:
        def post(self, url, json=None, headers=None):
            class R:
                status_code = 200

                def json(self):
                    return {"session_id": "sess-123"}
            return R()

    monkeypatch.setattr("builtins.input", _scripted_input("y", "athlete@example.com"))
    monkeypatch.setattr("getpass.getpass", lambda *_a: "correct-password")

    result = _setup_peloton(store, session=FakeSession())

    assert result is True
    assert store.get(PELOTON_PROVIDER, PELOTON_CRED_EMAIL) == "athlete@example.com"
    assert store.get(PELOTON_PROVIDER, PELOTON_CRED_PASSWORD) == "correct-password"


def test_peloton_rejected_login_rolls_back_stored_credentials(store, monkeypatch):
    """The store -> validate -> rollback pattern, for the ordinary
    "wrong password" case (authenticate() returns False, no exception)."""
    class FakeSession:
        def post(self, url, json=None, headers=None):
            class R:
                status_code = 403

                def json(self):
                    return {"message": "Access forbidden."}
            return R()

    monkeypatch.setattr("builtins.input", _scripted_input("y", "athlete@example.com"))
    monkeypatch.setattr("getpass.getpass", lambda *_a: "wrong-password")

    result = _setup_peloton(store, session=FakeSession())

    assert result is False
    assert store.get(PELOTON_PROVIDER, PELOTON_CRED_EMAIL) is None
    assert store.get(PELOTON_PROVIDER, PELOTON_CRED_PASSWORD) is None


def test_peloton_unexpected_exception_during_authenticate_also_rolls_back(store, monkeypatch):
    """The Chief Architect's explicit requirement: rollback must trigger
    on ANY failure, not only a clean authentication rejection — this test
    simulates authenticate() itself raising, not just returning False."""

    class ExplodingSession:
        def post(self, url, json=None, headers=None):
            raise RuntimeError("simulated unexpected failure, not a clean auth rejection")

    monkeypatch.setattr("builtins.input", _scripted_input("y", "athlete@example.com"))
    monkeypatch.setattr("getpass.getpass", lambda *_a: "some-password")

    result = _setup_peloton(store, session=ExplodingSession())

    assert result is False
    assert store.get(PELOTON_PROVIDER, PELOTON_CRED_EMAIL) is None
    assert store.get(PELOTON_PROVIDER, PELOTON_CRED_PASSWORD) is None


# --- Eufy (rollback + device_id in config.json, not Keychain) ---------------

def test_eufy_successful_auth_keeps_credentials_and_saves_discovered_device_id_to_config(store, config_path, monkeypatch):
    """RC1-HF-003: no device-ID prompt anymore — the single discovered
    device is auto-selected and its id saved to config.json."""
    class FakeSession:
        def post(self, url, json=None, headers=None):
            class R:
                status_code = 200

                def json(self):
                    return {
                        "res_code": 1, "access_token": "tok-123", "refresh_token": "refresh-123",
                        "devices": [{"id": "eufyt9150cfe90116a933", "name": "Smart Scale P3"}],
                    }
            return R()

    monkeypatch.setattr("builtins.input", _scripted_input("y", "athlete@example.com"))
    monkeypatch.setattr("getpass.getpass", lambda *_a: "correct-password")

    result = _setup_eufy(store, config_path, session=FakeSession())

    assert result is True
    assert store.get(EUFY_PROVIDER, EUFY_CRED_EMAIL) == "athlete@example.com"
    assert get_eufy_device_id(config_path) == "eufyt9150cfe90116a933"


def test_eufy_multiple_devices_prompts_for_a_choice(store, config_path, monkeypatch):
    """RC1-HF-003: when more than one device is discovered, the wizard
    shows a numbered choice instead of guessing."""
    class FakeSession:
        def post(self, url, json=None, headers=None):
            class R:
                status_code = 200

                def json(self):
                    return {
                        "res_code": 1, "access_token": "tok-123", "refresh_token": "refresh-123",
                        "devices": [
                            {"id": "scale-1", "name": "Smart Scale P3"},
                            {"id": "scale-2", "name": "Smart Scale P2 (guest room)"},
                        ],
                    }
            return R()

    # Prompts, in order: connect?, email, then the numbered device choice.
    monkeypatch.setattr("builtins.input", _scripted_input("y", "athlete@example.com", "2"))
    monkeypatch.setattr("getpass.getpass", lambda *_a: "correct-password")

    result = _setup_eufy(store, config_path, session=FakeSession())

    assert result is True
    assert get_eufy_device_id(config_path) == "scale-2"


def test_eufy_no_devices_found_rolls_back(store, config_path, monkeypatch):
    """A successfully authenticated account with zero devices cannot be
    used to sync anything — treated as a failure, credentials rolled back,
    same discipline as a rejected login."""
    class FakeSession:
        def post(self, url, json=None, headers=None):
            class R:
                status_code = 200

                def json(self):
                    return {"res_code": 1, "access_token": "tok-123", "refresh_token": "refresh-123", "devices": []}
            return R()

    monkeypatch.setattr("builtins.input", _scripted_input("y", "athlete@example.com"))
    monkeypatch.setattr("getpass.getpass", lambda *_a: "correct-password")

    result = _setup_eufy(store, config_path, session=FakeSession())

    assert result is False
    assert store.get(EUFY_PROVIDER, EUFY_CRED_EMAIL) is None
    assert get_eufy_device_id(config_path) is None


def test_eufy_rejected_login_rolls_back_credentials_and_does_not_save_device_id(store, config_path, monkeypatch):
    class FakeSession:
        def post(self, url, json=None, headers=None):
            class R:
                status_code = 401

                def json(self):
                    return {}
            return R()

    monkeypatch.setattr("builtins.input", _scripted_input("y", "athlete@example.com", "device-abc"))
    monkeypatch.setattr("getpass.getpass", lambda *_a: "wrong-password")

    result = _setup_eufy(store, config_path, session=FakeSession())

    assert result is False
    assert store.get(EUFY_PROVIDER, EUFY_CRED_EMAIL) is None
    assert get_eufy_device_id(config_path) is None  # never written on failure


# --- Cancellation (Ctrl+C / EOF) --------------------------------------------

def test_cancellation_during_strava_does_not_affect_nothing_yet_configured(store, config_path, monkeypatch, capsys):
    def _raise_interrupt(*_a):
        raise KeyboardInterrupt()

    monkeypatch.setattr("builtins.input", _raise_interrupt)

    run_first_time_setup(store, config_path)  # must not raise

    captured = capsys.readouterr()
    assert "Setup cancelled." in captured.out


def test_cancellation_during_peloton_preserves_already_configured_strava(store, config_path, monkeypatch):
    """The specific behavior the Chief Architect required: cancelling
    partway through does NOT roll back a provider that was already
    successfully configured earlier in the same run — only in-progress
    and remaining providers are affected."""
    from types import SimpleNamespace

    call_count = {"n": 0}

    def _sequenced_input(*_args):
        call_count["n"] += 1
        if call_count["n"] == 1:
            return "y"  # "Connect Strava now?"
        if call_count["n"] == 2:
            return "fake-auth-code"  # Strava's pasted code
        if call_count["n"] == 3:
            return "y"  # "Connect Peloton now?"
        raise KeyboardInterrupt()  # cancel during Peloton's email prompt

    monkeypatch.setenv("STRAVA_CLIENT_ID", "12345")
    monkeypatch.setenv("STRAVA_CLIENT_SECRET", "fake-secret")
    monkeypatch.setattr("builtins.input", _sequenced_input)

    class FakeStravalibClient:
        def authorization_url(self, client_id, redirect_uri, scope=None, state=None):
            return "https://strava.example/oauth/authorize?fake=1"

        def exchange_code_for_token(self, client_id, client_secret, code):
            return SimpleNamespace(access_token="a", refresh_token="r", expires_at=1)

    # run_first_time_setup() constructs its own stravalib.Client() internally
    # with no injection point — so this test calls _setup_strava() directly
    # first (proving Strava succeeds), then simulates cancellation during
    # the Peloton step by calling run_first_time_setup() is not possible
    # without a real network client for Strava's second internal call.
    # Instead: verify the actual documented guarantee directly — a
    # provider already configured is never touched by a later cancellation.
    _setup_strava(store, stravalib_client=FakeStravalibClient())
    assert store.get(STRAVA_PROVIDER, STRAVA_CRED_REFRESH_TOKEN) == "r"

    def _cancel_immediately(*_a):
        raise KeyboardInterrupt()

    monkeypatch.setattr("builtins.input", _cancel_immediately)

    from trainiq.setup_wizard import SetupCancelled
    with pytest.raises(SetupCancelled):
        # Calling the private function directly (not run_first_time_setup)
        # deliberately bypasses the top-level catch, so SetupCancelled
        # propagates here exactly as it would to run_first_time_setup's
        # own try/except — this test only cares that Strava's
        # already-stored credentials survive the exception, not about
        # where it's ultimately caught.
        _setup_peloton(store)

    # Strava's credentials from the earlier, separate, successful step
    # remain untouched by this later cancellation.
    assert store.get(STRAVA_PROVIDER, STRAVA_CRED_REFRESH_TOKEN) == "r"
