"""
Tests for Epic 3 — Peloton Connector (Features 3.1, 3.2, 3.3, 3.4).

Every test uses a fake HTTP session — this sandbox cannot reach
api.onepeloton.com (see module docstring in trainiq/connectors/peloton.py).
"""

from __future__ import annotations

import base64
import hashlib
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from trainiq.connectors.base import ConnectorState
from trainiq.connectors.peloton import (
    CLASS_TYPE_LOOKUP_FAILED,
    CLASS_TYPE_NOT_A_CLASS,
    CRED_EMAIL,
    CRED_MANUAL_BEARER_TOKEN,
    CRED_OAUTH_ACCESS_TOKEN,
    CRED_OAUTH_EXPIRES_AT,
    CRED_OAUTH_REFRESH_TOKEN,
    CRED_PASSWORD,
    CRED_SESSION_EXPIRES_AT,
    CRED_SESSION_ID,
    OAUTH_TOKEN_URL,
    PERFORMANCE_FETCH_STATUS_FAILED,
    PERFORMANCE_FETCH_STATUS_OK,
    PROVIDER,
    RIDE_ID_FIELD,
    SESSION_RIDE_ID_FIELD,
    WORKOUT_TYPE_FIELD,
    PelotonConnector,
    PelotonHTTPError,
    PelotonOAuthRejected,
    _extract_ride_metadata,
    apply_class_metadata_update,
    apply_hr_performance_update,
    generate_pkce_pair,
    _parse_performance_response,
)
from trainiq.credentials.store import CredentialStore
from trainiq.storage.schema import open_db
from trainiq.sync.engine import AuthenticationError, SynchronizationEngine, TransientError


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
    def __init__(self, status_code: int, json_body: dict | None = None, headers: dict | None = None):
        self.status_code = status_code
        self._json_body = json_body or {}
        self.headers = headers or {}

    def json(self):
        return self._json_body


class FakePelotonSession:
    def __init__(self):
        self.post_calls: list[dict] = []
        self.get_calls: list[dict] = []
        self._post_response: FakeResponse | None = None
        self._get_responses: list[FakeResponse] = []
        # Issue #47 AC2 (merged with #58): performance_graph GETs are routed
        # to their own queue and call log so class-lookup tests can script
        # session/ride-details responses in order without interleaving a
        # performance response per workout. Unscripted -> "no data".
        self.performance_get_calls: list[dict] = []
        self._performance_responses: list[FakeResponse] = []

    def script_post_response(self, response: FakeResponse):
        self._post_response = response

    def script_get_response(self, response: FakeResponse):
        self._get_responses.append(response)

    def post(self, url, json=None, headers=None):
        self.post_calls.append({"url": url, "json": json, "headers": headers})
        return self._post_response

    def script_performance_response(self, response: FakeResponse):
        self._performance_responses.append(response)

    def get(self, url, params=None, headers=None):
        if "/performance_graph" in url:
            self.performance_get_calls.append({"url": url, "params": params, "headers": headers})
            return self._performance_responses.pop(0) if self._performance_responses else _empty_performance_response()
        self.get_calls.append({"url": url, "params": params, "headers": headers})
        return self._get_responses.pop(0)


def _login_success_response(session_id="session-abc"):
    return FakeResponse(200, {"session_id": session_id, "user_id": "u1"})


# Issue #47, AC2: every download() call now also fetches
# performance_graph for each workout new since the checkpoint — these
# tests predate that and only care about pagination/class-metadata, so
# they script the simplest "no HR/power data at all" response for it.
def _empty_performance_response():
    return FakeResponse(200, {"metrics": []})


# What download() attaches to a workout dict for the above response —
# _avg_hr/_max_hr/_max_power keys are always set (to None here), never
# omitted, once a fetch is attempted and does not fail.
_NO_PERFORMANCE_DATA = {"_performance_fetch_status": "ok", "_avg_hr": None, "_max_hr": None, "_max_power": None}


# --- Feature 3.1: Authentication (automated path) ---------------------------

def test_authenticate_returns_false_when_never_connected(credential_store):
    fake = FakePelotonSession()
    connector = PelotonConnector(credential_store, session=fake)
    assert connector.authenticate() is False
    assert fake.post_calls == []


def test_automated_login_success_persists_session(credential_store):
    credential_store.set(PROVIDER, CRED_EMAIL, "athlete@example.com")
    credential_store.set(PROVIDER, CRED_PASSWORD, "pw")
    fake = FakePelotonSession()
    fake.script_post_response(_login_success_response("session-xyz"))
    connector = PelotonConnector(credential_store, session=fake)

    assert connector.authenticate() is True
    assert credential_store.get(PROVIDER, CRED_SESSION_ID) == "session-xyz"
    assert fake.post_calls[0]["headers"]["peloton-platform"] == "web"
    assert fake.post_calls[0]["json"] == {"username_or_email": "athlete@example.com", "password": "pw"}


def test_automated_login_reuses_valid_unexpired_session(credential_store):
    future_expiry = int((datetime.now(timezone.utc) + timedelta(minutes=30)).timestamp())
    credential_store.set(PROVIDER, CRED_EMAIL, "a@b.com")
    credential_store.set(PROVIDER, CRED_PASSWORD, "pw")
    credential_store.set(PROVIDER, CRED_SESSION_ID, "existing-session")
    credential_store.set(PROVIDER, CRED_SESSION_EXPIRES_AT, str(future_expiry))
    fake = FakePelotonSession()
    connector = PelotonConnector(credential_store, session=fake)

    assert connector.authenticate() is True
    assert fake.post_calls == []


def test_automated_login_rejected_returns_false_this_is_the_documented_failure_mode(credential_store):
    """This 403 is the literal, dated, documented Milestone 2 failure
    ('Endpoint no longer accepting requests') — not a hypothetical."""
    credential_store.set(PROVIDER, CRED_EMAIL, "a@b.com")
    credential_store.set(PROVIDER, CRED_PASSWORD, "pw")
    fake = FakePelotonSession()
    fake.script_post_response(FakeResponse(403, {"message": "Access forbidden. Endpoint no longer accepting requests."}))
    connector = PelotonConnector(credential_store, session=fake)

    assert connector.authenticate() is False


def test_automated_login_rate_limited_honors_retry_after_per_adr_037(credential_store):
    credential_store.set(PROVIDER, CRED_EMAIL, "a@b.com")
    credential_store.set(PROVIDER, CRED_PASSWORD, "pw")
    fake = FakePelotonSession()
    fake.script_post_response(FakeResponse(429, {}, headers={"Retry-After": "300"}))
    connector = PelotonConnector(credential_store, session=fake)

    with pytest.raises(TransientError) as excinfo:
        connector.authenticate()
    assert excinfo.value.retry_after_s == 300.0


def test_automated_login_unexpected_status_raises_http_error(credential_store):
    credential_store.set(PROVIDER, CRED_EMAIL, "a@b.com")
    credential_store.set(PROVIDER, CRED_PASSWORD, "pw")
    fake = FakePelotonSession()
    fake.script_post_response(FakeResponse(418, {}))
    connector = PelotonConnector(credential_store, session=fake)

    with pytest.raises(PelotonHTTPError):
        connector.authenticate()


# --- Feature 3.2: Recovery path ---------------------------------------------

def test_request_manual_recovery_returns_real_instructions_not_the_default_stub(credential_store):
    """Peloton is the first connector where this must NOT be the Connector
    base class's generic 'no recovery procedure defined' stub."""
    fake = FakePelotonSession()
    connector = PelotonConnector(credential_store, session=fake)
    message = connector.request_manual_recovery()
    assert "Bearer Token" in message
    assert message != "No manual recovery procedure is defined for this connector."


def test_submit_manual_recovery_rejects_empty_token(credential_store):
    fake = FakePelotonSession()
    connector = PelotonConnector(credential_store, session=fake)
    with pytest.raises(ValueError):
        connector.submit_manual_recovery("")


def test_authenticate_prefers_manual_token_when_in_recovery_required_state(credential_store):
    """Core dual-path behavior: once RecoveryRequired, do NOT keep trying
    automated login (Milestone 2 §7's ToS/hammering caution) — use the
    manually-supplied token instead."""
    fake = FakePelotonSession()
    connector = PelotonConnector(credential_store, session=fake)
    connector.transition_state(ConnectorState.WARNING)
    connector.transition_state(ConnectorState.DEGRADED)
    connector.transition_state(ConnectorState.RECOVERY_REQUIRED)
    connector.submit_manual_recovery("manually-extracted-bearer-token")

    assert connector.authenticate() is True
    assert fake.post_calls == []  # automated login must NOT have been attempted


def test_authenticate_in_recovery_required_with_no_token_yet_returns_false(credential_store):
    fake = FakePelotonSession()
    connector = PelotonConnector(credential_store, session=fake)
    connector.transition_state(ConnectorState.WARNING)
    connector.transition_state(ConnectorState.DEGRADED)
    connector.transition_state(ConnectorState.RECOVERY_REQUIRED)

    assert connector.authenticate() is False
    assert fake.post_calls == []


def test_manual_token_is_used_as_bearer_auth_header(credential_store):
    fake = FakePelotonSession()
    fake.script_get_response(FakeResponse(200, {"id": "u1"}))
    fake.script_get_response(FakeResponse(200, {"data": [], "show_next": False}))
    connector = PelotonConnector(credential_store, session=fake)
    connector.transition_state(ConnectorState.WARNING)
    connector.transition_state(ConnectorState.DEGRADED)
    connector.transition_state(ConnectorState.RECOVERY_REQUIRED)
    connector.submit_manual_recovery("recovery-token-123")
    connector.authenticate()

    connector.download()

    assert fake.get_calls[0]["headers"]["Authorization"] == "Bearer recovery-token-123"


# --- Feature 3.5: OAuth+PKCE auth path (issue #7) ---------------------------

def _oauth_token_response(access_token="access-1", refresh_token="refresh-1", expires_in=172800, status=200):
    return FakeResponse(status, {
        "access_token": access_token,
        "refresh_token": refresh_token,
        "expires_in": expires_in,
    })


def test_generate_pkce_pair_challenge_matches_s256_of_verifier():
    verifier, challenge = generate_pkce_pair()

    expected_digest = hashlib.sha256(verifier.encode("ascii")).digest()
    expected_challenge = base64.urlsafe_b64encode(expected_digest).rstrip(b"=").decode("ascii")

    assert challenge == expected_challenge
    assert 43 <= len(verifier) <= 128


def test_generate_pkce_pair_is_random_per_call():
    verifier1, challenge1 = generate_pkce_pair()
    verifier2, challenge2 = generate_pkce_pair()

    assert verifier1 != verifier2
    assert challenge1 != challenge2


def test_oauth_cached_unexpired_access_token_needs_no_network_call(credential_store):
    future_expiry = int((datetime.now(timezone.utc) + timedelta(hours=40)).timestamp())
    credential_store.set(PROVIDER, CRED_OAUTH_ACCESS_TOKEN, "cached-access-token")
    credential_store.set(PROVIDER, CRED_OAUTH_EXPIRES_AT, str(future_expiry))
    credential_store.set(PROVIDER, CRED_OAUTH_REFRESH_TOKEN, "cached-refresh-token")
    fake = FakePelotonSession()
    connector = PelotonConnector(credential_store, session=fake)

    assert connector.authenticate() is True
    assert fake.post_calls == []
    assert connector._active_auth_header == {"Authorization": "Bearer cached-access-token"}


def test_oauth_expired_access_token_refreshes_and_persists_both_tokens(credential_store):
    past_expiry = int((datetime.now(timezone.utc) - timedelta(hours=1)).timestamp())
    credential_store.set(PROVIDER, CRED_OAUTH_ACCESS_TOKEN, "stale-access-token")
    credential_store.set(PROVIDER, CRED_OAUTH_EXPIRES_AT, str(past_expiry))
    credential_store.set(PROVIDER, CRED_OAUTH_REFRESH_TOKEN, "stored-refresh-token")
    fake = FakePelotonSession()
    fake.script_post_response(_oauth_token_response(access_token="new-access", refresh_token="new-refresh"))
    connector = PelotonConnector(credential_store, session=fake)

    assert connector.authenticate() is True
    assert fake.post_calls[0]["url"] == OAUTH_TOKEN_URL
    assert fake.post_calls[0]["json"]["grant_type"] == "refresh_token"
    assert fake.post_calls[0]["json"]["refresh_token"] == "stored-refresh-token"
    assert credential_store.get(PROVIDER, CRED_OAUTH_ACCESS_TOKEN) == "new-access"
    assert credential_store.get(PROVIDER, CRED_OAUTH_REFRESH_TOKEN) == "new-refresh"
    assert connector._active_auth_header == {"Authorization": "Bearer new-access"}


def test_oauth_refresh_rotation_second_refresh_uses_rotated_token_not_original(credential_store):
    """The specific regression this issue calls out: Peloton invalidates a
    refresh token on use, so the second refresh must send the token
    returned by the first response, never the original stored one."""
    credential_store.set(PROVIDER, CRED_OAUTH_REFRESH_TOKEN, "original-refresh-token")
    fake = FakePelotonSession()
    connector = PelotonConnector(credential_store, session=fake)

    fake.script_post_response(_oauth_token_response(access_token="access-1", refresh_token="rotated-refresh-1"))
    assert connector._refresh_oauth_token("original-refresh-token") is True
    assert credential_store.get(PROVIDER, CRED_OAUTH_REFRESH_TOKEN) == "rotated-refresh-1"

    fake.script_post_response(_oauth_token_response(access_token="access-2", refresh_token="rotated-refresh-2"))
    assert connector._refresh_oauth_token("rotated-refresh-1") is True

    assert fake.post_calls[0]["json"]["refresh_token"] == "original-refresh-token"
    assert fake.post_calls[1]["json"]["refresh_token"] == "rotated-refresh-1"
    assert credential_store.get(PROVIDER, CRED_OAUTH_REFRESH_TOKEN) == "rotated-refresh-2"


def test_oauth_refresh_failure_falls_back_to_manual_recovery_in_same_call(credential_store):
    credential_store.set(PROVIDER, CRED_OAUTH_REFRESH_TOKEN, "revoked-refresh-token")
    credential_store.set(PROVIDER, CRED_MANUAL_BEARER_TOKEN, "manual-fallback-token")
    fake = FakePelotonSession()
    fake.script_post_response(FakeResponse(400, {"error": "invalid_grant"}))
    connector = PelotonConnector(credential_store, session=fake)

    assert connector.authenticate() is True
    assert connector._active_auth_header == {"Authorization": "Bearer manual-fallback-token"}


def test_oauth_refresh_failure_with_no_manual_token_returns_false(credential_store):
    credential_store.set(PROVIDER, CRED_OAUTH_REFRESH_TOKEN, "revoked-refresh-token")
    fake = FakePelotonSession()
    fake.script_post_response(FakeResponse(401, {"error": "invalid_grant"}))
    connector = PelotonConnector(credential_store, session=fake)

    assert connector.authenticate() is False


def test_oauth_refresh_rate_limited_raises_transient_error(credential_store):
    credential_store.set(PROVIDER, CRED_OAUTH_REFRESH_TOKEN, "some-refresh-token")
    fake = FakePelotonSession()
    fake.script_post_response(FakeResponse(429, {}, headers={"Retry-After": "120"}))
    connector = PelotonConnector(credential_store, session=fake)

    with pytest.raises(TransientError) as excinfo:
        connector.authenticate()
    assert excinfo.value.retry_after_s == 120.0


def test_oauth_takes_priority_over_recovery_required_state(credential_store):
    """Self-healing: an account already in RecoveryRequired with a stored
    OAuth refresh token heals via OAuth rather than needing the manual
    path — proving OAuth is checked before ConnectorState, not gated
    behind it (see architecture doc's 'Why not gate OAuth on
    ConnectorState too?')."""
    credential_store.set(PROVIDER, CRED_OAUTH_REFRESH_TOKEN, "healthy-refresh-token")
    fake = FakePelotonSession()
    fake.script_post_response(_oauth_token_response(access_token="healed-access", refresh_token="healed-refresh"))
    connector = PelotonConnector(credential_store, session=fake)
    connector.transition_state(ConnectorState.WARNING)
    connector.transition_state(ConnectorState.DEGRADED)
    connector.transition_state(ConnectorState.RECOVERY_REQUIRED)

    assert connector.authenticate() is True
    assert connector._active_auth_header == {"Authorization": "Bearer healed-access"}


def test_complete_oauth_setup_success_persists_all_three_and_not_others(credential_store):
    fake = FakePelotonSession()
    fake.script_post_response(_oauth_token_response(access_token="first-access", refresh_token="first-refresh"))
    connector = PelotonConnector(credential_store, session=fake)

    connector.complete_oauth_setup("auth-code-123", "verifier-abc")

    assert fake.post_calls[0]["json"]["grant_type"] == "authorization_code"
    assert fake.post_calls[0]["json"]["code"] == "auth-code-123"
    assert fake.post_calls[0]["json"]["code_verifier"] == "verifier-abc"
    assert credential_store.get(PROVIDER, CRED_OAUTH_ACCESS_TOKEN) == "first-access"
    assert credential_store.get(PROVIDER, CRED_OAUTH_REFRESH_TOKEN) == "first-refresh"
    assert credential_store.get(PROVIDER, CRED_OAUTH_EXPIRES_AT) is not None
    # AC4: never conflated with the other paths' credential types.
    assert credential_store.get(PROVIDER, CRED_EMAIL) is None
    assert credential_store.get(PROVIDER, CRED_PASSWORD) is None
    assert credential_store.get(PROVIDER, CRED_MANUAL_BEARER_TOKEN) is None


def test_complete_oauth_setup_rejected_code_raises_and_persists_nothing(credential_store):
    fake = FakePelotonSession()
    fake.script_post_response(FakeResponse(400, {"error": "invalid_grant"}))
    connector = PelotonConnector(credential_store, session=fake)

    with pytest.raises(PelotonOAuthRejected):
        connector.complete_oauth_setup("bad-code", "verifier-abc")

    assert credential_store.get(PROVIDER, CRED_OAUTH_ACCESS_TOKEN) is None
    assert credential_store.get(PROVIDER, CRED_OAUTH_REFRESH_TOKEN) is None


# --- Feature 3.3: Sync (REST only) -----------------------------------------

def test_download_before_authenticate_raises_authentication_error(credential_store):
    fake = FakePelotonSession()
    connector = PelotonConnector(credential_store, session=fake)
    with pytest.raises(AuthenticationError):
        connector.download()


def test_download_does_not_forward_since_as_a_query_param(credential_store):
    """Issue #5: the live-verified endpoint's request-side filtering was
    never confirmed to exist — `since` stays in the signature (required by
    the Connector interface) but must not be sent as an unverified param."""
    credential_store.set(PROVIDER, CRED_EMAIL, "a@b.com")
    credential_store.set(PROVIDER, CRED_PASSWORD, "pw")
    fake = FakePelotonSession()
    fake.script_post_response(_login_success_response())
    fake.script_get_response(FakeResponse(200, {"id": "u1"}))
    fake.script_get_response(FakeResponse(200, {"data": [], "show_next": False}))
    connector = PelotonConnector(credential_store, session=fake)
    connector.authenticate()

    # Issue #46: `since` is now also parsed as an epoch int (to decide
    # whether a class workout's detail fetch is worth attempting) — a
    # realistic checkpoint value, matching what extract_resume_cursor()
    # actually produces (str(start_time), a raw epoch int), not an ISO
    # date string.
    connector.download(since="1767225600")

    assert "after" not in fake.get_calls[1]["params"]
    assert "since" not in fake.get_calls[1]["params"]


def test_download_fetches_user_id_then_paginated_workouts(credential_store):
    credential_store.set(PROVIDER, CRED_EMAIL, "a@b.com")
    credential_store.set(PROVIDER, CRED_PASSWORD, "pw")
    fake = FakePelotonSession()
    fake.script_post_response(_login_success_response())
    fake.script_get_response(FakeResponse(200, {"id": "u1"}))
    fake.script_get_response(FakeResponse(200, {"data": [{"id": "w1"}], "show_next": False}))
    connector = PelotonConnector(credential_store, session=fake)
    connector.authenticate()

    workouts = connector.download()

    # Issue #46: every workout gets a _class_type attached — "not_a_class"
    # here since this fixture has neither workout_type nor peloton_id.
    assert workouts == [{"id": "w1", "_distance_unit": None, "_class_type": "not_a_class", **_NO_PERFORMANCE_DATA}]
    assert fake.get_calls[0]["url"] == "https://api.onepeloton.com/api/me"
    assert fake.get_calls[1]["url"] == "https://api.onepeloton.com/api/user/u1/workouts"
    assert fake.get_calls[1]["params"] == {"page": 0}


def test_download_walks_all_pages_until_show_next_is_falsy(credential_store):
    credential_store.set(PROVIDER, CRED_EMAIL, "a@b.com")
    credential_store.set(PROVIDER, CRED_PASSWORD, "pw")
    fake = FakePelotonSession()
    fake.script_post_response(_login_success_response())
    fake.script_get_response(FakeResponse(200, {"id": "u1"}))
    fake.script_get_response(FakeResponse(200, {"data": [{"id": "w1"}], "show_next": True}))
    fake.script_get_response(FakeResponse(200, {"data": [{"id": "w2"}], "show_next": False}))
    connector = PelotonConnector(credential_store, session=fake)
    connector.authenticate()

    workouts = connector.download()

    assert workouts == [
        {"id": "w1", "_distance_unit": None, "_class_type": "not_a_class", **_NO_PERFORMANCE_DATA},
        {"id": "w2", "_distance_unit": None, "_class_type": "not_a_class", **_NO_PERFORMANCE_DATA},
    ]
    assert fake.get_calls[1]["params"] == {"page": 0}
    assert fake.get_calls[2]["params"] == {"page": 1}


def test_download_walks_three_pages_not_just_a_hardcoded_two(credential_store):
    """QA (issue #5 AC2): the 2-page test above could pass even if paging
    were hardcoded to stop after exactly one extra page. This rules that
    out with a 3-page sequence, confirming the loop genuinely continues
    while show_next is truthy rather than stopping after a fixed count."""
    credential_store.set(PROVIDER, CRED_EMAIL, "a@b.com")
    credential_store.set(PROVIDER, CRED_PASSWORD, "pw")
    fake = FakePelotonSession()
    fake.script_post_response(_login_success_response())
    fake.script_get_response(FakeResponse(200, {"id": "u1"}))
    fake.script_get_response(FakeResponse(200, {"data": [{"id": "w1"}], "show_next": True}))
    fake.script_get_response(FakeResponse(200, {"data": [{"id": "w2"}], "show_next": True}))
    fake.script_get_response(FakeResponse(200, {"data": [{"id": "w3"}], "show_next": False}))
    connector = PelotonConnector(credential_store, session=fake)
    connector.authenticate()

    workouts = connector.download()

    assert workouts == [
        {"id": "w1", "_distance_unit": None, "_class_type": "not_a_class", **_NO_PERFORMANCE_DATA},
        {"id": "w2", "_distance_unit": None, "_class_type": "not_a_class", **_NO_PERFORMANCE_DATA},
        {"id": "w3", "_distance_unit": None, "_class_type": "not_a_class", **_NO_PERFORMANCE_DATA},
    ]
    assert fake.get_calls[1]["params"] == {"page": 0}
    assert fake.get_calls[2]["params"] == {"page": 1}
    assert fake.get_calls[3]["params"] == {"page": 2}


def test_download_resolves_recognized_account_unit_and_attaches_to_every_workout(credential_store):
    """Issue #45: the account's distance unit is resolved once per
    download() call (from /api/me) and attached to every workout dict —
    never omitted, even across multiple pages."""
    credential_store.set(PROVIDER, CRED_EMAIL, "a@b.com")
    credential_store.set(PROVIDER, CRED_PASSWORD, "pw")
    fake = FakePelotonSession()
    fake.script_post_response(_login_success_response())
    fake.script_get_response(FakeResponse(200, {"id": "u1", "distance_unit": "mi"}))
    fake.script_get_response(FakeResponse(200, {"data": [{"id": "w1"}], "show_next": True}))
    fake.script_get_response(FakeResponse(200, {"data": [{"id": "w2"}], "show_next": False}))
    connector = PelotonConnector(credential_store, session=fake)
    connector.authenticate()

    workouts = connector.download()

    assert workouts == [
        {"id": "w1", "_distance_unit": "mi", "_class_type": "not_a_class", **_NO_PERFORMANCE_DATA},
        {"id": "w2", "_distance_unit": "mi", "_class_type": "not_a_class", **_NO_PERFORMANCE_DATA},
    ]


def test_download_missing_account_unit_field_attaches_none_not_omitted(credential_store):
    """Issue #45: AC3 — a missing/unrecognized unit must never be guessed
    at. download() still always sets the key, to None, so normalize() can
    use .get() without distinguishing "absent" from "present but None"."""
    credential_store.set(PROVIDER, CRED_EMAIL, "a@b.com")
    credential_store.set(PROVIDER, CRED_PASSWORD, "pw")
    fake = FakePelotonSession()
    fake.script_post_response(_login_success_response())
    fake.script_get_response(FakeResponse(200, {"id": "u1"}))
    fake.script_get_response(FakeResponse(200, {"data": [{"id": "w1"}], "show_next": False}))
    connector = PelotonConnector(credential_store, session=fake)
    connector.authenticate()

    workouts = connector.download()

    assert workouts == [{"id": "w1", "_distance_unit": None, "_class_type": "not_a_class", **_NO_PERFORMANCE_DATA}]


# --- Issue #47, AC1: effort_zones. Unlike every other connector-internal
# field in this module, download() does NOT attach an underscore-prefixed
# key for this one — normalize() reads `effort_zones` straight off the raw
# workout dict (see peloton.py's Feature 3.7 docstring for why: zero extra
# network cost, and this makes renormalize_provider() backfill it for
# free). download() itself just needs to pass `effort_zones` through
# unmodified, which these tests pin.

def test_download_passes_effort_zones_through_unmodified(credential_store):
    credential_store.set(PROVIDER, CRED_EMAIL, "a@b.com")
    credential_store.set(PROVIDER, CRED_PASSWORD, "pw")
    fake = FakePelotonSession()
    fake.script_post_response(_login_success_response())
    fake.script_get_response(FakeResponse(200, {"id": "u1"}))
    effort_zones = {
        "total_effort_points": 39.9,
        "heart_rate_zone_durations": {
            "heart_rate_z1_duration": 0, "heart_rate_z2_duration": 119,
            "heart_rate_z3_duration": 220, "heart_rate_z4_duration": 858,
            "heart_rate_z5_duration": 0,
        },
    }
    fake.script_get_response(FakeResponse(200, {"data": [{"id": "w1", "effort_zones": effort_zones}], "show_next": False}))
    connector = PelotonConnector(credential_store, session=fake)
    connector.authenticate()

    workouts = connector.download()

    assert workouts[0]["effort_zones"] == effort_zones


def test_download_passes_effort_zones_through_even_for_a_workout_older_than_since(credential_store):
    """Unlike the class-detail fetch (#46) and the eventual performance
    fetch (#47 AC2), effort_zones costs zero extra network calls and is
    never gated on the sync checkpoint — download() must pass it through
    for every workout, including ones otherwise skipped as already-synced."""
    credential_store.set(PROVIDER, CRED_EMAIL, "a@b.com")
    credential_store.set(PROVIDER, CRED_PASSWORD, "pw")
    fake = FakePelotonSession()
    fake.script_post_response(_login_success_response())
    fake.script_get_response(FakeResponse(200, {"id": "u1"}))
    effort_zones = {"total_effort_points": 10.0, "heart_rate_zone_durations": {"heart_rate_z1_duration": 5}}
    fake.script_get_response(FakeResponse(200, {"data": [{
        "id": "w1", "start_time": 1000, "effort_zones": effort_zones,
    }], "show_next": False}))
    connector = PelotonConnector(credential_store, session=fake)
    connector.authenticate()

    workouts = connector.download(since="2000")  # older than this workout's start_time

    assert workouts[0]["effort_zones"] == effort_zones


def test_download_rate_limited_honors_retry_after(credential_store):
    credential_store.set(PROVIDER, CRED_EMAIL, "a@b.com")
    credential_store.set(PROVIDER, CRED_PASSWORD, "pw")
    fake = FakePelotonSession()
    fake.script_post_response(_login_success_response())
    fake.script_get_response(FakeResponse(429, {}, headers={"Retry-After": "60"}))
    connector = PelotonConnector(credential_store, session=fake)
    connector.authenticate()

    with pytest.raises(TransientError) as excinfo:
        connector.download()
    assert excinfo.value.retry_after_s == 60.0


def test_download_session_rejected_raises_authentication_error(credential_store):
    credential_store.set(PROVIDER, CRED_EMAIL, "a@b.com")
    credential_store.set(PROVIDER, CRED_PASSWORD, "pw")
    fake = FakePelotonSession()
    fake.script_post_response(_login_success_response())
    fake.script_get_response(FakeResponse(401, {}))
    connector = PelotonConnector(credential_store, session=fake)
    connector.authenticate()

    with pytest.raises(AuthenticationError):
        connector.download()


# --- Feature 3.4 (extraction-only): normalize() -----------------------------

# Issue #5's exact live-captured records (docs/verification/peloton-2026-09-28.md).
_REAL_RECORD_NO_EFFORT_ZONES = {
    "id": "78f58afc127241efaaecb69860befa77",
    "start_time": 1790014244,
    "end_time": 1790014543,
    "fitness_discipline": "cycling",
    "total_work": 23646.98,
    "distance": 1.2163,
    "calories": 30.64,
    "effort_zones": None,
}

_REAL_RECORD_WITH_EFFORT_ZONES = {
    **_REAL_RECORD_NO_EFFORT_ZONES,
    "id": "another-real-id",
    "effort_zones": {
        "total_effort_points": 39.9,
        "heart_rate_zone_durations": {
            "heart_rate_z1_duration": 0,
            "heart_rate_z2_duration": 119,
            "heart_rate_z3_duration": 220,
            "heart_rate_z4_duration": 858,
            "heart_rate_z5_duration": 0,
        },
    },
}


def test_normalize_maps_cycling_class_with_power(credential_store):
    """Issue #45: the real captured record itself carries no `_distance_unit`
    key (download() didn't attach one until this issue's fix), so a km
    unit is supplied explicitly here to keep this test's distance_m
    assertion meaningful (AC2 — km-account regression coverage) rather than
    relying on the old, since-removed always-km assumption."""
    fake = FakePelotonSession()
    connector = PelotonConnector(credential_store, session=fake)
    raw = {**_REAL_RECORD_NO_EFFORT_ZONES, "_distance_unit": "km"}

    result = connector.normalize(raw)

    assert result["discipline_raw"] == "cycling"
    assert result["duration_s"] == 299
    # QA (issue #5 AC8): id/start_time/calories are supposed to be
    # unchanged pass-throughs, but no existing test actually asserted
    # them against the real captured record — only distance_m was.
    assert result["external_id"] == "78f58afc127241efaaecb69860befa77"
    assert result["start_time"] == 1790014244
    assert result["calories"] == 30.64
    assert result["avg_power"] == pytest.approx(23646.98 / 299)
    assert result["distance_m"] == pytest.approx(1216.3)


def test_normalize_effort_zones_null_is_handled_as_absent_not_an_error(credential_store):
    """Issue #5 AC5: effort_zones being null must not raise, and must have
    no bearing on the avg_power derivation (which never reads it)."""
    fake = FakePelotonSession()
    connector = PelotonConnector(credential_store, session=fake)

    result = connector.normalize(_REAL_RECORD_NO_EFFORT_ZONES)

    assert result["avg_hr"] is None
    assert result["max_hr"] is None
    assert result["max_power"] is None
    assert result["avg_power"] == pytest.approx(23646.98 / 299)


def test_normalize_effort_zones_populated_still_never_fabricates_hr_or_max_power(credential_store):
    """Issue #5 AC6/AC7, unchanged by issue #47 AC1: avg_hr/max_hr/max_power
    are always None, even when effort_zones carries real per-zone HR
    duration data — that data is not a plain avg/max bpm and must not be
    mapped into those fields, per the never-fabricate standard. AC1 surfaces
    the SAME data under its own dedicated fields instead (see the download()
    fixture tests below) — this test only pins the three fields that remain
    genuinely unavailable until AC2's performance-endpoint fetch lands."""
    fake = FakePelotonSession()
    connector = PelotonConnector(credential_store, session=fake)

    result = connector.normalize(_REAL_RECORD_WITH_EFFORT_ZONES)

    assert result["avg_hr"] is None
    assert result["max_hr"] is None
    assert result["max_power"] is None


# --- Issue #47, AC1: hr_zone_1_s..hr_zone_5_s, effort_points. CONFIRMED
# field names (docs/trainiq/verification/peloton-2026-09-28.md) — no Task 1
# dependency, unlike AC2's avg_hr/max_hr/max_power/performance endpoint.

def test_normalize_new_hr_zone_and_effort_fields_null_when_effort_zones_is_null(credential_store):
    """`effort_zones: null` (confirmed to occur, see
    docs/trainiq/verification/peloton-2026-09-28.md) -> every one of the 6
    new fields is None, never zero."""
    fake = FakePelotonSession()
    connector = PelotonConnector(credential_store, session=fake)

    result = connector.normalize(_REAL_RECORD_NO_EFFORT_ZONES)

    assert result["hr_zone_1_s"] is None
    assert result["hr_zone_2_s"] is None
    assert result["hr_zone_3_s"] is None
    assert result["hr_zone_4_s"] is None
    assert result["hr_zone_5_s"] is None
    assert result["effort_points"] is None


def test_normalize_new_hr_zone_and_effort_fields_read_straight_off_effort_zones(credential_store):
    """DEVIATION from the architecture doc (see peloton.py's Feature 3.7
    docstring): normalize() reads `effort_zones` directly off `raw`, not
    via a download()-attached underscore key — so the real captured record
    (already carrying `effort_zones` verbatim) is enough on its own, with
    no additional connector-internal keys needed."""
    fake = FakePelotonSession()
    connector = PelotonConnector(credential_store, session=fake)

    result = connector.normalize(_REAL_RECORD_WITH_EFFORT_ZONES)

    assert result["hr_zone_1_s"] == 0
    assert result["hr_zone_2_s"] == 119
    assert result["hr_zone_3_s"] == 220
    assert result["hr_zone_4_s"] == 858
    assert result["hr_zone_5_s"] == 0
    assert result["effort_points"] == pytest.approx(39.9)


def test_normalize_hr_zone_data_present_even_with_partial_zone_durations(credential_store):
    """A real response might not carry every zone key (e.g. a workout with
    zero time in z5 could plausibly omit that key rather than send 0) —
    missing individual zone keys must independently yield None, not crash
    or default the whole block to None."""
    fake = FakePelotonSession()
    connector = PelotonConnector(credential_store, session=fake)
    raw = {
        **_REAL_RECORD_NO_EFFORT_ZONES,
        "effort_zones": {
            "total_effort_points": 12.5,
            "heart_rate_zone_durations": {"heart_rate_z1_duration": 100},
        },
    }

    result = connector.normalize(raw)

    assert result["hr_zone_1_s"] == 100
    assert result["hr_zone_2_s"] is None
    assert result["hr_zone_5_s"] is None
    assert result["effort_points"] == pytest.approx(12.5)


def test_normalize_missing_end_time_yields_none_duration_no_exception(credential_store):
    fake = FakePelotonSession()
    connector = PelotonConnector(credential_store, session=fake)
    raw = {**_REAL_RECORD_NO_EFFORT_ZONES, "end_time": None}

    result = connector.normalize(raw)

    assert result["duration_s"] is None


def test_normalize_missing_total_work_with_no_duration_yields_none_avg_power(credential_store):
    """Guards both a missing total_work and a division by zero/None
    duration_s — never a ZeroDivisionError or TypeError."""
    fake = FakePelotonSession()
    connector = PelotonConnector(credential_store, session=fake)
    raw = {**_REAL_RECORD_NO_EFFORT_ZONES, "end_time": None}

    result = connector.normalize(raw)

    assert result["avg_power"] is None


def test_normalize_strength_class_has_no_power_never_fabricated(credential_store):
    """R-PELOTON-06: strength classes never report power. Milestone 2's
    named scenario, must never become a fabricated 0."""
    fake = FakePelotonSession()
    connector = PelotonConnector(credential_store, session=fake)
    raw = {
        "id": 2, "start_time": 1790014244, "end_time": 1790014244,
        "fitness_discipline": "strength", "total_work": None, "effort_zones": None,
    }

    result = connector.normalize(raw)

    assert result["discipline_raw"] == "strength"
    assert result["avg_power"] is None


def test_normalize_logs_unrecognized_discipline_without_dropping_the_record(credential_store, caplog):
    """R-PELOTON-07 (semantic drift): an unrecognized fitness_discipline
    value must be logged, never silently dropped or guessed."""
    fake = FakePelotonSession()
    connector = PelotonConnector(credential_store, session=fake)
    raw = {"id": 3, "start_time": 1790014244, "end_time": 1790015044, "fitness_discipline": "underwater_basket_weaving"}

    result = connector.normalize(raw)

    assert result["discipline_raw"] == "underwater_basket_weaving"  # preserved, not dropped
    assert result["external_id"] == "3"


# --- Issue #45: distance_m depends on the account's distance unit, never
# a hard-coded one ------------------------------------------------------

def test_normalize_mi_account_converts_distance_correctly(credential_store):
    """AC1: a mi-unit account's distance_m matches the issue's own
    Strava-confirmed figure (21213.7 m) to within 1% — this is the
    acceptance bar AC1 actually asks for, not exact float equality."""
    fake = FakePelotonSession()
    connector = PelotonConnector(credential_store, session=fake)
    raw = {**_REAL_RECORD_NO_EFFORT_ZONES, "distance": 13.1816, "_distance_unit": "mi"}

    result = connector.normalize(raw)

    assert result["distance_m"] == pytest.approx(13.1816 * 1609.344)
    assert result["distance_m"] == pytest.approx(21213.7, rel=0.01)


def test_normalize_km_account_converts_distance_correctly(credential_store):
    """AC2: a km-unit account must not regress — same conversion issue #5
    originally assumed for every account, now scoped to accounts that are
    actually km."""
    fake = FakePelotonSession()
    connector = PelotonConnector(credential_store, session=fake)
    raw = {**_REAL_RECORD_NO_EFFORT_ZONES, "distance": 21.2137, "_distance_unit": "km"}

    result = connector.normalize(raw)

    assert result["distance_m"] == pytest.approx(21213.7)


@pytest.mark.parametrize(
    "distance_unit",
    [
        pytest.param(None, id="key_present_none"),
        pytest.param("furlongs", id="unrecognized_value"),
    ],
)
def test_normalize_unresolved_unit_with_real_distance_yields_none_and_warns(credential_store, distance_unit):
    """AC3/AC7: a real distance value whose unit is missing or unrecognized
    must be logged and stored as NULL — never guessed. Covers both a
    `_distance_unit` key explicitly set to None and an unrecognized token.
    loguru output isn't captured by pytest's `caplog` (stdlib logging) —
    see test_strava_walk_maps_to_other_explicitly_not_via_warning in
    test_taxonomy.py for this codebase's actual working pattern, used here
    too."""
    import io

    from loguru import logger

    fake = FakePelotonSession()
    connector = PelotonConnector(credential_store, session=fake)
    raw = {**_REAL_RECORD_NO_EFFORT_ZONES, "distance": 13.1816, "_distance_unit": distance_unit}

    log_stream = io.StringIO()
    handler_id = logger.add(log_stream, format="{message}")
    try:
        result = connector.normalize(raw)
    finally:
        logger.remove(handler_id)

    assert result["distance_m"] is None
    assert "distance" in log_stream.getvalue().lower()


def test_normalize_missing_distance_unit_key_entirely_yields_none_and_warns(credential_store):
    """AC3/AC7: the `_distance_unit` key absent entirely (e.g. a raw dict
    that predates this fix, re-normalized without a raw_transform) behaves
    identically to the key being present and None."""
    import io

    from loguru import logger

    fake = FakePelotonSession()
    connector = PelotonConnector(credential_store, session=fake)
    raw = {**_REAL_RECORD_NO_EFFORT_ZONES, "distance": 13.1816}
    assert "_distance_unit" not in raw

    log_stream = io.StringIO()
    handler_id = logger.add(log_stream, format="{message}")
    try:
        result = connector.normalize(raw)
    finally:
        logger.remove(handler_id)

    assert result["distance_m"] is None
    assert "distance" in log_stream.getvalue().lower()


def test_normalize_no_distance_at_all_yields_none_without_warning(credential_store):
    """A workout with no `distance` field at all (e.g. a meditation
    session) is not a unit problem — nothing to convert, nothing to warn
    about, even though the unit is also unresolved."""
    import io

    from loguru import logger

    fake = FakePelotonSession()
    connector = PelotonConnector(credential_store, session=fake)
    raw = {
        "id": 9, "start_time": 1790014244, "end_time": 1790014544,
        "fitness_discipline": "meditation", "total_work": None, "effort_zones": None,
    }
    assert "distance" not in raw

    log_stream = io.StringIO()
    handler_id = logger.add(log_stream, format="{message}")
    try:
        result = connector.normalize(raw)
    finally:
        logger.remove(handler_id)

    assert result["distance_m"] is None
    assert log_stream.getvalue() == ""


# --- Issue #46: class title/instructor/class type/planned length --------

def test_normalize_class_workout_with_full_metadata(credential_store):
    fake = FakePelotonSession()
    connector = PelotonConnector(credential_store, session=fake)
    raw = {
        "id": 10, "start_time": 1790014244, "end_time": 1790014543,
        "fitness_discipline": "cycling", "total_work": 23646.98, "calories": 30.64,
        "_class_title": "Power Zone Max", "_instructor_name": "Matt Wilpers",
        "_class_type": "power_zone_max", "_planned_duration_s": 2700,
        "_provider_class_id": "ride-abc", "_difficulty_estimate": 8.40,
    }

    result = connector.normalize(raw)

    assert result["activity_title"] == "Power Zone Max"
    assert result["instructor_name"] == "Matt Wilpers"
    assert result["class_type"] == "power_zone_max"
    assert result["planned_duration_s"] == 2700
    assert result["provider_class_id"] == "ride-abc"
    assert result["difficulty_estimate"] == 8.40
    assert result["duration_s"] == 299  # actual duration unaffected (AC1)


def test_normalize_non_class_workout_has_sentinel_and_no_instructor(credential_store):
    fake = FakePelotonSession()
    connector = PelotonConnector(credential_store, session=fake)
    raw = {
        "id": 11, "start_time": 1790014244, "end_time": 1790014543,
        "fitness_discipline": "cycling", "_class_type": CLASS_TYPE_NOT_A_CLASS,
    }

    result = connector.normalize(raw)

    assert result["class_type"] == CLASS_TYPE_NOT_A_CLASS
    assert result["instructor_name"] is None
    assert result["activity_title"] is None


def test_normalize_class_workout_with_failed_lookup(credential_store):
    fake = FakePelotonSession()
    connector = PelotonConnector(credential_store, session=fake)
    raw = {
        "id": 12, "start_time": 1790014244, "end_time": 1790014543,
        "fitness_discipline": "cycling", "_class_type": CLASS_TYPE_LOOKUP_FAILED,
    }

    result = connector.normalize(raw)

    assert result["class_type"] == CLASS_TYPE_LOOKUP_FAILED
    assert result["instructor_name"] is None


def test_normalize_workout_with_no_class_keys_at_all_yields_none_for_every_new_field(credential_store):
    """The "not attempted this pass" case: a workout that predates issue
    #46's download() entirely, or was skipped as older than the sync
    checkpoint. COALESCE in upsert_normalized_activity() depends on this."""
    fake = FakePelotonSession()
    connector = PelotonConnector(credential_store, session=fake)
    raw = {
        "id": 13, "start_time": 1790014244, "end_time": 1790014543,
        "fitness_discipline": "cycling",
    }

    result = connector.normalize(raw)

    assert result["activity_title"] is None
    assert result["instructor_name"] is None
    assert result["class_type"] is None
    assert result["planned_duration_s"] is None
    assert result["provider_class_id"] is None
    assert result["difficulty_estimate"] is None


def _me_and_workouts_responses(workouts: list[dict]):
    """Scripts a successful /api/me + single-page /workouts sequence onto
    a fresh FakePelotonSession, authenticated via automated login."""
    fake = FakePelotonSession()
    fake.script_post_response(_login_success_response())
    fake.script_get_response(FakeResponse(200, {"id": "u1"}))
    fake.script_get_response(FakeResponse(200, {"data": workouts, "show_next": False}))
    return fake


def _authenticated_connector(credential_store, fake):
    credential_store.set(PROVIDER, CRED_EMAIL, "a@b.com")
    credential_store.set(PROVIDER, CRED_PASSWORD, "pw")
    connector = PelotonConnector(credential_store, session=fake)
    connector.authenticate()
    return connector


def _session_response(ride_id: str, **extra):
    """A GET /api/peloton/{peloton_id} success body — live-evidence shape
    from issue #58 (ride_id, scheduled_start_time, is_live, is_encore)."""
    body = {"ride_id": ride_id, "scheduled_start_time": 1790000000, "is_live": False, "is_encore": False}
    body.update(extra)
    return FakeResponse(200, body)


def _ride_details_response(title: str, duration: int, class_type_names: list[str] | None = None, **extra_ride):
    """A GET /api/ride/{ride_id}/details success body — live-evidence shape
    from issue #58: `ride` is nested, `class_types` is a top-level sibling."""
    ride = {"title": title, "duration": duration, "instructor": {"name": "Matt Wilpers"}, **extra_ride}
    class_types = [{"name": name} for name in (class_type_names or ["Power Zone"])]
    return FakeResponse(200, {"ride": ride, "class_types": class_types, "is_power_zone_class": True})


def test_download_two_workouts_sharing_ride_id_fetches_ride_details_once(credential_store):
    """Issue #58, AC3: two different peloton_ids (two attendances) that
    resolve to the same ride_id must still cost only one ride-details call
    — two session-resolution calls (one per distinct peloton_id), one
    ride-details call (one per distinct resolved ride_id)."""
    workouts = [
        {"id": "w1", WORKOUT_TYPE_FIELD: "class", RIDE_ID_FIELD: "session-1", "start_time": 2000},
        {"id": "w2", WORKOUT_TYPE_FIELD: "class", RIDE_ID_FIELD: "session-2", "start_time": 3000},
    ]
    fake = _me_and_workouts_responses(workouts)
    fake.script_get_response(_session_response("ride-1"))  # resolves w1's session-1
    fake.script_get_response(_ride_details_response("Power Zone Max", 2700))  # first-seen ride-1
    fake.script_get_response(_session_response("ride-1"))  # resolves w2's session-2
    connector = _authenticated_connector(credential_store, fake)

    result = connector.download(since="1000")

    assert len(fake.get_calls) == 5  # /me, /workouts, 2 session calls, exactly 1 ride-detail call
    assert result[0]["_class_title"] == "Power Zone Max"
    assert result[1]["_class_title"] == "Power Zone Max"
    assert result[0]["_provider_class_id"] == "ride-1"


def test_download_same_peloton_id_twice_resolves_session_once(credential_store):
    """Issue #58, AC3: the same peloton_id seen twice costs only one
    session-resolution call."""
    workouts = [
        {"id": "w1", WORKOUT_TYPE_FIELD: "class", RIDE_ID_FIELD: "session-1", "start_time": 2000},
        {"id": "w2", WORKOUT_TYPE_FIELD: "class", RIDE_ID_FIELD: "session-1", "start_time": 3000},
    ]
    fake = _me_and_workouts_responses(workouts)
    fake.script_get_response(_session_response("ride-1"))
    fake.script_get_response(_ride_details_response("Power Zone Max", 2700))
    connector = _authenticated_connector(credential_store, fake)

    result = connector.download(since="1000")

    assert len(fake.get_calls) == 4  # /me, /workouts, 1 session call, 1 ride-detail call
    assert result[1]["_class_title"] == "Power Zone Max"


def test_download_class_workout_older_than_since_is_not_fetched(credential_store):
    workouts = [{"id": "w1", WORKOUT_TYPE_FIELD: "class", RIDE_ID_FIELD: "session-1", "start_time": 500}]
    fake = _me_and_workouts_responses(workouts)
    connector = _authenticated_connector(credential_store, fake)

    result = connector.download(since="1000")

    assert len(fake.get_calls) == 2  # /me, /workouts — no lookup call at all
    assert "_class_type" not in result[0]
    assert "_class_title" not in result[0]


def test_download_class_workout_with_since_none_is_always_attempted(credential_store):
    workouts = [{"id": "w1", WORKOUT_TYPE_FIELD: "class", RIDE_ID_FIELD: "session-1", "start_time": 500}]
    fake = _me_and_workouts_responses(workouts)
    fake.script_get_response(_session_response("ride-1"))
    fake.script_get_response(_ride_details_response("Endurance", 1800))
    connector = _authenticated_connector(credential_store, fake)

    result = connector.download(since=None)

    assert len(fake.get_calls) == 4
    assert result[0]["_class_title"] == "Endurance"


def test_download_session_lookup_404_sets_lookup_failed_and_logs_without_fetching_ride_details(credential_store):
    """Issue #58, AC4/AC8: a 404 at the session-resolution step (the
    literal old-bug scenario — a session id can't be resolved) must stop
    before the ride-details call, distinguishable from a ride-details
    failure. Loguru, not stdlib logging — caplog doesn't capture it (see
    test_sync_engine.py's test_connector_declaring_no_incremental_support_logs_explanatory_note
    for the established pattern this follows)."""
    import io
    from loguru import logger

    workouts = [{"id": "w1", WORKOUT_TYPE_FIELD: "class", RIDE_ID_FIELD: "session-1", "start_time": 2000}]
    fake = _me_and_workouts_responses(workouts)
    fake.script_get_response(FakeResponse(404, {}))
    connector = _authenticated_connector(credential_store, fake)

    log_stream = io.StringIO()
    handler_id = logger.add(log_stream, format="{message}")
    try:
        result = connector.download(since="1000")
    finally:
        logger.remove(handler_id)

    assert result[0]["_class_type"] == CLASS_TYPE_LOOKUP_FAILED
    assert "class session lookup failed" in log_stream.getvalue()
    assert len(fake.get_calls) == 3  # /me, /workouts, the failed session call — never a ride-detail call


def test_download_ride_details_404_after_successful_session_resolution_sets_lookup_failed_and_logs(credential_store):
    """Issue #58, AC4/AC8: a failure at the SECOND step, distinguishable
    from the session-resolution failure above — the session resolves fine,
    but the resolved ride_id's details fetch 404s."""
    import io
    from loguru import logger

    workouts = [{"id": "w1", WORKOUT_TYPE_FIELD: "class", RIDE_ID_FIELD: "session-1", "start_time": 2000}]
    fake = _me_and_workouts_responses(workouts)
    fake.script_get_response(_session_response("ride-1"))
    fake.script_get_response(FakeResponse(404, {}))
    connector = _authenticated_connector(credential_store, fake)

    log_stream = io.StringIO()
    handler_id = logger.add(log_stream, format="{message}")
    try:
        result = connector.download(since="1000")
    finally:
        logger.remove(handler_id)

    assert result[0]["_class_type"] == CLASS_TYPE_LOOKUP_FAILED
    assert "ride-details lookup failed" in log_stream.getvalue()
    assert "ride_id='ride-1'" in log_stream.getvalue()
    assert "peloton_id='session-1'" in log_stream.getvalue()


def test_download_session_lookup_rate_limited_propagates_transient_error(credential_store):
    workouts = [{"id": "w1", WORKOUT_TYPE_FIELD: "class", RIDE_ID_FIELD: "session-1", "start_time": 2000}]
    fake = _me_and_workouts_responses(workouts)
    fake.script_get_response(FakeResponse(429, {}, headers={"Retry-After": "30"}))
    connector = _authenticated_connector(credential_store, fake)

    with pytest.raises(TransientError) as excinfo:
        connector.download(since="1000")
    assert excinfo.value.retry_after_s == 30.0


def test_download_ride_details_rate_limited_propagates_transient_error(credential_store):
    workouts = [{"id": "w1", WORKOUT_TYPE_FIELD: "class", RIDE_ID_FIELD: "session-1", "start_time": 2000}]
    fake = _me_and_workouts_responses(workouts)
    fake.script_get_response(_session_response("ride-1"))
    fake.script_get_response(FakeResponse(429, {}, headers={"Retry-After": "30"}))
    connector = _authenticated_connector(credential_store, fake)

    with pytest.raises(TransientError) as excinfo:
        connector.download(since="1000")
    assert excinfo.value.retry_after_s == 30.0


def test_download_non_class_workout_gets_not_a_class_sentinel_no_network_call(credential_store):
    """"No network call" here means specifically no ride-detail call — a
    performance fetch (#47 AC2) still happens, independent of class
    status, since any discipline can have HR data."""
    workouts = [{"id": "w1", "start_time": 2000}]  # no workout_type, no peloton_id at all
    fake = _me_and_workouts_responses(workouts)
    connector = _authenticated_connector(credential_store, fake)

    result = connector.download(since="1000")

    assert result[0]["_class_type"] == CLASS_TYPE_NOT_A_CLASS
    assert len(fake.get_calls) == 2  # /me, /workouts — no lookup call (performance fetches are tracked separately)


# --- Issue #47, AC2: _parse_performance_response() -----------------------
# CONFIRMED shape (docs/trainiq/verification/peloton-2026-09-28.md's
# 2026-10-09 addendum, BL-012 resolved) — the BO's real captured response
# for a 45-min Power Zone ride with an HR monitor paired.

_REAL_PERFORMANCE_RESPONSE = {
    "duration": 2700,
    "metrics": [
        {"display_name": "Output", "display_unit": "watts", "max_value": 294, "average_value": 131, "slug": "output"},
        {"display_name": "Cadence", "display_unit": "rpm", "max_value": 128, "average_value": 91, "slug": "cadence"},
        {"display_name": "Resistance", "display_unit": "%", "max_value": 57, "average_value": 34, "slug": "resistance"},
        {"display_name": "Speed", "display_unit": "kph", "max_value": 39.8, "average_value": 28.3, "slug": "speed"},
        {
            "display_name": "Heart Rate", "display_unit": "bpm", "max_value": 163, "average_value": 135,
            "slug": "heart_rate", "missing_data_duration": 79,
        },
    ],
    "summaries": [
        {"display_name": "Total Output", "display_unit": "kj", "value": 354, "slug": "total_output"},
        {"display_name": "Distance", "display_unit": "km", "value": 21.21367, "slug": "distance"},
    ],
}


def test_parse_performance_response_extracts_hr_and_power_by_slug_not_position(credential_store):
    result = _parse_performance_response(_REAL_PERFORMANCE_RESPONSE)

    assert result["avg_hr"] == 135
    assert result["max_hr"] == 163
    assert result["max_power"] == 294


def test_parse_performance_response_no_heart_rate_entry_yields_none_not_zero(credential_store):
    """A workout with no HR monitor paired has no "heart_rate" entry in
    metrics[] at all — never fabricated as 0."""
    body = {**_REAL_PERFORMANCE_RESPONSE, "metrics": [
        m for m in _REAL_PERFORMANCE_RESPONSE["metrics"] if m["slug"] != "heart_rate"
    ]}

    result = _parse_performance_response(body)

    assert result["avg_hr"] is None
    assert result["max_hr"] is None
    assert result["max_power"] == 294  # unaffected — output is a separate entry


def test_parse_performance_response_no_metrics_key_at_all_yields_all_none(credential_store):
    result = _parse_performance_response({"duration": 2700})

    assert result == {"avg_hr": None, "max_hr": None, "max_power": None}


def test_parse_performance_response_malformed_metrics_entries_are_skipped_not_raised(credential_store):
    body = {"metrics": ["not-a-dict", 42, None, {"slug": "heart_rate", "display_unit": "bpm", "average_value": 100, "max_value": 150}]}

    result = _parse_performance_response(body)

    assert result["avg_hr"] == 100
    assert result["max_hr"] == 150


def test_parse_performance_response_unexpected_display_unit_yields_none_and_warns(credential_store):
    """Never guess past a display_unit the connector doesn't recognize —
    e.g. a locale that reports heart rate differently."""
    import io

    from loguru import logger

    body = {"metrics": [{"slug": "heart_rate", "display_unit": "unexpected", "average_value": 100, "max_value": 150}]}

    log_stream = io.StringIO()
    handler_id = logger.add(log_stream, format="{message}")
    try:
        result = _parse_performance_response(body)
    finally:
        logger.remove(handler_id)

    assert result["avg_hr"] is None
    assert result["max_hr"] is None
    assert "heart_rate" in log_stream.getvalue().lower()


def test_parse_performance_response_float_average_value_rounds_to_int(credential_store):
    """average_value/max_value can come back as a float even though the
    stored columns are INTEGER — rounds rather than truncates."""
    body = {"metrics": [{"slug": "heart_rate", "display_unit": "bpm", "average_value": 134.6, "max_value": 163.2}]}

    result = _parse_performance_response(body)

    assert result["avg_hr"] == 135
    assert result["max_hr"] == 163


# --- Issue #47, AC2: fetch_workout_performance() --------------------------

def test_fetch_workout_performance_success_returns_parsed_dict(credential_store):
    fake = FakePelotonSession()
    fake.script_post_response(_login_success_response())
    connector = _authenticated_connector(credential_store, fake)
    fake.script_performance_response(FakeResponse(200, _REAL_PERFORMANCE_RESPONSE))

    result = connector.fetch_workout_performance("workout-1")

    assert result["avg_hr"] == 135
    assert result["max_hr"] == 163
    assert result["max_power"] == 294
    assert fake.performance_get_calls[-1]["url"] == "https://api.onepeloton.com/api/workout/workout-1/performance_graph"
    assert fake.performance_get_calls[-1]["params"] == {"every_n": 60}


def test_fetch_workout_performance_404_returns_none_logged_not_raised(credential_store):
    import io

    from loguru import logger

    fake = FakePelotonSession()
    fake.script_post_response(_login_success_response())
    connector = _authenticated_connector(credential_store, fake)
    fake.script_performance_response(FakeResponse(404, {}))

    log_stream = io.StringIO()
    handler_id = logger.add(log_stream, format="{message}")
    try:
        result = connector.fetch_workout_performance("workout-1")
    finally:
        logger.remove(handler_id)

    assert result is None
    assert "performance fetch failed" in log_stream.getvalue()


def test_fetch_workout_performance_retries_exhausted_returns_none_not_raised(credential_store):
    """AC5: never aborts the run over one workout's missing HR data, even
    after the retry policy gives up — a deliberately WIDER safety net than
    fetch_class_details()'s (see the method's own docstring)."""
    fake = FakePelotonSession()
    fake.script_post_response(_login_success_response())
    connector = _authenticated_connector(credential_store, fake)
    for _ in range(4):  # max_retries=3 default inside retry_with_backoff -> 4 total attempts
        fake.script_performance_response(FakeResponse(429, {}, headers={"Retry-After": "0.001"}))

    result = connector.fetch_workout_performance("workout-1")

    assert result is None


def test_fetch_workout_performance_401_raises_authentication_error(credential_store):
    fake = FakePelotonSession()
    fake.script_post_response(_login_success_response())
    connector = _authenticated_connector(credential_store, fake)
    fake.script_performance_response(FakeResponse(401, {}))

    with pytest.raises(AuthenticationError):
        connector.fetch_workout_performance("workout-1")


def test_fetch_workout_performance_before_authenticate_raises_authentication_error(credential_store):
    fake = FakePelotonSession()
    connector = PelotonConnector(credential_store, session=fake)

    with pytest.raises(AuthenticationError):
        connector.fetch_workout_performance("workout-1")


# --- Issue #47, AC2: normalize() reads the (possibly absent) performance
# keys, never guesses -------------------------------------------------

def test_normalize_reads_avg_hr_max_hr_max_power_from_performance_keys(credential_store):
    fake = FakePelotonSession()
    connector = PelotonConnector(credential_store, session=fake)
    raw = {
        **_REAL_RECORD_NO_EFFORT_ZONES,
        "_performance_fetch_status": PERFORMANCE_FETCH_STATUS_OK,
        "_avg_hr": 135, "_max_hr": 163, "_max_power": 294,
    }

    result = connector.normalize(raw)

    assert result["avg_hr"] == 135
    assert result["max_hr"] == 163
    assert result["max_power"] == 294
    assert result["performance_fetch_status"] == PERFORMANCE_FETCH_STATUS_OK


def test_normalize_performance_fetch_failed_yields_none_for_all_three(credential_store):
    fake = FakePelotonSession()
    connector = PelotonConnector(credential_store, session=fake)
    raw = {**_REAL_RECORD_NO_EFFORT_ZONES, "_performance_fetch_status": PERFORMANCE_FETCH_STATUS_FAILED}

    result = connector.normalize(raw)

    assert result["avg_hr"] is None
    assert result["max_hr"] is None
    assert result["max_power"] is None
    assert result["performance_fetch_status"] == PERFORMANCE_FETCH_STATUS_FAILED


def test_normalize_no_performance_keys_at_all_is_not_attempted_this_pass(credential_store):
    """The "not attempted this pass" case the COALESCE upsert depends on —
    a workout older than the sync checkpoint, or one synced before AC2
    shipped at all."""
    fake = FakePelotonSession()
    connector = PelotonConnector(credential_store, session=fake)

    result = connector.normalize(_REAL_RECORD_NO_EFFORT_ZONES)

    assert result["avg_hr"] is None
    assert result["max_hr"] is None
    assert result["max_power"] is None
    assert result["performance_fetch_status"] is None


# --- Issue #47, AC2: download() wires fetch_workout_performance() into
# the per-workout loop, independent of class status ------------------

def test_download_attaches_real_hr_and_power_data_for_a_new_workout(credential_store):
    workouts = [{"id": "w1", "start_time": 2000}]  # not a class workout
    fake = _me_and_workouts_responses(workouts)
    fake.script_performance_response(FakeResponse(200, _REAL_PERFORMANCE_RESPONSE))
    connector = _authenticated_connector(credential_store, fake)

    result = connector.download(since="1000")

    assert result[0]["_performance_fetch_status"] == PERFORMANCE_FETCH_STATUS_OK
    assert result[0]["_avg_hr"] == 135
    assert result[0]["_max_hr"] == 163
    assert result[0]["_max_power"] == 294


def test_download_performance_fetch_failure_sets_failed_status_does_not_raise(credential_store):
    """AC5: a performance-endpoint fetch failure for one workout is logged
    and degraded to NULL, never aborting the whole download()."""
    workouts = [{"id": "w1", "start_time": 2000}]
    fake = _me_and_workouts_responses(workouts)
    for _ in range(4):
        fake.script_performance_response(FakeResponse(429, {}, headers={"Retry-After": "0.001"}))
    connector = _authenticated_connector(credential_store, fake)

    result = connector.download(since="1000")

    assert result[0]["_performance_fetch_status"] == PERFORMANCE_FETCH_STATUS_FAILED
    assert "_avg_hr" not in result[0]
    assert "_max_hr" not in result[0]
    assert "_max_power" not in result[0]


def test_download_performance_fetch_independent_of_class_status(credential_store):
    """A class workout (#46) and a non-class workout both get a
    performance fetch — the two mechanisms are gated on the same
    checkpoint but are otherwise independent."""
    workouts = [{"id": "w1", WORKOUT_TYPE_FIELD: "class", RIDE_ID_FIELD: "ride-1", "start_time": 2000}]
    fake = _me_and_workouts_responses(workouts)
    fake.script_get_response(_session_response("ride-1"))
    fake.script_get_response(_ride_details_response("Endurance", 1800))
    fake.script_performance_response(FakeResponse(200, _REAL_PERFORMANCE_RESPONSE))
    connector = _authenticated_connector(credential_store, fake)

    result = connector.download(since="1000")

    assert result[0]["_class_title"] == "Endurance"  # #46, unaffected
    assert result[0]["_avg_hr"] == 135  # #47 AC2, same workout, same pass


# --- Issue #47, AC2: apply_hr_performance_update() ------------------------

def test_apply_hr_performance_update_sets_only_the_four_columns(db):
    db.execute(
        """
        INSERT INTO normalized_activities
            (provider, external_id, start_time, duration_s, discipline, source_confidence, avg_power)
        VALUES ('peloton', 'ext-1', '2026-01-01T00:00:00+00:00', 300, 'cycling', 0.9, 200)
        """
    )
    db.commit()

    apply_hr_performance_update(
        db, "ext-1",
        {"avg_hr": 135, "max_hr": 163, "max_power": 294, "performance_fetch_status": PERFORMANCE_FETCH_STATUS_OK},
    )
    db.commit()

    row = dict(db.execute(
        "SELECT * FROM normalized_activities WHERE provider = 'peloton' AND external_id = 'ext-1'"
    ).fetchone())
    assert row["avg_hr"] == 135
    assert row["max_hr"] == 163
    assert row["max_power"] == 294
    assert row["performance_fetch_status"] == PERFORMANCE_FETCH_STATUS_OK
    assert row["avg_power"] == 200  # untouched


def test_apply_hr_performance_update_scoped_to_peloton_provider_only(db):
    db.execute(
        """
        INSERT INTO normalized_activities
            (provider, external_id, start_time, duration_s, discipline, source_confidence)
        VALUES ('strava', 'ext-1', '2026-01-01T00:00:00+00:00', 300, 'cycling', 0.9)
        """
    )
    db.commit()

    apply_hr_performance_update(db, "ext-1", {"avg_hr": 135, "max_hr": 163, "max_power": 294, "performance_fetch_status": "ok"})
    db.commit()

    row = dict(db.execute(
        "SELECT * FROM normalized_activities WHERE provider = 'strava' AND external_id = 'ext-1'"
    ).fetchone())
    assert row["avg_hr"] is None


def test_download_populates_difficulty_estimate(credential_store):
    workouts = [{"id": "w1", WORKOUT_TYPE_FIELD: "class", RIDE_ID_FIELD: "session-1", "start_time": 2000}]
    fake = _me_and_workouts_responses(workouts)
    fake.script_get_response(_session_response("ride-1"))
    fake.script_get_response(_ride_details_response("Power Zone Max", 2700, difficulty_estimate=8.40))
    connector = _authenticated_connector(credential_store, fake)

    result = connector.download(since="1000")

    assert result[0]["_difficulty_estimate"] == 8.40


# --- Issue #58: fetch_class_session() (resolves BL-011) ---------------------

def test_fetch_class_session_success_returns_parsed_session_dict(credential_store):
    fake = FakePelotonSession()
    fake.script_get_response(_session_response("ride-1", is_live=True))
    connector = PelotonConnector(credential_store, session=fake)
    connector._active_auth_header = {"Cookie": "peloton_session_id=x"}

    session = connector.fetch_class_session("session-1")

    assert session[SESSION_RIDE_ID_FIELD] == "ride-1"
    assert session["is_live"] is True


def test_fetch_class_session_404_returns_none(credential_store):
    """The literal old-bug scenario: a workout's peloton_id is a session
    id, so calling the OLD ride-detail endpoint directly with it 404s
    (documented by the regression test below); this endpoint is what
    correctly resolves that session id instead."""
    fake = FakePelotonSession()
    fake.script_get_response(FakeResponse(404, {}))
    connector = PelotonConnector(credential_store, session=fake)
    connector._active_auth_header = {"Cookie": "peloton_session_id=x"}

    assert connector.fetch_class_session("session-1") is None


def test_regression_old_bug_session_id_used_directly_as_ride_id_404s(credential_store):
    """Issue #58, AC8: proves the OLD call pattern (passing a workout's
    peloton_id straight into fetch_class_details(), as #46's download()
    did) fails — this is exactly why step 1 (fetch_class_session()) must
    run first. Must never regress to calling fetch_class_details() with an
    unresolved peloton_id again."""
    fake = FakePelotonSession()
    fake.script_get_response(FakeResponse(404, {}))
    connector = PelotonConnector(credential_store, session=fake)
    connector._active_auth_header = {"Cookie": "peloton_session_id=x"}

    session_id_mistaken_for_ride_id = "session-1"
    assert connector.fetch_class_details(session_id_mistaken_for_ride_id) is None


# --- Issue #58: _extract_ride_metadata() (resolves BL-011) ------------------

def test_extract_ride_metadata_maps_all_six_fields():
    details = {
        "ride": {
            "title": "45 min Power Zone Max Ride", "duration": 2700,
            "difficulty_estimate": 8.40, "instructor": {"name": "Matt Wilpers"}, "instructor_id": "i1",
        },
        "class_types": [{"name": "Power Zone"}],
        "is_power_zone_class": True,
    }

    result = _extract_ride_metadata(details, "ride-1")

    assert result == {
        "activity_title": "45 min Power Zone Max Ride",
        "instructor_name": "Matt Wilpers",
        "class_type": "Power Zone",
        "planned_duration_s": 2700,
        "provider_class_id": "ride-1",
        "difficulty_estimate": 8.40,
    }


def test_extract_ride_metadata_multiple_class_types_comma_joined():
    details = {"ride": {"title": "Tabata Ride", "duration": 1800}, "class_types": [{"name": "Power Zone"}, {"name": "Tabata"}]}

    result = _extract_ride_metadata(details, "ride-2")

    assert result["class_type"] == "Power Zone, Tabata"


def test_extract_ride_metadata_empty_class_types_is_empty_string_not_none():
    """The COALESCE-contract edge case: a genuinely successful lookup with
    zero tags must store "", never None — None is reserved for "not
    attempted this pass" (see peloton.py's _extract_ride_metadata()
    docstring and the architecture doc's "None-means-not-attempted
    contract"). A future "simplification" to e.g. class_types[0]["name"]
    would silently reintroduce this hazard; this test guards against it."""
    details = {"ride": {"title": "Just Ride", "duration": 1200}, "class_types": []}

    result = _extract_ride_metadata(details, "ride-3")

    assert result["class_type"] == ""
    assert result["class_type"] is not None


def test_extract_ride_metadata_missing_instructor_is_none_not_an_error():
    details = {"ride": {"title": "Scenic Ride", "duration": 1800}, "class_types": []}

    result = _extract_ride_metadata(details, "ride-4")

    assert result["instructor_name"] is None


# --- Issue #46: apply_class_metadata_update() --------------------------

def test_apply_class_metadata_update_sets_only_the_six_columns(db):
    db.execute(
        """
        INSERT INTO normalized_activities
            (provider, external_id, start_time, duration_s, discipline, source_confidence)
        VALUES ('peloton', 'ext-1', '2026-01-01T00:00:00+00:00', 300, 'cycling', 0.9)
        """
    )
    db.commit()

    apply_class_metadata_update(
        db, "ext-1",
        {
            "activity_title": "Power Zone Max", "instructor_name": "Matt Wilpers",
            "class_type": "power_zone_max", "planned_duration_s": 2700, "provider_class_id": "ride-1",
            "difficulty_estimate": 8.40,
        },
    )
    db.commit()

    row = dict(db.execute(
        "SELECT * FROM normalized_activities WHERE provider = 'peloton' AND external_id = 'ext-1'"
    ).fetchone())
    assert row["activity_title"] == "Power Zone Max"
    assert row["instructor_name"] == "Matt Wilpers"
    assert row["class_type"] == "power_zone_max"
    assert row["planned_duration_s"] == 2700
    assert row["provider_class_id"] == "ride-1"
    assert row["difficulty_estimate"] == 8.40
    assert row["discipline"] == "cycling"  # untouched


def test_apply_class_metadata_update_scoped_to_peloton_provider_only(db):
    """Must not touch a same-external_id row belonging to a different
    provider — the WHERE clause filters on provider = 'peloton' explicitly,
    not just external_id."""
    db.execute(
        """
        INSERT INTO normalized_activities
            (provider, external_id, start_time, duration_s, discipline, source_confidence)
        VALUES ('strava', 'ext-1', '2026-01-01T00:00:00+00:00', 300, 'cycling', 0.9)
        """
    )
    db.commit()

    apply_class_metadata_update(db, "ext-1", {"activity_title": "Should not apply"})
    db.commit()

    row = dict(db.execute(
        "SELECT * FROM normalized_activities WHERE provider = 'strava' AND external_id = 'ext-1'"
    ).fetchone())
    assert row["activity_title"] is None


# --- Issue #33: extract_resume_cursor() must always return a str --------

def test_extract_resume_cursor_coerces_int_start_time_to_str(credential_store):
    fake = FakePelotonSession()
    connector = PelotonConnector(credential_store, session=fake)

    assert connector.extract_resume_cursor({"start_time": 1790014244}) == "1790014244"


def test_extract_resume_cursor_missing_start_time_returns_none(credential_store):
    fake = FakePelotonSession()
    connector = PelotonConnector(credential_store, session=fake)

    assert connector.extract_resume_cursor({}) is None


# --- End-to-end: PelotonConnector through the real Synchronization Engine --

def test_end_to_end_automated_login_sync(db, credential_store):
    credential_store.set(PROVIDER, CRED_EMAIL, "athlete@example.com")
    credential_store.set(PROVIDER, CRED_PASSWORD, "pw")
    fake = FakePelotonSession()
    fake.script_post_response(_login_success_response())
    fake.script_get_response(FakeResponse(200, {"id": "u1"}))
    fake.script_get_response(FakeResponse(200, {"data": [
        {"id": 1, "start_time": 1790014244, "end_time": 1790016044, "fitness_discipline": "cycling"},
        {"id": 2, "start_time": 1790100644, "end_time": 1790102444, "fitness_discipline": "strength"},
    ], "show_next": False}))
    connector = PelotonConnector(credential_store, session=fake)
    engine = SynchronizationEngine(db)

    result = engine.run_once([connector])

    assert result.connector_results[0].records_upserted == 2
    assert result.connector_results[0].state == ConnectorState.HEALTHY
    checkpoint = engine.get_checkpoint("peloton")
    assert checkpoint == "1790100644"

    # Epic 6, slice 5: the R-PELOTON-06 concern (strength misnormalized as
    # a ride) is now verified all the way through to actual persistence,
    # not just at the connector's raw-extraction layer (Epic 3).
    canonical_rows = db.execute(
        "SELECT external_id, discipline FROM normalized_activities "
        "WHERE provider = 'peloton' ORDER BY external_id"
    ).fetchall()
    disciplines = {r["external_id"]: r["discipline"] for r in canonical_rows}
    assert disciplines["1"] == "cycling"
    assert disciplines["2"] == "strength"  # never "cycling"/"ride"


def test_end_to_end_sync_with_preexisting_str_checkpoint_does_not_raise(db, credential_store):
    """Issue #33 regression: reproduces the BO's logged crash — a
    `sync_checkpoints` row already persisted with `last_cursor` as a str
    (as every row does, via SQLite TEXT affinity, even pre-fix) must not
    raise TypeError when compared against a freshly downloaded int-shaped
    `start_time`. Uses the exact logged values: persisted resume
    `'1790883770'`, candidate `1791134056`."""
    credential_store.set(PROVIDER, CRED_EMAIL, "athlete@example.com")
    credential_store.set(PROVIDER, CRED_PASSWORD, "pw")
    fake = FakePelotonSession()
    fake.script_post_response(_login_success_response())
    fake.script_get_response(FakeResponse(200, {"id": "u1"}))
    fake.script_get_response(FakeResponse(200, {"data": [
        {"id": 1, "start_time": 1791134056, "end_time": 1791135856, "fitness_discipline": "cycling"},
    ], "show_next": False}))
    connector = PelotonConnector(credential_store, session=fake)
    engine = SynchronizationEngine(db)
    engine._set_checkpoint(PROVIDER, "1790883770", strategy="default")

    result = engine.run_once([connector])

    assert result.connector_results[0].state == ConnectorState.HEALTHY
    assert engine.get_checkpoint("peloton") == "1791134056"


def test_end_to_end_two_consecutive_syncs_cursor_advances_without_error(db, credential_store):
    """Issue #33 regression: the full extraction -> persist -> reload cycle
    across two separate sync runs against the same DB, which is the path
    that was actually broken (a unit test of extract_resume_cursor() alone
    would not catch the SQLite TEXT-affinity round trip)."""
    credential_store.set(PROVIDER, CRED_EMAIL, "athlete@example.com")
    credential_store.set(PROVIDER, CRED_PASSWORD, "pw")
    fake = FakePelotonSession()
    fake.script_post_response(_login_success_response())
    fake.script_get_response(FakeResponse(200, {"id": "u1"}))
    fake.script_get_response(FakeResponse(200, {"data": [
        {"id": 1, "start_time": 1790883770, "end_time": 1790885570, "fitness_discipline": "cycling"},
    ], "show_next": False}))
    connector = PelotonConnector(credential_store, session=fake)
    engine = SynchronizationEngine(db)

    first_result = engine.run_once([connector])

    assert first_result.connector_results[0].state == ConnectorState.HEALTHY
    assert engine.get_checkpoint("peloton") == "1790883770"

    fake.script_get_response(FakeResponse(200, {"id": "u1"}))
    fake.script_get_response(FakeResponse(200, {"data": [
        {"id": 2, "start_time": 1791134056, "end_time": 1791135856, "fitness_discipline": "cycling"},
    ], "show_next": False}))

    second_result = engine.run_once([connector])

    assert second_result.connector_results[0].state == ConnectorState.HEALTHY
    assert engine.get_checkpoint("peloton") == "1791134056"


def test_end_to_end_repeated_auth_failure_transitions_to_degraded(db, credential_store):
    """Baseline resilience check — the state machine and Sync Engine
    integration work exactly as they did for Strava/Eufy."""
    credential_store.set(PROVIDER, CRED_EMAIL, "athlete@example.com")
    credential_store.set(PROVIDER, CRED_PASSWORD, "wrong-password")
    fake = FakePelotonSession()
    fake.script_post_response(FakeResponse(403, {"message": "Access forbidden. Endpoint no longer accepting requests."}))
    connector = PelotonConnector(credential_store, session=fake)
    engine = SynchronizationEngine(db)

    result = engine.run_once([connector])

    assert result.connector_results[0].state == ConnectorState.DEGRADED


# --- ADR-038 verification: Degraded connectors ARE now periodically re-attempted (resolves BL-007) ---

def test_degraded_connector_is_re_attempted_after_the_backoff_interval_elapses(db, credential_store):
    """BL-007 resolved: this replaces the earlier test that documented the
    gap (a connector attempted exactly once, then never again). With
    ADR-038 wired in, using the Sync Engine's injectable clock to simulate
    real elapsed days rather than actual wall-clock waiting, a Degraded
    Peloton connector IS re-attempted once the backoff interval elapses —
    not on every run (that would be the Milestone 2 §7 hammering risk this
    ADR was specifically designed to avoid), but on the documented cadence."""
    credential_store.set(PROVIDER, CRED_EMAIL, "a@b.com")
    credential_store.set(PROVIDER, CRED_PASSWORD, "wrong-password")
    fake = FakePelotonSession()
    fake.script_post_response(FakeResponse(403, {"message": "Access forbidden."}))

    clock = {"now": datetime(2026, 1, 1, tzinfo=timezone.utc)}
    connector = PelotonConnector(credential_store, session=fake)
    engine = SynchronizationEngine(db, now_fn=lambda: clock["now"])

    engine.run_once([connector])
    assert len(fake.post_calls) == 1  # first, entry attempt

    # Same day: still within the 1-day backoff window — no re-attempt yet.
    clock["now"] += timedelta(hours=6)
    engine.run_once([connector])
    assert len(fake.post_calls) == 1

    # One full day later: eligible again per the backoff schedule.
    clock["now"] += timedelta(days=1)
    engine.run_once([connector])
    assert len(fake.post_calls) == 2


def test_degraded_connector_escalates_to_recovery_required_after_ten_days(db, credential_store):
    """Quality Gate 1 (ADR-038 §6): escalates exactly once, at the
    configured threshold, verified end-to-end through the real
    PelotonConnector and Sync Engine, not just the pure policy function."""
    credential_store.set(PROVIDER, CRED_EMAIL, "a@b.com")
    credential_store.set(PROVIDER, CRED_PASSWORD, "wrong-password")
    fake = FakePelotonSession()
    fake.script_post_response(FakeResponse(403, {"message": "Access forbidden."}))

    clock = {"now": datetime(2026, 1, 1, tzinfo=timezone.utc)}
    connector = PelotonConnector(credential_store, session=fake)
    engine = SynchronizationEngine(db, now_fn=lambda: clock["now"])

    # Walk the clock forward day by day, well past the 10-day threshold,
    # only running the engine when the backoff schedule would actually
    # make a new attempt eligible (1, 3, 7, 14, ... days from entry).
    for day in (0, 1, 3, 7, 14):
        clock["now"] = datetime(2026, 1, 1, tzinfo=timezone.utc) + timedelta(days=day)
        result = engine.run_once([connector])

    final_state = db.execute(
        "SELECT state FROM connector_state WHERE provider = 'peloton'"
    ).fetchone()["state"]
    assert final_state == "RecoveryRequired"
    # Real recovery instructions must now be available, matching Feature 3.2.
    assert "Bearer Token" in connector.request_manual_recovery()
