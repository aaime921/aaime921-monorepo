"""
Tests for the Strava session-cookie connector (issue #18).

Every test uses a fake HTTP session — this sandbox cannot reach
api.strava.com (see module docstring in
trainiq/connectors/strava_unofficial.py).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from trainiq.connectors.base import AcquisitionStrategy
from trainiq.connectors.strava_unofficial import (
    ASSUMED_SESSION_LIFETIME_S,
    CRED_STRAVA_SESSION_COOKIE,
    CRED_STRAVA_SESSION_EXPIRES_AT,
    CRED_STRAVA_SESSION_OBTAINED_AT,
    DEFAULT_RETRY_AFTER_S,
    PROVIDER,
    StravaUnofficialConnector,
    StravaUnofficialHTTPError,
)
from trainiq.credentials.store import CredentialStore
from trainiq.storage.schema import open_db
from trainiq.sync.engine import AuthenticationError, TransientError


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
def credential_store(db):
    return CredentialStore(conn=db)


class FakeResponse:
    def __init__(self, status_code: int, json_body=None, headers: dict | None = None):
        self.status_code = status_code
        self._json_body = json_body if json_body is not None else {}
        self.headers = headers or {}

    def json(self):
        return self._json_body


class FakeStravaUnofficialSession:
    def __init__(self):
        self.get_calls: list[dict] = []
        self._get_responses: list[FakeResponse] = []

    def script_get_response(self, response: FakeResponse):
        self._get_responses.append(response)

    def get(self, url, params=None, headers=None):
        self.get_calls.append({"url": url, "params": params, "headers": headers})
        return self._get_responses.pop(0)


def _future_expiry(days=7):
    return int((datetime.now(timezone.utc) + timedelta(days=days)).timestamp())


def _past_expiry(days=1):
    return int((datetime.now(timezone.utc) - timedelta(days=days)).timestamp())


# --- authenticate() ----------------------------------------------------------

def test_authenticate_valid_unexpired_cookie_returns_true_with_zero_network_calls(credential_store):
    credential_store.set(PROVIDER, CRED_STRAVA_SESSION_COOKIE, "cookie-value")
    credential_store.set(PROVIDER, CRED_STRAVA_SESSION_EXPIRES_AT, str(_future_expiry()))
    fake = FakeStravaUnofficialSession()
    connector = StravaUnofficialConnector(credential_store, session=fake)

    assert connector.authenticate() is True
    assert fake.get_calls == []


def test_authenticate_no_cookie_stored_returns_false(credential_store):
    fake = FakeStravaUnofficialSession()
    connector = StravaUnofficialConnector(credential_store, session=fake)

    assert connector.authenticate() is False
    assert fake.get_calls == []


def test_authenticate_cookie_past_safety_margin_returns_false_no_network_call(credential_store):
    credential_store.set(PROVIDER, CRED_STRAVA_SESSION_COOKIE, "cookie-value")
    credential_store.set(PROVIDER, CRED_STRAVA_SESSION_EXPIRES_AT, str(_future_expiry(days=0)))
    fake = FakeStravaUnofficialSession()
    connector = StravaUnofficialConnector(credential_store, session=fake)

    assert connector.authenticate() is False
    assert fake.get_calls == []


# --- list_acquisition_strategies() -------------------------------------------

def test_list_acquisition_strategies_reports_unofficial_session(credential_store):
    fake = FakeStravaUnofficialSession()
    connector = StravaUnofficialConnector(credential_store, session=fake)

    assert connector.list_acquisition_strategies() == [AcquisitionStrategy.UNOFFICIAL_SESSION]


# --- submit_manual_recovery() -------------------------------------------------

def test_submit_manual_recovery_valid_cookie_persists_all_three_credentials(credential_store):
    fake = FakeStravaUnofficialSession()
    fake.script_get_response(FakeResponse(200, {"id": 1}))
    connector = StravaUnofficialConnector(credential_store, session=fake)

    assert connector.submit_manual_recovery("new-cookie-value") is True
    assert credential_store.get(PROVIDER, CRED_STRAVA_SESSION_COOKIE) == "new-cookie-value"
    obtained_at = int(credential_store.get(PROVIDER, CRED_STRAVA_SESSION_OBTAINED_AT))
    expires_at = int(credential_store.get(PROVIDER, CRED_STRAVA_SESSION_EXPIRES_AT))
    assert expires_at == obtained_at + ASSUMED_SESSION_LIFETIME_S
    assert fake.get_calls[0]["headers"]["Cookie"] == "_strava4_session=new-cookie-value"


def test_submit_manual_recovery_validates_against_corrected_base_url(credential_store):
    """DEFAULT_BASE_URL was `https://api.strava.com` (no DNS record at all,
    issue #26) and is now `https://www.strava.com` — the one Strava host
    independently confirmed resolvable. Guards against the host portion of
    the constant drifting back to the broken value."""
    fake = FakeStravaUnofficialSession()
    fake.script_get_response(FakeResponse(200, {"id": 1}))
    connector = StravaUnofficialConnector(credential_store, session=fake)

    connector.submit_manual_recovery("new-cookie-value")

    assert fake.get_calls[0]["url"] == "https://www.strava.com/api/v3/athlete"


def test_submit_manual_recovery_rejected_cookie_returns_false_and_persists_nothing(credential_store):
    fake = FakeStravaUnofficialSession()
    fake.script_get_response(FakeResponse(401, {}))
    connector = StravaUnofficialConnector(credential_store, session=fake)

    assert connector.submit_manual_recovery("bad-cookie") is False
    assert credential_store.exists(PROVIDER, CRED_STRAVA_SESSION_COOKIE) is False
    assert credential_store.exists(PROVIDER, CRED_STRAVA_SESSION_OBTAINED_AT) is False
    assert credential_store.exists(PROVIDER, CRED_STRAVA_SESSION_EXPIRES_AT) is False


def test_submit_manual_recovery_rejected_cookie_403_also_returns_false(credential_store):
    fake = FakeStravaUnofficialSession()
    fake.script_get_response(FakeResponse(403, {}))
    connector = StravaUnofficialConnector(credential_store, session=fake)

    assert connector.submit_manual_recovery("bad-cookie") is False


def test_submit_manual_recovery_empty_string_raises_value_error_persists_nothing(credential_store):
    fake = FakeStravaUnofficialSession()
    connector = StravaUnofficialConnector(credential_store, session=fake)

    with pytest.raises(ValueError):
        connector.submit_manual_recovery("")
    assert credential_store.exists(PROVIDER, CRED_STRAVA_SESSION_COOKIE) is False


def test_submit_manual_recovery_transient_error_during_validation_propagates_not_swallowed(credential_store):
    fake = FakeStravaUnofficialSession()
    fake.script_get_response(FakeResponse(429, {}, headers={"Retry-After": "60"}))
    connector = StravaUnofficialConnector(credential_store, session=fake)

    with pytest.raises(TransientError):
        connector.submit_manual_recovery("some-cookie")
    assert credential_store.exists(PROVIDER, CRED_STRAVA_SESSION_COOKIE) is False


def test_request_manual_recovery_returns_real_instructions(credential_store):
    fake = FakeStravaUnofficialSession()
    connector = StravaUnofficialConnector(credential_store, session=fake)

    message = connector.request_manual_recovery()

    assert "_strava4_session" in message
    assert message != "No manual recovery procedure is defined for this connector."


# --- download() ---------------------------------------------------------------

def _authenticate_with_cookie(connector, credential_store, cookie="valid-cookie"):
    credential_store.set(PROVIDER, CRED_STRAVA_SESSION_COOKIE, cookie)
    credential_store.set(PROVIDER, CRED_STRAVA_SESSION_EXPIRES_AT, str(_future_expiry()))
    assert connector.authenticate() is True


def test_download_before_authenticate_raises_authentication_error_no_network_call(credential_store):
    fake = FakeStravaUnofficialSession()
    connector = StravaUnofficialConnector(credential_store, session=fake)

    with pytest.raises(AuthenticationError):
        connector.download()
    assert fake.get_calls == []


def test_download_no_checkpoint_full_backfill_concatenates_all_pages(credential_store):
    fake = FakeStravaUnofficialSession()
    fake.script_get_response(FakeResponse(200, [{"id": i} for i in range(200)]))
    fake.script_get_response(FakeResponse(200, [{"id": i} for i in range(200, 400)]))
    fake.script_get_response(FakeResponse(200, [{"id": 400}]))
    fake.script_get_response(FakeResponse(200, []))
    connector = StravaUnofficialConnector(credential_store, session=fake)
    _authenticate_with_cookie(connector, credential_store)

    activities = connector.download()

    assert len(activities) == 401
    assert [c["params"]["page"] for c in fake.get_calls] == [1, 2, 3, 4]


def test_download_checkpoint_sends_correct_after_epoch_seconds(credential_store):
    fake = FakeStravaUnofficialSession()
    fake.script_get_response(FakeResponse(200, []))
    connector = StravaUnofficialConnector(credential_store, session=fake)
    _authenticate_with_cookie(connector, credential_store)

    connector.download(since="2024-01-01T00:00:00+00:00")

    expected_after = int(datetime.fromisoformat("2024-01-01T00:00:00+00:00").timestamp())
    assert fake.get_calls[0]["params"]["after"] == expected_after


def test_download_empty_first_page_returns_empty_list_no_exception(credential_store):
    fake = FakeStravaUnofficialSession()
    fake.script_get_response(FakeResponse(200, []))
    connector = StravaUnofficialConnector(credential_store, session=fake)
    _authenticate_with_cookie(connector, credential_store)

    assert connector.download() == []


def test_download_uses_cookie_header(credential_store):
    fake = FakeStravaUnofficialSession()
    fake.script_get_response(FakeResponse(200, []))
    connector = StravaUnofficialConnector(credential_store, session=fake)
    _authenticate_with_cookie(connector, credential_store, cookie="my-session-cookie")

    connector.download()

    assert fake.get_calls[0]["headers"]["Cookie"] == "_strava4_session=my-session-cookie"


def test_download_requests_against_corrected_base_url(credential_store):
    """Same guard as submit_manual_recovery()'s base-URL test, for the
    other call site (issue #26)."""
    fake = FakeStravaUnofficialSession()
    fake.script_get_response(FakeResponse(200, []))
    connector = StravaUnofficialConnector(credential_store, session=fake)
    _authenticate_with_cookie(connector, credential_store)

    connector.download()

    assert fake.get_calls[0]["url"] == "https://www.strava.com/api/v3/athlete/activities"


# --- Error handling ------------------------------------------------------------

def test_download_429_with_retry_after_header(credential_store):
    fake = FakeStravaUnofficialSession()
    fake.script_get_response(FakeResponse(429, {}, headers={"Retry-After": "120"}))
    connector = StravaUnofficialConnector(credential_store, session=fake)
    _authenticate_with_cookie(connector, credential_store)

    with pytest.raises(TransientError) as excinfo:
        connector.download()
    assert excinfo.value.retry_after_s == 120.0


def test_download_429_without_retry_after_header_uses_default(credential_store):
    fake = FakeStravaUnofficialSession()
    fake.script_get_response(FakeResponse(429, {}))
    connector = StravaUnofficialConnector(credential_store, session=fake)
    _authenticate_with_cookie(connector, credential_store)

    with pytest.raises(TransientError) as excinfo:
        connector.download()
    assert excinfo.value.retry_after_s == DEFAULT_RETRY_AFTER_S


@pytest.mark.parametrize("status", [401, 403])
def test_download_401_403_raises_authentication_error_and_clears_all_credentials(credential_store, status):
    fake = FakeStravaUnofficialSession()
    fake.script_get_response(FakeResponse(status, {}))
    connector = StravaUnofficialConnector(credential_store, session=fake)
    _authenticate_with_cookie(connector, credential_store)

    with pytest.raises(AuthenticationError):
        connector.download()

    assert credential_store.exists(PROVIDER, CRED_STRAVA_SESSION_COOKIE) is False
    assert credential_store.exists(PROVIDER, CRED_STRAVA_SESSION_OBTAINED_AT) is False
    assert credential_store.exists(PROVIDER, CRED_STRAVA_SESSION_EXPIRES_AT) is False


@pytest.mark.parametrize("status", [404, 500])
def test_download_404_and_5xx_raise_transient_error(credential_store, status):
    fake = FakeStravaUnofficialSession()
    fake.script_get_response(FakeResponse(status, {}))
    connector = StravaUnofficialConnector(credential_store, session=fake)
    _authenticate_with_cookie(connector, credential_store)

    with pytest.raises(TransientError):
        connector.download()


def test_download_unexpected_status_raises_strava_unofficial_http_error(credential_store):
    fake = FakeStravaUnofficialSession()
    fake.script_get_response(FakeResponse(418, {}))
    connector = StravaUnofficialConnector(credential_store, session=fake)
    _authenticate_with_cookie(connector, credential_store)

    with pytest.raises(StravaUnofficialHTTPError):
        connector.download()


def test_download_network_level_failure_raises_transient_error(credential_store):
    from trainiq.connectors.strava_unofficial import _TransientHTTPCondition

    class _RaisingSession:
        def get(self, url, params=None, headers=None):
            raise _TransientHTTPCondition("connection reset")

    connector = StravaUnofficialConnector(credential_store, session=_RaisingSession())
    _authenticate_with_cookie(connector, credential_store)

    with pytest.raises(TransientError):
        connector.download()


# --- normalize() ---------------------------------------------------------------

# Same fixture shapes test_strava_connector.py uses for StravaConnector.normalize(),
# per the architecture doc's "identical mapping" claim (AC4).
_REAL_ACTIVITY_RECORD = {
    "id": 123,
    "start_date": "2026-01-05T07:00:00+00:00",
    "elapsed_time": 3600,
    "sport_type": "Ride",
    "type": "Ride",
    "average_heartrate": 145.0,
    "max_heartrate": 168,
    "average_watts": 210.0,
    "max_watts": 310,
    "distance": 30000.0,
}


def test_normalize_maps_core_fields_identically_to_strava_connector(credential_store):
    fake = FakeStravaUnofficialSession()
    connector = StravaUnofficialConnector(credential_store, session=fake)

    result = connector.normalize(_REAL_ACTIVITY_RECORD)

    assert result["provider"] == PROVIDER
    assert result["external_id"] == "123"
    assert result["start_time"] == "2026-01-05T07:00:00+00:00"
    assert result["duration_s"] == 3600
    assert result["discipline_raw"] == "Ride"
    assert result["avg_hr"] == 145
    assert result["max_hr"] == 168
    assert result["avg_power"] == 210
    assert result["max_power"] == 310
    assert result["distance_m"] == 30000.0
    assert result["calories"] is None


def test_normalize_falls_back_to_type_when_sport_type_absent(credential_store):
    fake = FakeStravaUnofficialSession()
    connector = StravaUnofficialConnector(credential_store, session=fake)
    raw = {**_REAL_ACTIVITY_RECORD, "sport_type": None, "type": "Run"}

    result = connector.normalize(raw)

    assert result["discipline_raw"] == "Run"


def test_normalize_handles_missing_hr_and_power_as_none_not_zero(credential_store):
    fake = FakeStravaUnofficialSession()
    connector = StravaUnofficialConnector(credential_store, session=fake)
    raw = {
        **_REAL_ACTIVITY_RECORD,
        "average_heartrate": None, "max_heartrate": None,
        "average_watts": None, "max_watts": None,
    }

    result = connector.normalize(raw)

    assert result["avg_hr"] is None
    assert result["max_hr"] is None
    assert result["avg_power"] is None
    assert result["max_power"] is None


# --- extract_resume_cursor() ----------------------------------------------------

def test_extract_resume_cursor_uses_inherited_base_class_behavior(credential_store):
    fake = FakeStravaUnofficialSession()
    connector = StravaUnofficialConnector(credential_store, session=fake)

    assert connector.extract_resume_cursor({"start_time": "2026-01-05T07:00:00+00:00"}) == "2026-01-05T07:00:00+00:00"
    assert connector.extract_resume_cursor({}) is None
