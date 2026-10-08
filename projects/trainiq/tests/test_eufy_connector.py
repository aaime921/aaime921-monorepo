"""
Tests for Epic 2 — Eufy Connector (Features 2.1, 2.2, 2.3).

Every test uses a fake HTTP session — this sandbox cannot reach
home-api.eufylife.com (see module docstring in trainiq/connectors/eufy.py).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from trainiq.connectors.base import AcquisitionStrategy, CapabilityTier
from trainiq.connectors.eufy import (
    CRED_ACCESS_TOKEN,
    CRED_EMAIL,
    CRED_EXPIRES_AT,
    CRED_PASSWORD,
    CRED_REFRESH_TOKEN,
    PROVIDER,
    EufyConnector,
    EufyHTTPError,
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


class FakeEufySession:
    """Stand-in for the real requests-based session — fully scripted."""

    def __init__(self):
        self.post_calls: list[dict] = []
        self.get_calls: list[dict] = []
        self._post_response: FakeResponse | None = None
        self._get_response: FakeResponse | None = None

    def script_post_response(self, response: FakeResponse):
        self._post_response = response

    def script_get_response(self, response: FakeResponse):
        self._get_response = response

    def post(self, url, json=None, headers=None):
        self.post_calls.append({"url": url, "json": json, "headers": headers})
        return self._post_response

    def get(self, url, params=None, headers=None):
        self.get_calls.append({"url": url, "params": params, "headers": headers})
        return self._get_response


def _login_success_response(access_token="access-token-1", refresh_token="refresh-token-1", devices=None):
    body = {"res_code": 1, "access_token": access_token, "refresh_token": refresh_token, "user_id": "u1"}
    if devices is not None:
        body["devices"] = devices
    return FakeResponse(200, body)


# --- RC1-HF-003: device discovery from the login response ------------------

def test_discovered_devices_empty_before_any_login(credential_store):
    fake = FakeEufySession()
    connector = EufyConnector(credential_store, session=fake)
    assert connector.discovered_devices() == []


def test_discovered_devices_populated_from_login_response(credential_store):
    """The core RC1-HF-003 finding, verified live: the login response
    itself includes a `devices` array — no separate GET /device/ call
    needed to discover the scale's id."""
    credential_store.set(PROVIDER, CRED_EMAIL, "a@b.com")
    credential_store.set(PROVIDER, CRED_PASSWORD, "pw")
    fake = FakeEufySession()
    fake.script_post_response(_login_success_response(
        devices=[{"id": "eufyt9150cfe90116a933", "name": "Smart Scale P3"}]
    ))
    connector = EufyConnector(credential_store, session=fake)

    connector.authenticate()

    assert connector.discovered_devices() == [{"id": "eufyt9150cfe90116a933", "name": "Smart Scale P3"}]


def test_discovered_devices_defaults_to_empty_list_when_field_absent(credential_store):
    """Defensive: a login response with no `devices` field at all (e.g. an
    account with zero registered devices) must not crash — empty list, not
    a KeyError."""
    credential_store.set(PROVIDER, CRED_EMAIL, "a@b.com")
    credential_store.set(PROVIDER, CRED_PASSWORD, "pw")
    fake = FakeEufySession()
    fake.script_post_response(_login_success_response())  # no devices= passed
    connector = EufyConnector(credential_store, session=fake)

    connector.authenticate()

    assert connector.discovered_devices() == []


def test_device_id_is_optional_at_construction(credential_store):
    """RC1-HF-003: device_id is no longer mandatory — verified live that
    it can be discovered post-login instead of required upfront."""
    fake = FakeEufySession()
    connector = EufyConnector(credential_store, session=fake)  # no device_id passed
    assert connector is not None  # must not raise TypeError


def test_select_device_sets_the_device_id_used_by_download(credential_store):
    fake = FakeEufySession()
    fake.script_post_response(_login_success_response())
    fake.script_get_response(FakeResponse(200, {"res_code": 1, "data": []}))
    connector = EufyConnector(credential_store, session=fake)
    credential_store.set(PROVIDER, CRED_EMAIL, "a@b.com")
    credential_store.set(PROVIDER, CRED_PASSWORD, "pw")
    connector.authenticate()

    connector.select_device("chosen-device-id")
    connector.download()

    assert "chosen-device-id" in fake.get_calls[0]["url"]


# --- Feature 2.1a: Authentication -----------------------------------------

def test_authenticate_returns_false_when_never_connected(credential_store):
    fake = FakeEufySession()
    connector = EufyConnector(credential_store, device_id="dev-1", session=fake)
    assert connector.authenticate() is False
    assert fake.post_calls == []


def test_authenticate_logs_in_with_stored_email_and_password(credential_store):
    credential_store.set(PROVIDER, CRED_EMAIL, "athlete@example.com")
    credential_store.set(PROVIDER, CRED_PASSWORD, "correct-horse-battery-staple")
    fake = FakeEufySession()
    fake.script_post_response(_login_success_response())
    connector = EufyConnector(credential_store, device_id="dev-1", session=fake)

    assert connector.authenticate() is True
    # RC1-HF-003: verified live against a real account — the login payload
    # requires client_id/client_secret alongside email/password, and a
    # category: Health header. The original implementation omitted both,
    # which produced a generic, misleading application-level error
    # ({"res_code":500,"message":"Service is temporarily unavailable..."})
    # rather than a clear "missing field" error.
    assert fake.post_calls[0]["json"] == {
        "client_id": "eufy-app",
        "client_secret": "8FHf22gaTKu7MZXqz5zytw",
        "email": "athlete@example.com",
        "password": "correct-horse-battery-staple",
    }
    assert fake.post_calls[0]["headers"] == {"category": "Health"}
    assert "user/v2/email/login" in fake.post_calls[0]["url"]


def test_authenticate_reuses_valid_unexpired_access_token(credential_store):
    future_expiry = int((datetime.now(timezone.utc) + timedelta(hours=1)).timestamp())
    credential_store.set(PROVIDER, CRED_EMAIL, "a@b.com")
    credential_store.set(PROVIDER, CRED_PASSWORD, "pw")
    credential_store.set(PROVIDER, CRED_ACCESS_TOKEN, "still-valid-token")
    credential_store.set(PROVIDER, CRED_EXPIRES_AT, str(future_expiry))
    fake = FakeEufySession()
    connector = EufyConnector(credential_store, device_id="dev-1", session=fake)

    assert connector.authenticate() is True
    assert fake.post_calls == []  # must NOT re-login when still valid


def test_login_persists_access_and_refresh_tokens(credential_store):
    credential_store.set(PROVIDER, CRED_EMAIL, "a@b.com")
    credential_store.set(PROVIDER, CRED_PASSWORD, "pw")
    fake = FakeEufySession()
    fake.script_post_response(_login_success_response("new-access", "new-refresh"))
    connector = EufyConnector(credential_store, device_id="dev-1", session=fake)

    connector.authenticate()

    assert credential_store.get(PROVIDER, CRED_ACCESS_TOKEN) == "new-access"
    assert credential_store.get(PROVIDER, CRED_REFRESH_TOKEN) == "new-refresh"


def test_login_rejected_returns_false(credential_store):
    credential_store.set(PROVIDER, CRED_EMAIL, "a@b.com")
    credential_store.set(PROVIDER, CRED_PASSWORD, "wrong-password")
    fake = FakeEufySession()
    fake.script_post_response(FakeResponse(401, {"message": "invalid credentials"}))
    connector = EufyConnector(credential_store, device_id="dev-1", session=fake)

    assert connector.authenticate() is False


def test_login_rate_limited_raises_transient_error_with_retry_after(credential_store):
    """ADR-037: a standard Retry-After header must be honored, same as Strava."""
    credential_store.set(PROVIDER, CRED_EMAIL, "a@b.com")
    credential_store.set(PROVIDER, CRED_PASSWORD, "pw")
    fake = FakeEufySession()
    fake.script_post_response(FakeResponse(429, {}, headers={"Retry-After": "120"}))
    connector = EufyConnector(credential_store, device_id="dev-1", session=fake)

    with pytest.raises(TransientError) as excinfo:
        connector.authenticate()
    assert excinfo.value.retry_after_s == 120.0


def test_login_server_error_raises_transient_error_without_retry_after(credential_store):
    credential_store.set(PROVIDER, CRED_EMAIL, "a@b.com")
    credential_store.set(PROVIDER, CRED_PASSWORD, "pw")
    fake = FakeEufySession()
    fake.script_post_response(FakeResponse(503, {}))
    connector = EufyConnector(credential_store, device_id="dev-1", session=fake)

    with pytest.raises(TransientError) as excinfo:
        connector.authenticate()
    assert excinfo.value.retry_after_s is None  # no hint available — generic backoff applies


def test_login_unexpected_status_raises_eufy_http_error(credential_store):
    credential_store.set(PROVIDER, CRED_EMAIL, "a@b.com")
    credential_store.set(PROVIDER, CRED_PASSWORD, "pw")
    fake = FakeEufySession()
    fake.script_post_response(FakeResponse(418, {}))
    connector = EufyConnector(credential_store, device_id="dev-1", session=fake)

    with pytest.raises(EufyHTTPError):
        connector.authenticate()


def test_login_http_200_with_application_error_payload_returns_false_not_keyerror(credential_store):
    """Regression test: an HTTP 200 response carrying an application-level
    error (res_code != 1, no access_token) — exactly the failure mode this
    connector's login previously hit before RC1-HF-003 — must return False
    cleanly, never raise KeyError from `body["access_token"]`."""
    credential_store.set(PROVIDER, CRED_EMAIL, "a@b.com")
    credential_store.set(PROVIDER, CRED_PASSWORD, "pw")
    fake = FakeEufySession()
    fake.script_post_response(FakeResponse(200, {
        "res_code": 500, "message": "Service is temporarily unavailable. Please try again later."
    }))
    connector = EufyConnector(credential_store, device_id="dev-1", session=fake)

    assert connector.authenticate() is False  # must not raise


def test_login_requires_res_code_1_even_if_access_token_present(credential_store):
    """Defensive: authenticate only when res_code == 1, per explicit
    requirement — a response with an access_token but a non-1 res_code
    must still be rejected, not opportunistically accepted."""
    credential_store.set(PROVIDER, CRED_EMAIL, "a@b.com")
    credential_store.set(PROVIDER, CRED_PASSWORD, "pw")
    fake = FakeEufySession()
    fake.script_post_response(FakeResponse(200, {"res_code": 0, "access_token": "should-not-be-used"}))
    connector = EufyConnector(credential_store, device_id="dev-1", session=fake)

    assert connector.authenticate() is False


# --- Feature 2.1b: Sync ----------------------------------------------------

def test_download_before_authenticate_raises_authentication_error(credential_store):
    fake = FakeEufySession()
    connector = EufyConnector(credential_store, device_id="dev-1", session=fake)

    with pytest.raises(AuthenticationError):
        connector.download()


def test_download_sends_token_header_not_bearer(credential_store):
    """Milestone 3 §3.1: Eufy uses a non-standard `token:` header, not
    `Authorization: Bearer`. This is a real, deliberate divergence, not an
    oversight — verified explicitly rather than assumed."""
    credential_store.set(PROVIDER, CRED_EMAIL, "a@b.com")
    credential_store.set(PROVIDER, CRED_PASSWORD, "pw")
    fake = FakeEufySession()
    fake.script_post_response(_login_success_response(access_token="the-token"))
    fake.script_get_response(FakeResponse(200, {"res_code": 1, "data": []}))
    connector = EufyConnector(credential_store, device_id="dev-1", session=fake)
    connector.authenticate()

    connector.download()

    assert fake.get_calls[0]["headers"] == {"token": "the-token"}


def test_download_passes_since_as_start_time_param(credential_store):
    credential_store.set(PROVIDER, CRED_EMAIL, "a@b.com")
    credential_store.set(PROVIDER, CRED_PASSWORD, "pw")
    fake = FakeEufySession()
    fake.script_post_response(_login_success_response())
    fake.script_get_response(FakeResponse(200, {"res_code": 1, "data": []}))
    connector = EufyConnector(credential_store, device_id="dev-1", session=fake)
    connector.authenticate()

    connector.download(since="2026-01-01T00:00:00+00:00")

    assert fake.get_calls[0]["params"] == {"start_time": "2026-01-01T00:00:00+00:00"}


def test_download_rate_limited_raises_transient_error_with_retry_after(credential_store):
    credential_store.set(PROVIDER, CRED_EMAIL, "a@b.com")
    credential_store.set(PROVIDER, CRED_PASSWORD, "pw")
    fake = FakeEufySession()
    fake.script_post_response(_login_success_response())
    fake.script_get_response(FakeResponse(429, {}, headers={"Retry-After": "45"}))
    connector = EufyConnector(credential_store, device_id="dev-1", session=fake)
    connector.authenticate()

    with pytest.raises(TransientError) as excinfo:
        connector.download()
    assert excinfo.value.retry_after_s == 45.0


def test_download_session_rejected_raises_authentication_error(credential_store):
    credential_store.set(PROVIDER, CRED_EMAIL, "a@b.com")
    credential_store.set(PROVIDER, CRED_PASSWORD, "pw")
    fake = FakeEufySession()
    fake.script_post_response(_login_success_response())
    fake.script_get_response(FakeResponse(401, {}))
    connector = EufyConnector(credential_store, device_id="dev-1", session=fake)
    connector.authenticate()

    with pytest.raises(AuthenticationError):
        connector.download()


def test_download_returns_data_array_not_items(credential_store):
    """RC1-HF-003 correction: the real API returns the array under `data`,
    not `items` — the previous key was never verified against a live
    account and was simply wrong. Renamed from
    test_download_returns_items_list to reflect the corrected field."""
    credential_store.set(PROVIDER, CRED_EMAIL, "a@b.com")
    credential_store.set(PROVIDER, CRED_PASSWORD, "pw")
    fake = FakeEufySession()
    fake.script_post_response(_login_success_response())
    fake.script_get_response(FakeResponse(200, {"res_code": 1, "data": [{"id": "1"}, {"id": "2"}]}))
    connector = EufyConnector(credential_store, device_id="dev-1", session=fake)
    connector.authenticate()

    result = connector.download()

    assert result == [{"id": "1"}, {"id": "2"}]


# --- Feature 2.2 (extraction-only): normalize() -----------------------------

def test_normalize_maps_full_record(credential_store):
    fake = FakeEufySession()
    connector = EufyConnector(credential_store, device_id="dev-1", session=fake)
    raw = {
        "id": 42, "device_id": "dev-1", "create_time": "2026-01-05T06:30:00+00:00", "update_time": "2026-01-05T06:30:00+00:00",
        "scale_data": {"weight": 754, "body_fat": 18.2, "muscle_mass": 34.1},
    }

    result = connector.normalize(raw)

    assert result == {
        "provider": "eufy",
        "external_id": "42",
        "timestamp": "2026-01-05T06:30:00+00:00",
        "weight_kg": 75.4,
        "body_fat_pct": 18.2,
        "muscle_mass_pct": 34.1,
    }


def test_normalize_never_fabricates_missing_body_composition_fields(credential_store):
    """Milestone 3 §4: metrics are model-dependent and sparse. A basic scale
    reporting only weight must not have body_fat/muscle_mass fabricated."""
    fake = FakeEufySession()
    connector = EufyConnector(credential_store, device_id="dev-1", session=fake)
    raw = {
        "id": 1, "device_id": "dev-1", "create_time": "2026-01-05T06:30:00+00:00",
        "scale_data": {"weight": 800},
    }

    result = connector.normalize(raw)

    assert result["weight_kg"] == 80.0
    assert result["body_fat_pct"] is None
    assert result["muscle_mass_pct"] is None


def test_normalize_converts_deci_kg_weight_to_kg(credential_store):
    """Issue #1 regression test: real captured Eufy account evidence
    (2026-09-27) showed raw scale_data.weight values of 829.5 and 883.5 —
    physically impossible as kilograms, but plausible adult body weights
    (82.95, 88.35) once divided by 10, confirming the field is in
    deci-kilograms, not kilograms. Uses the exact real values from the
    issue so this conversion cannot silently regress."""
    fake = FakeEufySession()
    connector = EufyConnector(credential_store, device_id="dev-1", session=fake)

    result_1 = connector.normalize({
        "id": 1, "device_id": "dev-1", "create_time": 1787814286,
        "scale_data": {"weight": 829.5},
    })
    result_2 = connector.normalize({
        "id": 2, "device_id": "dev-1", "create_time": 1748027437,
        "scale_data": {"weight": 883.5},
    })

    assert result_1["weight_kg"] == pytest.approx(82.95)
    assert result_2["weight_kg"] == pytest.approx(88.35)


def test_normalize_weight_kg_is_none_when_weight_key_absent(credential_store):
    """Issue #1 AC3: a missing weight key must yield weight_kg is None,
    never None / WEIGHT_DECI_KG_TO_KG_DIVISOR (which would raise TypeError)."""
    fake = FakeEufySession()
    connector = EufyConnector(credential_store, device_id="dev-1", session=fake)
    raw = {
        "id": 1, "device_id": "dev-1", "create_time": "2026-01-05T06:30:00+00:00",
        "scale_data": {"body_fat": 18.2},
    }

    result = connector.normalize(raw)

    assert result["weight_kg"] is None


def test_normalize_weight_kg_is_none_when_scale_data_absent(credential_store):
    """Issue #1 AC3: scale_data missing entirely must also yield
    weight_kg is None, not an exception."""
    fake = FakeEufySession()
    connector = EufyConnector(credential_store, device_id="dev-1", session=fake)
    raw = {"id": 1, "device_id": "dev-1", "create_time": "2026-01-05T06:30:00+00:00"}

    result = connector.normalize(raw)

    assert result["weight_kg"] is None


# --- Issue #42: body_fat/muscle_mass = 0.0 is Eufy's "not measured" sentinel


def test_normalize_maps_zero_body_fat_to_none(credential_store):
    """BO-confirmed live evidence: a literal 0 on body_fat means the scale
    took no impedance reading that time (e.g. weighed with socks on), not
    a genuine 0% reading. Must normalize to None, never pass through as a
    literal 0."""
    fake = FakeEufySession()
    connector = EufyConnector(credential_store, device_id="dev-1", session=fake)
    raw = {
        "id": 1, "device_id": "dev-1", "create_time": "2026-09-11T08:00:00+00:00",
        "scale_data": {"weight": 822, "body_fat": 0.0, "muscle_mass": 34.0},
    }

    result = connector.normalize(raw)

    assert result["weight_kg"] == pytest.approx(82.2)
    assert result["body_fat_pct"] is None
    assert result["muscle_mass_pct"] == 34.0


def test_normalize_maps_zero_muscle_mass_to_none(credential_store):
    """Same sentinel pattern, BO-confirmed for muscle_mass too."""
    fake = FakeEufySession()
    connector = EufyConnector(credential_store, device_id="dev-1", session=fake)
    raw = {
        "id": 1, "device_id": "dev-1", "create_time": "2026-09-11T08:00:00+00:00",
        "scale_data": {"weight": 822, "body_fat": 18.0, "muscle_mass": 0.0},
    }

    result = connector.normalize(raw)

    assert result["body_fat_pct"] == 18.0
    assert result["muscle_mass_pct"] is None


def test_normalize_passes_through_non_zero_body_fat_and_muscle_mass_unchanged(credential_store):
    """Regression against over-normalizing: a present, non-zero value must
    not be touched by the sentinel fix."""
    fake = FakeEufySession()
    connector = EufyConnector(credential_store, device_id="dev-1", session=fake)
    raw = {
        "id": 1, "device_id": "dev-1", "create_time": "2026-09-11T08:00:00+00:00",
        "scale_data": {"weight": 822, "body_fat": 18.2, "muscle_mass": 34.1},
    }

    result = connector.normalize(raw)

    assert result["body_fat_pct"] == 18.2
    assert result["muscle_mass_pct"] == 34.1


def test_extract_resume_cursor_always_returns_a_str(credential_store):
    """Regression test for the live-verified TypeError: 'int' > 'str'.
    create_time from the real API is a Unix epoch integer, not an ISO 8601
    string as originally (never-live-verified) assumed. The base class's
    contract (base.py) requires extract_resume_cursor() to return a value
    "safely orderable via a plain string `>` comparison" — an unconverted
    int violates that contract the moment it round-trips through the
    TEXT-affinity sync_checkpoints column and comes back as a str on the
    next run. Uses the exact real value observed in the user's own
    sync_checkpoints row."""
    fake = FakeEufySession()
    connector = EufyConnector(credential_store, device_id="dev-1", session=fake)
    normalized = connector.normalize({
        "id": 1, "device_id": "dev-1", "create_time": 1781160591,  # real observed value, an int
        "scale_data": {"weight": 800},
    })

    cursor = connector.extract_resume_cursor(normalized)

    assert cursor == "1781160591"
    assert isinstance(cursor, str)


# --- Feature 2.3: Acquisition strategy plumbing -----------------------------

def test_reports_tier_3_multi_strategy(credential_store):
    fake = FakeEufySession()
    connector = EufyConnector(credential_store, device_id="dev-1", session=fake)
    assert connector.capability_tier == CapabilityTier.TIER_3_MULTI_STRATEGY


def test_reports_only_cloud_strategy_ble_not_implemented(credential_store):
    fake = FakeEufySession()
    connector = EufyConnector(credential_store, device_id="dev-1", session=fake)
    assert connector.list_acquisition_strategies() == [AcquisitionStrategy.CLOUD]
    assert connector.active_strategy() == AcquisitionStrategy.CLOUD


# --- End-to-end: EufyConnector through the real Synchronization Engine -----

def test_sync_no_longer_reports_zero_records_against_the_real_payload_shape(db, credential_store):
    """The exact regression named in the bug report: against the real,
    verified response shape (data wrapped under `data`, not `items`), the
    old parser would have silently returned an empty list and reported
    0 records upserted despite the server returning real measurements.
    This proves that no longer happens."""
    credential_store.set(PROVIDER, CRED_EMAIL, "athlete@example.com")
    credential_store.set(PROVIDER, CRED_PASSWORD, "pw")
    fake = FakeEufySession()
    fake.script_post_response(_login_success_response())
    fake.script_get_response(FakeResponse(200, {"res_code": 1, "data": [
        {"id": "m1", "device_id": "dev-1", "create_time": "2026-02-01T07:00:00+00:00",
         "update_time": "2026-02-01T07:00:00+00:00",
         "scale_data": {"weight": 823, "body_fat": 21.0, "muscle_mass": 36.5}},
        {"id": "m2", "device_id": "dev-1", "create_time": "2026-02-02T07:00:00+00:00",
         "update_time": "2026-02-02T07:00:00+00:00",
         "scale_data": {"weight": 819}},
        {"id": "m3", "device_id": "dev-1", "create_time": "2026-02-03T07:00:00+00:00",
         "update_time": "2026-02-03T07:00:00+00:00",
         "scale_data": {"weight": 818, "body_fat": 20.7, "muscle_mass": 36.8}},
    ]}))
    connector = EufyConnector(credential_store, device_id="dev-1", session=fake)
    engine = SynchronizationEngine(db)

    result = engine.run_once([connector])

    assert result.connector_results[0].records_upserted == 3  # NOT 0
    weights = [
        r["weight_kg"] for r in db.execute(
            "SELECT weight_kg FROM weigh_ins WHERE provider = 'eufy' ORDER BY external_id"
        ).fetchall()
    ]
    assert weights == [82.3, 81.9, 81.8]


def test_end_to_end_sync_via_synchronization_engine(db, credential_store):
    credential_store.set(PROVIDER, CRED_EMAIL, "athlete@example.com")
    credential_store.set(PROVIDER, CRED_PASSWORD, "pw")
    fake = FakeEufySession()
    fake.script_post_response(_login_success_response())
    fake.script_get_response(FakeResponse(200, {"res_code": 1, "data": [
        {"id": "w1", "device_id": "dev-1", "create_time": "2026-01-05T06:30:00+00:00", "scale_data": {"weight": 750}},
        {"id": "w2", "device_id": "dev-1", "create_time": "2026-01-06T06:30:00+00:00", "scale_data": {"weight": 749}},
    ]}))
    connector = EufyConnector(credential_store, device_id="dev-1", session=fake)
    engine = SynchronizationEngine(db)

    result = engine.run_once([connector])

    assert result.connector_results[0].records_upserted == 2
    rows = db.execute("SELECT provider, external_id FROM raw_activities ORDER BY external_id").fetchall()
    assert [(r["provider"], r["external_id"]) for r in rows] == [("eufy", "w1"), ("eufy", "w2")]

    # Checkpoint must be strategy-aware (Hardening Finding 4) — Eufy is the
    # first REAL Tier 3 connector to exercise this, not just a mock.
    checkpoint = engine.get_checkpoint("eufy", strategy="cloud")
    assert checkpoint is not None

    meta = db.execute("SELECT connected FROM credentials_metadata WHERE provider = 'eufy'").fetchone()
    assert meta["connected"] == 1

    # Epic 6, slice 5: Eufy is WEIGH_IN-kind, so its canonical records must
    # land in weigh_ins, never normalized_activities.
    weigh_in_rows = db.execute(
        "SELECT weight_kg FROM weigh_ins WHERE provider = 'eufy' ORDER BY external_id"
    ).fetchall()
    assert [r["weight_kg"] for r in weigh_in_rows] == [75.0, 74.9]
    activity_rows = db.execute(
        "SELECT * FROM normalized_activities WHERE provider = 'eufy'"
    ).fetchall()
    assert activity_rows == []
