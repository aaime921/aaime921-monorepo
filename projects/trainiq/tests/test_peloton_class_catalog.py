"""
Tests for issue #72's two new PelotonConnector methods —
`fetch_ride_metadata_mappings()` and `fetch_archived_classes()` — used by
`trainiq.export.classes` to build the coach's class-candidate catalog.

Every test uses a fake HTTP session — this sandbox cannot reach
api.onepeloton.com (see module docstring in trainiq/connectors/peloton.py).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from trainiq.connectors.peloton import CRED_EMAIL, CRED_PASSWORD, PROVIDER, PelotonConnector
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
    def __init__(self, status_code: int, json_body: dict | None = None, headers: dict | None = None):
        self.status_code = status_code
        self._json_body = json_body or {}
        self.headers = headers or {}

    def json(self):
        return self._json_body


class FakeSession:
    def __init__(self):
        self.get_calls: list[dict] = []
        self._get_responses: list[FakeResponse] = []
        self._post_response: FakeResponse | None = None

    def script_get_response(self, response: FakeResponse):
        self._get_responses.append(response)

    def script_post_response(self, response: FakeResponse):
        self._post_response = response

    def post(self, url, json=None, headers=None):
        return self._post_response

    def get(self, url, params=None, headers=None):
        self.get_calls.append({"url": url, "params": params, "headers": headers})
        return self._get_responses.pop(0)


def _login_success_response():
    return FakeResponse(200, {"session_id": "session-abc"})


def _authenticated_connector(credential_store, fake):
    credential_store.set(PROVIDER, CRED_EMAIL, "a@b.com")
    credential_store.set(PROVIDER, CRED_PASSWORD, "pw")
    fake.script_post_response(_login_success_response())
    connector = PelotonConnector(credential_store, session=fake)
    connector.authenticate()
    return connector


# Live-evidence-shaped fixture (BO's real-account capture, 2026-10-10):
# GET /api/ride/metadata_mappings returns class_types/instructors as LISTS
# of objects, not id-keyed dicts. These two methods are thin pass-throughs
# (they don't interpret the body), so this only has to round-trip — the
# shape itself is interpreted by trainiq.export.classes, tested separately.
_METADATA_MAPPINGS_RESPONSE = {
    "class_types": [
        {"id": "pz-id", "name": "Power Zone", "fitness_discipline": "cycling", "is_active": True},
        {"id": "li-id", "name": "Low Impact", "fitness_discipline": "cycling", "is_active": True},
        {"id": "climb-id", "name": "Climb", "fitness_discipline": "cycling", "is_active": True},
    ],
    "instructors": [
        {"id": "inst-1", "name": "Matt Wilpers"},
        {"id": "inst-2", "name": "Ally Love"},
    ],
}

# GET /api/v2/ride/archived.
_ARCHIVED_RESPONSE = {
    "data": [
        {
            "id": "class-1", "title": "45 min Power Zone Ride", "instructor_id": "inst-1",
            "duration": 2700, "difficulty_estimate": 7.5, "original_air_time": 1760000000,
        },
    ],
    "total": 391,
}


# --- fetch_ride_metadata_mappings() ----------------------------------------

def test_fetch_ride_metadata_mappings_returns_parsed_body(credential_store):
    fake = FakeSession()
    connector = _authenticated_connector(credential_store, fake)
    fake.script_get_response(FakeResponse(200, _METADATA_MAPPINGS_RESPONSE))

    result = connector.fetch_ride_metadata_mappings()

    assert result == _METADATA_MAPPINGS_RESPONSE
    assert fake.get_calls[-1]["url"] == "https://api.onepeloton.com/api/ride/metadata_mappings"


def test_fetch_ride_metadata_mappings_before_authenticate_raises_authentication_error(credential_store):
    fake = FakeSession()
    connector = PelotonConnector(credential_store, session=fake)

    with pytest.raises(AuthenticationError):
        connector.fetch_ride_metadata_mappings()


def test_fetch_ride_metadata_mappings_401_raises_authentication_error(credential_store):
    fake = FakeSession()
    connector = _authenticated_connector(credential_store, fake)
    fake.script_get_response(FakeResponse(401, {}))

    with pytest.raises(AuthenticationError):
        connector.fetch_ride_metadata_mappings()


def test_fetch_ride_metadata_mappings_429_raises_transient_error(credential_store):
    fake = FakeSession()
    connector = _authenticated_connector(credential_store, fake)
    fake.script_get_response(FakeResponse(429, {}, headers={"Retry-After": "30"}))

    with pytest.raises(TransientError) as exc_info:
        connector.fetch_ride_metadata_mappings()
    assert exc_info.value.retry_after_s == 30.0


# --- fetch_archived_classes() ----------------------------------------------

def test_fetch_archived_classes_sends_cycling_only_query(credential_store):
    fake = FakeSession()
    connector = _authenticated_connector(credential_store, fake)
    fake.script_get_response(FakeResponse(200, _ARCHIVED_RESPONSE))

    result = connector.fetch_archived_classes("pz-id", 2700, limit=8)

    assert result == _ARCHIVED_RESPONSE
    call = fake.get_calls[-1]
    assert call["url"] == "https://api.onepeloton.com/api/v2/ride/archived"
    assert call["params"] == {
        "browse_category": "cycling", "class_type_id": "pz-id", "duration": 2700,
        "sort_by": "original_air_time", "desc": "true", "limit": 8, "page": 0,
    }


def test_fetch_archived_classes_before_authenticate_raises_authentication_error(credential_store):
    fake = FakeSession()
    connector = PelotonConnector(credential_store, session=fake)

    with pytest.raises(AuthenticationError):
        connector.fetch_archived_classes("pz-id", 2700)


def test_fetch_archived_classes_404_returns_none(credential_store):
    fake = FakeSession()
    connector = _authenticated_connector(credential_store, fake)
    fake.script_get_response(FakeResponse(404, {}))

    assert connector.fetch_archived_classes("pz-id", 2700) is None


def test_fetch_archived_classes_5xx_raises_transient_error(credential_store):
    fake = FakeSession()
    connector = _authenticated_connector(credential_store, fake)
    fake.script_get_response(FakeResponse(503, {}))

    with pytest.raises(TransientError):
        connector.fetch_archived_classes("pz-id", 2700)
