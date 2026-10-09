"""
Tests for the Strava session-cookie connector (issue #18; endpoint switch
to Strava's web endpoints per issue #30).

Every test uses a fake HTTP session — this sandbox cannot reach
strava.com (see module docstring in
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
    TRAINING_ACTIVITIES_PATH,
    StravaUnofficialConnector,
    StravaUnofficialHTTPError,
    _detect_system_timezone,
)
from trainiq.credentials.store import CredentialStore
from trainiq.normalization.taxonomy import Discipline, map_discipline
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
    """`status_code`/`headers` as before. `json_body=None` with
    `raise_on_json=True` scripts a 200 (or any status) whose body is not
    actually JSON (issue #30's "HTML login page" case) — `.json()` raises
    `ValueError` the way a real HTML body would via `requests`."""

    def __init__(
        self,
        status_code: int,
        json_body=None,
        headers: dict | None = None,
        raise_on_json: bool = False,
    ):
        self.status_code = status_code
        self._json_body = json_body if json_body is not None else {}
        self.headers = headers or {}
        self._raise_on_json = raise_on_json

    def json(self):
        if self._raise_on_json:
            raise ValueError("Expecting value: line 1 column 1 (char 0)")
        return self._json_body


class FakeStravaUnofficialSession:
    """Fake stand-in for `_RequestsSession`. Production code disables
    redirect-following (`allow_redirects=False`) so a 3xx response is
    observable by `_authenticated_get` instead of being silently followed
    to a 200 HTML page — this fake has no real HTTP layer to disable
    redirects on, so that constraint is exercised by `_RequestsSession`
    itself, not here; this fake just needs to hand back whatever
    `FakeResponse` (including a scripted 3xx) a test scripts."""

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


def _training_activities_body(models: list[dict], total: int, per_page: int = 20) -> dict:
    return {"models": models, "page": 1, "perPage": per_page, "total": total}


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
    fake.script_get_response(FakeResponse(200, _training_activities_body([{"id": 1}], total=1)))
    connector = StravaUnofficialConnector(credential_store, session=fake)

    assert connector.submit_manual_recovery("new-cookie-value") is True
    assert credential_store.get(PROVIDER, CRED_STRAVA_SESSION_COOKIE) == "new-cookie-value"
    obtained_at = int(credential_store.get(PROVIDER, CRED_STRAVA_SESSION_OBTAINED_AT))
    expires_at = int(credential_store.get(PROVIDER, CRED_STRAVA_SESSION_EXPIRES_AT))
    assert expires_at == obtained_at + ASSUMED_SESSION_LIFETIME_S
    assert fake.get_calls[0]["headers"]["Cookie"] == "_strava4_session=new-cookie-value"


def test_submit_manual_recovery_validates_against_training_activities_endpoint(credential_store):
    """Issue #30: /api/v3/athlete now 401s even for a valid cookie, so
    validation must target the web endpoint instead."""
    fake = FakeStravaUnofficialSession()
    fake.script_get_response(FakeResponse(200, _training_activities_body([{"id": 1}], total=1)))
    connector = StravaUnofficialConnector(credential_store, session=fake)

    connector.submit_manual_recovery("new-cookie-value")

    assert fake.get_calls[0]["url"] == f"https://www.strava.com{TRAINING_ACTIVITIES_PATH}"
    assert fake.get_calls[0]["params"] == {"page": 1}


def test_submit_manual_recovery_includes_web_endpoint_headers(credential_store):
    fake = FakeStravaUnofficialSession()
    fake.script_get_response(FakeResponse(200, _training_activities_body([{"id": 1}], total=1)))
    connector = StravaUnofficialConnector(credential_store, session=fake)

    connector.submit_manual_recovery("new-cookie-value")

    headers = fake.get_calls[0]["headers"]
    assert headers["X-Requested-With"] == "XMLHttpRequest"
    assert headers["Accept"] == "application/json"
    assert "User-Agent" in headers


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


def test_submit_manual_recovery_redirect_to_login_returns_false_nothing_persisted(credential_store):
    """Issue #30 AC5/AC3: a 3xx redirect to the login page is an
    invalid-session signal, same as 401/403."""
    fake = FakeStravaUnofficialSession()
    fake.script_get_response(FakeResponse(302, headers={"Location": "https://www.strava.com/login"}))
    connector = StravaUnofficialConnector(credential_store, session=fake)

    assert connector.submit_manual_recovery("bad-cookie") is False
    assert credential_store.exists(PROVIDER, CRED_STRAVA_SESSION_COOKIE) is False


def test_submit_manual_recovery_html_body_instead_of_json_returns_false_nothing_persisted(credential_store):
    """Issue #30 AC5: a 200 whose body isn't JSON (an HTML login/interstitial
    page) is also an invalid-session signal."""
    fake = FakeStravaUnofficialSession()
    fake.script_get_response(FakeResponse(200, raise_on_json=True))
    connector = StravaUnofficialConnector(credential_store, session=fake)

    assert connector.submit_manual_recovery("bad-cookie") is False
    assert credential_store.exists(PROVIDER, CRED_STRAVA_SESSION_COOKIE) is False


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


def test_download_pagination_terminates_via_total_not_empty_page(credential_store):
    """Regression test for the bug this issue exists to fix: `per_page` is
    ignored by this endpoint, so a full, non-empty final page must still
    stop the loop once `total` items have been fetched — looping until an
    empty page (the old REST-endpoint behavior) would never terminate here,
    since every page the server returns is full."""
    fake = FakeStravaUnofficialSession()
    fake.script_get_response(_response_page([{"id": i} for i in range(20)], total=45))
    fake.script_get_response(_response_page([{"id": i} for i in range(20, 40)], total=45))
    fake.script_get_response(_response_page([{"id": i} for i in range(40, 45)], total=45))
    connector = StravaUnofficialConnector(credential_store, session=fake)
    _authenticate_with_cookie(connector, credential_store)

    activities = connector.download()

    assert len(activities) == 45
    assert [c["params"]["page"] for c in fake.get_calls] == [1, 2, 3]


def test_download_total_not_evenly_divisible_by_page_size_still_terminates_correctly(credential_store):
    fake = FakeStravaUnofficialSession()
    fake.script_get_response(_response_page([{"id": i} for i in range(20)], total=41))
    fake.script_get_response(_response_page([{"id": i} for i in range(20, 40)], total=41))
    fake.script_get_response(_response_page([{"id": 40}], total=41))
    connector = StravaUnofficialConnector(credential_store, session=fake)
    _authenticate_with_cookie(connector, credential_store)

    activities = connector.download()

    assert len(activities) == 41
    assert [c["params"]["page"] for c in fake.get_calls] == [1, 2, 3]


def test_download_empty_first_page_zero_total_returns_empty_list_one_request(credential_store):
    fake = FakeStravaUnofficialSession()
    fake.script_get_response(_response_page([], total=0))
    connector = StravaUnofficialConnector(credential_store, session=fake)
    _authenticate_with_cookie(connector, credential_store)

    assert connector.download() == []
    assert len(fake.get_calls) == 1


def test_download_checkpoint_stops_early_mid_page_and_requests_no_further_pages(credential_store):
    """Client-side incremental stop: a batch mixing newer and older-than-
    checkpoint items returns only the newer ones, and no second page is
    requested — confirms early-stop, not just correct filtering."""
    since = "2026-01-10T00:00:00+00:00"
    newer = {"id": 1, "start_time": "2026-01-15T00:00:00+0000"}
    older = {"id": 2, "start_time": "2026-01-05T00:00:00+0000"}
    fake = FakeStravaUnofficialSession()
    fake.script_get_response(_response_page([newer, older], total=50))
    connector = StravaUnofficialConnector(credential_store, session=fake)
    _authenticate_with_cookie(connector, credential_store)

    activities = connector.download(since=since)

    assert activities == [newer]
    assert len(fake.get_calls) == 1


def test_download_checkpoint_older_than_every_item_proceeds_through_full_pagination(credential_store):
    since = "2020-01-01T00:00:00+00:00"
    item = {"id": 1, "start_time": "2026-01-15T00:00:00+0000"}
    fake = FakeStravaUnofficialSession()
    fake.script_get_response(_response_page([item], total=1))
    connector = StravaUnofficialConnector(credential_store, session=fake)
    _authenticate_with_cookie(connector, credential_store)

    activities = connector.download(since=since)

    assert activities == [item]


def test_download_uses_cookie_header(credential_store):
    fake = FakeStravaUnofficialSession()
    fake.script_get_response(_response_page([], total=0))
    connector = StravaUnofficialConnector(credential_store, session=fake)
    _authenticate_with_cookie(connector, credential_store, cookie="my-session-cookie")

    connector.download()

    assert fake.get_calls[0]["headers"]["Cookie"] == "_strava4_session=my-session-cookie"


def test_download_requests_against_training_activities_endpoint(credential_store):
    """Issue #30: /api/v3/athlete/activities now 401s; download() must use
    the web endpoint instead."""
    fake = FakeStravaUnofficialSession()
    fake.script_get_response(_response_page([], total=0))
    connector = StravaUnofficialConnector(credential_store, session=fake)
    _authenticate_with_cookie(connector, credential_store)

    connector.download()

    assert fake.get_calls[0]["url"] == f"https://www.strava.com{TRAINING_ACTIVITIES_PATH}"


def test_download_includes_web_endpoint_headers(credential_store):
    fake = FakeStravaUnofficialSession()
    fake.script_get_response(_response_page([], total=0))
    connector = StravaUnofficialConnector(credential_store, session=fake)
    _authenticate_with_cookie(connector, credential_store)

    connector.download()

    headers = fake.get_calls[0]["headers"]
    assert headers["X-Requested-With"] == "XMLHttpRequest"
    assert headers["Accept"] == "application/json"
    assert "User-Agent" in headers


# --- Invalid-session classification (AC5) --------------------------------------

def test_download_redirect_to_login_raises_authentication_error_and_clears_credentials(credential_store):
    fake = FakeStravaUnofficialSession()
    fake.script_get_response(FakeResponse(302, headers={"Location": "https://www.strava.com/login"}))
    connector = StravaUnofficialConnector(credential_store, session=fake)
    _authenticate_with_cookie(connector, credential_store)

    with pytest.raises(AuthenticationError):
        connector.download()

    assert credential_store.exists(PROVIDER, CRED_STRAVA_SESSION_COOKIE) is False
    assert credential_store.exists(PROVIDER, CRED_STRAVA_SESSION_OBTAINED_AT) is False
    assert credential_store.exists(PROVIDER, CRED_STRAVA_SESSION_EXPIRES_AT) is False


def test_download_redirect_without_location_header_still_raises_authentication_error(credential_store):
    fake = FakeStravaUnofficialSession()
    fake.script_get_response(FakeResponse(303))
    connector = StravaUnofficialConnector(credential_store, session=fake)
    _authenticate_with_cookie(connector, credential_store)

    with pytest.raises(AuthenticationError):
        connector.download()


def test_download_html_body_instead_of_json_raises_authentication_error_and_clears_credentials(credential_store):
    fake = FakeStravaUnofficialSession()
    fake.script_get_response(FakeResponse(200, raise_on_json=True))
    connector = StravaUnofficialConnector(credential_store, session=fake)
    _authenticate_with_cookie(connector, credential_store)

    with pytest.raises(AuthenticationError):
        connector.download()

    assert credential_store.exists(PROVIDER, CRED_STRAVA_SESSION_COOKIE) is False
    assert credential_store.exists(PROVIDER, CRED_STRAVA_SESSION_OBTAINED_AT) is False
    assert credential_store.exists(PROVIDER, CRED_STRAVA_SESSION_EXPIRES_AT) is False


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


# --- Error handling (unchanged behavior, new endpoint) --------------------------

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


def _response_page(models: list[dict], total: int) -> FakeResponse:
    return FakeResponse(200, _training_activities_body(models, total=total))


# --- normalize() / _extract_start_time_iso() ------------------------------------

# Shaped like the issue's live-captured evidence
# (docs/trainiq/requirements/30-strava-unofficial-web-endpoints.md), with
# `start_time` as the primary field (issue #43) — every live payload seen
# so far includes it.
_WEB_ACTIVITY_RECORD = {
    "id": 123,
    "name": "Morning Ride",
    "display_type": "Ride",
    "activity_type_display_name": "Ride",
    "distance_raw": 30000.0,
    "moving_time_raw": 3500,
    "elapsed_time_raw": 3600,
    "elevation_gain_raw": 120.0,
    "start_time": "2026-01-05T07:00:00+0000",
    "commute": False,
    "private": False,
    "has_latlng": True,
    "description": "",
}


def test_normalize_maps_web_payload_fields_to_canonical_shape(credential_store):
    fake = FakeStravaUnofficialSession()
    connector = StravaUnofficialConnector(credential_store, session=fake)

    result = connector.normalize(_WEB_ACTIVITY_RECORD)

    assert result["provider"] == PROVIDER
    assert result["external_id"] == "123"
    assert result["start_time"] == datetime(2026, 1, 5, 7, 0, 0, tzinfo=timezone.utc).isoformat()
    assert result["duration_s"] == 3600
    assert result["discipline_raw"] == "Ride"
    assert result["distance_m"] == 30000.0
    assert result["avg_hr"] is None
    assert result["max_hr"] is None
    assert result["avg_power"] is None
    assert result["max_power"] is None
    assert result["calories"] is None
    # Issue #46 (AC3): raw name/sport_type persisted independent of any
    # Peloton link.
    assert result["activity_title"] == "Morning Ride"
    assert result["sport_type_raw"] == "Ride"


def test_normalize_activity_title_is_none_when_name_absent(credential_store):
    fake = FakeStravaUnofficialSession()
    connector = StravaUnofficialConnector(credential_store, session=fake)
    raw = {**_WEB_ACTIVITY_RECORD, "name": None}

    result = connector.normalize(raw)

    assert result["activity_title"] is None


def test_normalize_sport_type_raw_falls_back_to_display_type(credential_store):
    fake = FakeStravaUnofficialSession()
    connector = StravaUnofficialConnector(credential_store, session=fake)
    raw = {**_WEB_ACTIVITY_RECORD, "activity_type_display_name": None, "display_type": "Run"}

    result = connector.normalize(raw)

    assert result["sport_type_raw"] == "Run"


# --- Issue #43: start_time field priority + timezone handling ------------------

def test_normalize_bst_summer_payload_matches_peloton_epoch_for_same_ride(credential_store):
    """AC3 (summer/BST case): the BO's live evidence — `start_time`
    carrying the correct UTC instant, with `start_date_local_raw` present
    but shifted +1h — must normalize to the same instant Peloton reported
    for the same real-world ride, not the shifted local value."""
    fake = FakeStravaUnofficialSession()
    connector = StravaUnofficialConnector(credential_store, session=fake)
    raw = {
        **_WEB_ACTIVITY_RECORD,
        "start_time": "2026-10-07T18:57:34+0000",
        # BST epoch for the same instant — would wrongly read as 19:57:34
        # if treated as UTC (today's bug). Present to prove start_time
        # wins over it, not absence-by-construction.
        "start_date_local_raw": int(datetime(2026, 10, 7, 19, 57, 34, tzinfo=timezone.utc).timestamp()),
    }
    peloton_epoch_utc = datetime(2026, 10, 7, 18, 57, 34, tzinfo=timezone.utc)

    result = connector.normalize(raw)

    assert result["start_time"] == peloton_epoch_utc.isoformat()


def test_normalize_gmt_winter_payload_matches_peloton_epoch_for_same_ride(credential_store):
    """AC3 (winter/GMT case): local time equals UTC, so this must continue
    to work exactly as before."""
    fake = FakeStravaUnofficialSession()
    connector = StravaUnofficialConnector(credential_store, session=fake)
    raw = {
        **_WEB_ACTIVITY_RECORD,
        "start_time": "2026-01-07T18:57:34+0000",
        "start_date_local_raw": int(datetime(2026, 1, 7, 18, 57, 34, tzinfo=timezone.utc).timestamp()),
    }
    peloton_epoch_utc = datetime(2026, 1, 7, 18, 57, 34, tzinfo=timezone.utc)

    result = connector.normalize(raw)

    assert result["start_time"] == peloton_epoch_utc.isoformat()


def test_normalize_start_time_with_colon_offset_also_parses_correctly(credential_store):
    """Not every offset-aware ISO string uses Strava's exact +0000 shape —
    confirms fromisoformat's handling isn't accidentally narrower."""
    fake = FakeStravaUnofficialSession()
    connector = StravaUnofficialConnector(credential_store, session=fake)
    raw = {**_WEB_ACTIVITY_RECORD, "start_time": "2026-06-01T12:00:00+01:00"}

    result = connector.normalize(raw)

    assert result["start_time"] == datetime(2026, 6, 1, 11, 0, 0, tzinfo=timezone.utc).isoformat()


def test_normalize_start_time_without_offset_raises_rather_than_assuming_utc(credential_store):
    """An offset-naive start_time must never be silently treated as UTC —
    that would just be today's bug, moved to a different field."""
    fake = FakeStravaUnofficialSession()
    connector = StravaUnofficialConnector(credential_store, session=fake)
    raw = {**_WEB_ACTIVITY_RECORD, "start_time": "2026-06-01T12:00:00"}

    with pytest.raises(StravaUnofficialHTTPError):
        connector.normalize(raw)


def test_normalize_falls_back_to_local_raw_with_explicit_timezone_when_start_time_absent(credential_store):
    """Fallback path (start_time absent): start_date_local_raw is
    converted via an explicit local-timezone rule, reproducing the BO's
    evidence table exactly — never treated as a UTC epoch (today's bug)."""
    fake = FakeStravaUnofficialSession()
    connector = StravaUnofficialConnector(
        credential_store, session=fake, local_timezone="Europe/London"
    )
    raw = {k: v for k, v in _WEB_ACTIVITY_RECORD.items() if k != "start_time"}
    raw["start_date_local_raw"] = int(datetime(2026, 10, 7, 19, 57, 34, tzinfo=timezone.utc).timestamp())

    result = connector.normalize(raw)

    assert result["start_time"] == datetime(2026, 10, 7, 18, 57, 34, tzinfo=timezone.utc).isoformat()


def test_normalize_falls_back_to_local_raw_unaffected_in_winter(credential_store):
    fake = FakeStravaUnofficialSession()
    connector = StravaUnofficialConnector(
        credential_store, session=fake, local_timezone="Europe/London"
    )
    raw = {k: v for k, v in _WEB_ACTIVITY_RECORD.items() if k != "start_time"}
    raw["start_date_local_raw"] = int(datetime(2026, 1, 7, 18, 57, 34, tzinfo=timezone.utc).timestamp())

    result = connector.normalize(raw)

    assert result["start_time"] == datetime(2026, 1, 7, 18, 57, 34, tzinfo=timezone.utc).isoformat()


def test_normalize_local_raw_fallback_with_no_known_timezone_raises_not_silently_utc(
    credential_store, monkeypatch
):
    """No config value and no detectable system timezone: must fail loud,
    never guess the local zone is UTC (today's bug, reintroduced). Passing
    `local_timezone=None` to the constructor means "auto-detect," not
    "force none" (see __init__), so the no-timezone-resolvable case is
    simulated by making detection itself report None."""
    monkeypatch.setattr(
        "trainiq.connectors.strava_unofficial._detect_system_timezone", lambda: None
    )
    fake = FakeStravaUnofficialSession()
    connector = StravaUnofficialConnector(credential_store, session=fake)
    raw = {k: v for k, v in _WEB_ACTIVITY_RECORD.items() if k != "start_time"}
    raw["start_date_local_raw"] = int(datetime(2026, 10, 7, 19, 57, 34, tzinfo=timezone.utc).timestamp())

    with pytest.raises(StravaUnofficialHTTPError):
        connector.normalize(raw)


def test_normalize_local_raw_fallback_with_unknown_zone_name_raises_strava_unofficial_http_error(credential_store):
    fake = FakeStravaUnofficialSession()
    connector = StravaUnofficialConnector(credential_store, session=fake, local_timezone="Not/AZone")
    raw = {k: v for k, v in _WEB_ACTIVITY_RECORD.items() if k != "start_time"}
    raw["start_date_local_raw"] = int(datetime(2026, 10, 7, 19, 57, 34, tzinfo=timezone.utc).timestamp())

    with pytest.raises(StravaUnofficialHTTPError):
        connector.normalize(raw)


def test_detect_system_timezone_reads_etc_localtime_symlink_target(tmp_path):
    zoneinfo_dir = tmp_path / "usr" / "share" / "zoneinfo"
    zoneinfo_dir.mkdir(parents=True)
    zone_file = zoneinfo_dir / "Europe" / "London"
    zone_file.parent.mkdir(parents=True)
    zone_file.write_text("")
    localtime_link = tmp_path / "localtime"
    localtime_link.symlink_to(zone_file)

    assert _detect_system_timezone(localtime_link) == "Europe/London"


def test_detect_system_timezone_returns_none_when_not_a_symlink(tmp_path):
    not_a_symlink = tmp_path / "localtime"
    not_a_symlink.write_text("")

    assert _detect_system_timezone(not_a_symlink) is None


def test_detect_system_timezone_returns_none_when_target_missing_entirely(tmp_path):
    missing = tmp_path / "does_not_exist"

    assert _detect_system_timezone(missing) is None


def test_detect_system_timezone_returns_none_when_target_not_under_zoneinfo(tmp_path):
    target = tmp_path / "some_other_file"
    target.write_text("")
    localtime_link = tmp_path / "localtime"
    localtime_link.symlink_to(target)

    assert _detect_system_timezone(localtime_link) is None


def test_connector_without_explicit_local_timezone_detects_system_default(credential_store, monkeypatch):
    """Constructor default: when the caller doesn't resolve a timezone
    itself, the connector resolves the system default at construction
    time (cheap — one symlink read, no network call)."""
    monkeypatch.setattr(
        "trainiq.connectors.strava_unofficial._detect_system_timezone", lambda: "Europe/London"
    )
    fake = FakeStravaUnofficialSession()
    connector = StravaUnofficialConnector(credential_store, session=fake)

    assert connector._local_timezone == "Europe/London"


def test_normalize_falls_back_to_display_type_when_activity_type_display_name_absent(credential_store):
    fake = FakeStravaUnofficialSession()
    connector = StravaUnofficialConnector(credential_store, session=fake)
    raw = {**_WEB_ACTIVITY_RECORD, "activity_type_display_name": None, "display_type": "Run"}

    result = connector.normalize(raw)

    assert result["discipline_raw"] == "Run"


def test_normalize_no_start_time_candidate_present_raises_loudly(credential_store):
    """Confirms the 'fail loud, don't silently corrupt the checkpoint'
    design choice — exactly the scenario if the field-name guess in
    _START_FIELD_CANDIDATES turns out wrong in production."""
    fake = FakeStravaUnofficialSession()
    connector = StravaUnofficialConnector(credential_store, session=fake)
    raw = {k: v for k, v in _WEB_ACTIVITY_RECORD.items() if k != "start_time"}

    with pytest.raises(StravaUnofficialHTTPError):
        connector.normalize(raw)


# --- Issue #36: discipline mapping (end-to-end, connector + taxonomy together) --

def test_mountain_bike_ride_normalizes_and_maps_to_cycling_end_to_end(credential_store):
    """AC5: a raw payload with display_type="Mountain Bike Ride" must still
    resolve to Discipline.CYCLING — not because the taxonomy map has a
    "Mountain Bike Ride" key (it doesn't need one), but because normalize()
    already prefers activity_type_display_name ("Ride") over display_type.
    Exercised end-to-end (normalize() then map_discipline()), not as a
    taxonomy-only unit test, since the whole point is that the map never
    sees "Mountain Bike Ride" as input at all."""
    fake = FakeStravaUnofficialSession()
    connector = StravaUnofficialConnector(credential_store, session=fake)
    raw = {
        **_WEB_ACTIVITY_RECORD,
        "display_type": "Mountain Bike Ride",
        "activity_type_display_name": "Ride",
    }

    normalized = connector.normalize(raw)
    discipline = map_discipline(PROVIDER, normalized["discipline_raw"])

    assert normalized["discipline_raw"] == "Ride"
    assert discipline == Discipline.CYCLING


def test_run_normalizes_and_maps_to_running_end_to_end(credential_store):
    fake = FakeStravaUnofficialSession()
    connector = StravaUnofficialConnector(credential_store, session=fake)
    raw = {**_WEB_ACTIVITY_RECORD, "display_type": "Run", "activity_type_display_name": "Run"}

    normalized = connector.normalize(raw)
    discipline = map_discipline(PROVIDER, normalized["discipline_raw"])

    assert discipline == Discipline.RUNNING


def test_walk_normalizes_and_maps_to_other_end_to_end(credential_store):
    fake = FakeStravaUnofficialSession()
    connector = StravaUnofficialConnector(credential_store, session=fake)
    raw = {**_WEB_ACTIVITY_RECORD, "display_type": "Walk", "activity_type_display_name": "Walk"}

    normalized = connector.normalize(raw)
    discipline = map_discipline(PROVIDER, normalized["discipline_raw"])

    assert discipline == Discipline.OTHER


def test_workout_normalizes_and_maps_to_strength_end_to_end(credential_store):
    fake = FakeStravaUnofficialSession()
    connector = StravaUnofficialConnector(credential_store, session=fake)
    raw = {**_WEB_ACTIVITY_RECORD, "display_type": "Workout", "activity_type_display_name": "Workout"}

    normalized = connector.normalize(raw)
    discipline = map_discipline(PROVIDER, normalized["discipline_raw"])

    assert discipline == Discipline.STRENGTH


# --- extract_resume_cursor() ----------------------------------------------------

def test_extract_resume_cursor_uses_inherited_base_class_behavior(credential_store):
    fake = FakeStravaUnofficialSession()
    connector = StravaUnofficialConnector(credential_store, session=fake)

    assert connector.extract_resume_cursor({"start_time": "2026-01-05T07:00:00+00:00"}) == "2026-01-05T07:00:00+00:00"
    assert connector.extract_resume_cursor({}) is None


# --- AC6: resume-cursor consistency after renormalization (issue #43) -----------

def test_stale_cursor_from_old_bug_incorrectly_skips_a_later_bst_activity(db, credential_store):
    """Reproduces the exact failure mode AC6 exists to prevent: a
    checkpoint computed under the old (wrong, shifted +1h) start_time
    logic sits ABOVE another activity's true, corrected start_time, so
    the connector's early-stop comparison wrongly treats that activity as
    already synced and skips it, even though it was never downloaded."""
    old_wrong_cursor = "2026-10-07T19:57:34+00:00"  # the old bug's +1h value
    fake = FakeStravaUnofficialSession()
    fake.script_get_response(
        _response_page(
            [{"id": "new-bst-2", "start_time": "2026-10-07T19:30:00+0000"}], total=1
        )
    )
    connector = StravaUnofficialConnector(
        credential_store, session=fake, local_timezone="Europe/London"
    )
    _authenticate_with_cookie(connector, credential_store)

    activities = connector.download(since=old_wrong_cursor)

    assert activities == []  # wrongly skipped


def test_recomputed_cursor_after_renormalization_does_not_skip_that_same_activity(db, credential_store):
    """The fix: recompute_checkpoint_from_normalized() (issue #43),
    run after a renormalization pass, re-derives the checkpoint from the
    now-corrected normalized_activities data instead of leaving the old,
    too-high cursor in place. With the corrected (lower) cursor, the same
    activity from the previous test is no longer wrongly skipped."""
    from trainiq.sync.engine import SynchronizationEngine, recompute_checkpoint_from_normalized

    corrected_max = "2026-10-07T18:57:34+00:00"  # true max after correction
    db.execute(
        """
        INSERT INTO normalized_activities
            (provider, external_id, start_time, duration_s, discipline, distance_m,
             avg_hr, max_hr, avg_power, max_power, calories,
             training_load, training_load_method, source_confidence)
        VALUES (?, ?, ?, ?, 'cycling', NULL, NULL, NULL, NULL, NULL, NULL, NULL, 'unknown', 0.5)
        """,
        (PROVIDER, "already-synced-1", corrected_max, 1800),
    )
    db.execute(
        "INSERT INTO sync_checkpoints (provider, strategy, last_success_at, last_cursor) "
        "VALUES (?, 'unofficial_session', ?, ?)",
        (PROVIDER, "2026-10-07T19:57:34+00:00", "2026-10-07T19:57:34+00:00"),
    )
    db.commit()

    new_cursor = recompute_checkpoint_from_normalized(db, PROVIDER, strategy="unofficial_session")
    db.commit()

    assert new_cursor == corrected_max
    engine = SynchronizationEngine(conn=db)
    assert engine.get_checkpoint(PROVIDER, strategy="unofficial_session") == corrected_max

    fake = FakeStravaUnofficialSession()
    fake.script_get_response(
        _response_page(
            [{"id": "new-bst-2", "start_time": "2026-10-07T19:30:00+0000"}], total=1
        )
    )
    connector = StravaUnofficialConnector(
        credential_store, session=fake, local_timezone="Europe/London"
    )
    _authenticate_with_cookie(connector, credential_store)

    activities = connector.download(since=new_cursor)

    assert len(activities) == 1
    assert activities[0]["id"] == "new-bst-2"
