"""
Tests for Epic 1 — Strava Connector (Features 1.1, 1.2, 1.3-extraction-only).

Every test uses a fake stravalib Client — this sandbox cannot reach
api.strava.com (see module docstring in trainiq/connectors/strava.py).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from stravalib import exc as stravalib_exc

from trainiq.connectors.strava import (
    CRED_ACCESS_TOKEN,
    CRED_EXPIRES_AT,
    CRED_REFRESH_TOKEN,
    PROVIDER,
    StravaConnector,
)
from trainiq.credentials.store import CredentialStore
from trainiq.storage.schema import open_db
from trainiq.sync.engine import AuthenticationError, SynchronizationEngine, TransientError


@pytest.fixture(autouse=True)
def strava_app_credentials(monkeypatch):
    monkeypatch.setenv("STRAVA_CLIENT_ID", "12345")
    monkeypatch.setenv("STRAVA_CLIENT_SECRET", "test-client-secret")


@pytest.fixture(autouse=True)
def in_memory_keyring(monkeypatch):
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


class FakeStravalibClient:
    """Stand-in for stravalib.Client — no network calls, fully scripted."""

    def __init__(self):
        self.access_token = None
        self.refresh_calls: list[dict] = []
        self.get_activities_calls: list[dict] = []
        self._refresh_response = None
        self._refresh_exception = None
        self._activities: list[SimpleNamespace] = []
        self._get_activities_exception = None

    def script_refresh_success(self, access_token: str, refresh_token: str, expires_at: int):
        self._refresh_response = SimpleNamespace(
            access_token=access_token, refresh_token=refresh_token, expires_at=expires_at
        )

    def script_refresh_exception(self, exc: Exception):
        self._refresh_exception = exc

    def script_activities(self, activities: list[SimpleNamespace]):
        self._activities = activities

    def script_get_activities_exception(self, exc: Exception):
        self._get_activities_exception = exc

    def refresh_access_token(self, client_id, client_secret, refresh_token):
        self.refresh_calls.append(
            {"client_id": client_id, "client_secret": client_secret, "refresh_token": refresh_token}
        )
        if self._refresh_exception:
            raise self._refresh_exception
        return self._refresh_response

    def get_activities(self, after=None, before=None, limit=None):
        self.get_activities_calls.append({"after": after})
        if self._get_activities_exception:
            raise self._get_activities_exception
        return iter(self._activities)


def _fake_activity(
    id_=123, start_date="2026-01-05T07:00:00+00:00", elapsed_time=3600,
    sport_type="Ride", type_="Ride", avg_hr=145.0, max_hr=168, avg_watts=210.0,
    max_watts=310, distance=30000.0,
):
    return SimpleNamespace(
        id=id_,
        start_date=datetime.fromisoformat(start_date),
        elapsed_time=elapsed_time,
        sport_type=sport_type,
        type=type_,
        average_heartrate=avg_hr,
        max_heartrate=max_hr,
        average_watts=avg_watts,
        max_watts=max_watts,
        distance=distance,
    )


# --- Feature 1.1: Authentication -----------------------------------------

def test_authenticate_returns_false_when_never_connected(credential_store):
    fake = FakeStravalibClient()
    connector = StravaConnector(credential_store, stravalib_client=fake)
    assert connector.authenticate() is False
    assert fake.refresh_calls == []


def test_authenticate_reuses_valid_unexpired_access_token(credential_store):
    future_expiry = int((datetime.now(timezone.utc) + timedelta(hours=1)).timestamp())
    credential_store.set(PROVIDER, CRED_REFRESH_TOKEN, "existing-refresh-token")
    credential_store.set(PROVIDER, CRED_ACCESS_TOKEN, "still-valid-access-token")
    credential_store.set(PROVIDER, CRED_EXPIRES_AT, str(future_expiry))

    fake = FakeStravalibClient()
    connector = StravaConnector(credential_store, stravalib_client=fake)

    assert connector.authenticate() is True
    assert fake.refresh_calls == []  # must NOT refresh when still valid
    assert fake.access_token == "still-valid-access-token"


def test_authenticate_refreshes_when_access_token_expired(credential_store):
    past_expiry = int((datetime.now(timezone.utc) - timedelta(hours=1)).timestamp())
    credential_store.set(PROVIDER, CRED_REFRESH_TOKEN, "old-refresh-token")
    credential_store.set(PROVIDER, CRED_ACCESS_TOKEN, "expired-access-token")
    credential_store.set(PROVIDER, CRED_EXPIRES_AT, str(past_expiry))

    fake = FakeStravalibClient()
    new_expiry = int((datetime.now(timezone.utc) + timedelta(hours=6)).timestamp())
    fake.script_refresh_success("new-access-token", "new-refresh-token", new_expiry)
    connector = StravaConnector(credential_store, stravalib_client=fake)

    assert connector.authenticate() is True
    assert len(fake.refresh_calls) == 1
    assert fake.refresh_calls[0]["refresh_token"] == "old-refresh-token"
    assert fake.access_token == "new-access-token"


def test_refresh_persists_the_new_refresh_token_not_the_old_one(credential_store):
    """This is THE bug Milestone 1 flagged: Strava rotates the refresh
    token on every use and invalidates the previous one. A store that still
    has the OLD refresh token after a successful refresh is broken."""
    credential_store.set(PROVIDER, CRED_REFRESH_TOKEN, "old-refresh-token")
    fake = FakeStravalibClient()
    new_expiry = int((datetime.now(timezone.utc) + timedelta(hours=6)).timestamp())
    fake.script_refresh_success("new-access-token", "brand-new-refresh-token", new_expiry)
    connector = StravaConnector(credential_store, stravalib_client=fake)

    connector.authenticate()

    stored_refresh = credential_store.get(PROVIDER, CRED_REFRESH_TOKEN)
    assert stored_refresh == "brand-new-refresh-token"
    assert stored_refresh != "old-refresh-token"


def test_refresh_survives_across_a_fresh_connector_instance(credential_store):
    """Simulates an app restart 6+ hours later: a brand new StravaConnector
    must still be able to authenticate using only what's in the store."""
    fake_1 = FakeStravalibClient()
    fake_1.script_refresh_success("access-1", "refresh-1", int(datetime.now(timezone.utc).timestamp()) + 21600)
    credential_store.set(PROVIDER, CRED_REFRESH_TOKEN, "initial-refresh-token")
    StravaConnector(credential_store, stravalib_client=fake_1).authenticate()

    # New process, new connector instance, same on-disk/keychain state.
    past_expiry = int((datetime.now(timezone.utc) - timedelta(hours=1)).timestamp())
    credential_store.rotate(PROVIDER, CRED_EXPIRES_AT, str(past_expiry))  # force refresh path
    fake_2 = FakeStravalibClient()
    fake_2.script_refresh_success("access-2", "refresh-2", int(datetime.now(timezone.utc).timestamp()) + 21600)
    connector_2 = StravaConnector(credential_store, stravalib_client=fake_2)

    assert connector_2.authenticate() is True
    assert fake_2.refresh_calls[0]["refresh_token"] == "refresh-1"  # picked up from Feature 1's rotation


def test_refresh_rate_limited_raises_transient_error_not_swallowed(credential_store):
    credential_store.set(PROVIDER, CRED_REFRESH_TOKEN, "refresh-token")
    fake = FakeStravalibClient()
    fake.script_refresh_exception(stravalib_exc.RateLimitExceeded("rate limited"))
    connector = StravaConnector(credential_store, stravalib_client=fake)

    with pytest.raises(TransientError):
        connector.authenticate()


def test_refresh_auth_error_returns_false(credential_store):
    credential_store.set(PROVIDER, CRED_REFRESH_TOKEN, "revoked-refresh-token")
    fake = FakeStravalibClient()
    fake.script_refresh_exception(stravalib_exc.AuthError("invalid_grant"))
    connector = StravaConnector(credential_store, stravalib_client=fake)

    assert connector.authenticate() is False


def test_missing_app_credentials_raises_clear_error(credential_store, monkeypatch):
    monkeypatch.delenv("STRAVA_CLIENT_ID", raising=False)
    monkeypatch.delenv("STRAVA_CLIENT_SECRET", raising=False)
    credential_store.set(PROVIDER, CRED_REFRESH_TOKEN, "some-refresh-token")
    fake = FakeStravalibClient()
    connector = StravaConnector(credential_store, stravalib_client=fake)

    with pytest.raises(RuntimeError, match="STRAVA_CLIENT_ID"):
        connector.authenticate()


# --- Feature 1.2: Sync -----------------------------------------------------

def test_download_passes_since_as_after_datetime(credential_store):
    fake = FakeStravalibClient()
    fake.script_activities([_fake_activity()])
    connector = StravaConnector(credential_store, stravalib_client=fake)

    connector.download(since="2026-01-01T00:00:00+00:00")

    assert fake.get_activities_calls[0]["after"] == datetime.fromisoformat("2026-01-01T00:00:00+00:00")


def test_download_with_no_checkpoint_passes_after_none(credential_store):
    fake = FakeStravalibClient()
    fake.script_activities([])
    connector = StravaConnector(credential_store, stravalib_client=fake)

    connector.download(since=None)

    assert fake.get_activities_calls[0]["after"] is None


def test_download_rate_limited_translates_to_transient_error(credential_store):
    fake = FakeStravalibClient()
    fake.script_get_activities_exception(stravalib_exc.RateLimitExceeded("rate limited"))
    connector = StravaConnector(credential_store, stravalib_client=fake)

    with pytest.raises(TransientError):
        connector.download()


def test_download_rate_limited_passes_through_stravalib_timeout_per_adr_037(credential_store):
    """ADR-037: stravalib's authoritative wait time must survive the
    translation into TransientError, not be discarded."""
    fake = FakeStravalibClient()
    fake.script_get_activities_exception(
        stravalib_exc.RateLimitExceeded("rate limited", timeout=837.0)
    )
    connector = StravaConnector(credential_store, stravalib_client=fake)

    with pytest.raises(TransientError) as excinfo:
        connector.download()

    assert excinfo.value.retry_after_s == 837.0


def test_refresh_rate_limited_passes_through_stravalib_timeout_per_adr_037(credential_store):
    credential_store.set(PROVIDER, CRED_REFRESH_TOKEN, "refresh-token")
    fake = FakeStravalibClient()
    fake.script_refresh_exception(stravalib_exc.RateLimitExceeded("rate limited", timeout=612.0))
    connector = StravaConnector(credential_store, stravalib_client=fake)

    with pytest.raises(TransientError) as excinfo:
        connector.authenticate()

    assert excinfo.value.retry_after_s == 612.0


def test_download_login_required_translates_to_authentication_error(credential_store):
    fake = FakeStravalibClient()
    fake.script_get_activities_exception(stravalib_exc.LoginRequired("session invalid"))
    connector = StravaConnector(credential_store, stravalib_client=fake)

    with pytest.raises(AuthenticationError):
        connector.download()


# --- Feature 1.3 (extraction-only): normalize() -----------------------------

def test_normalize_maps_core_fields(credential_store):
    fake = FakeStravalibClient()
    connector = StravaConnector(credential_store, stravalib_client=fake)
    raw = connector._activity_to_raw_dict(_fake_activity())

    result = connector.normalize(raw)

    assert result["provider"] == "strava"
    assert result["external_id"] == "123"
    assert result["start_time"] == "2026-01-05T07:00:00+00:00"
    assert result["duration_s"] == 3600
    assert result["discipline_raw"] == "Ride"
    assert result["avg_hr"] == 145
    assert result["max_hr"] == 168
    assert result["avg_power"] == 210
    assert result["max_power"] == 310
    assert result["distance_m"] == 30000.0


def test_normalize_never_fabricates_calories(credential_store):
    """Documented limitation: the summary endpoint doesn't report calories.
    It must be None, never guessed."""
    fake = FakeStravalibClient()
    connector = StravaConnector(credential_store, stravalib_client=fake)
    raw = connector._activity_to_raw_dict(_fake_activity())

    result = connector.normalize(raw)

    assert result["calories"] is None


def test_normalize_falls_back_to_type_when_sport_type_absent(credential_store):
    fake = FakeStravalibClient()
    connector = StravaConnector(credential_store, stravalib_client=fake)
    raw = connector._activity_to_raw_dict(_fake_activity(sport_type=None, type_="Run"))

    result = connector.normalize(raw)

    assert result["discipline_raw"] == "Run"


def test_normalize_handles_missing_hr_and_power_as_none_not_zero(credential_store):
    fake = FakeStravalibClient()
    connector = StravaConnector(credential_store, stravalib_client=fake)
    raw = connector._activity_to_raw_dict(
        _fake_activity(avg_hr=None, max_hr=None, avg_watts=None, max_watts=None)
    )

    result = connector.normalize(raw)

    assert result["avg_hr"] is None
    assert result["max_hr"] is None
    assert result["avg_power"] is None
    assert result["max_power"] is None


# --- End-to-end: StravaConnector through the real Synchronization Engine ---

def test_end_to_end_sync_via_synchronization_engine(db, credential_store):
    """Wires the real Sync Engine (Epic 0.6) to a real CredentialStore and a
    fake stravalib Client — proves Feature 1.1 + 1.2 actually work together
    through the already-tested Foundation, not just in isolation."""
    credential_store.set(PROVIDER, CRED_REFRESH_TOKEN, "initial-refresh-token")
    fake = FakeStravalibClient()
    fake.script_refresh_success(
        "access-token", "rotated-refresh-token",
        int((datetime.now(timezone.utc) + timedelta(hours=6)).timestamp()),
    )
    fake.script_activities([
        _fake_activity(id_=1, start_date="2026-01-05T07:00:00+00:00"),
        _fake_activity(id_=2, start_date="2026-01-06T07:00:00+00:00"),
    ])
    connector = StravaConnector(credential_store, stravalib_client=fake)
    engine = SynchronizationEngine(db)

    result = engine.run_once([connector])

    assert result.connector_results[0].records_upserted == 2
    rows = db.execute("SELECT provider, external_id FROM raw_activities ORDER BY external_id").fetchall()
    assert [(r["provider"], r["external_id"]) for r in rows] == [("strava", "1"), ("strava", "2")]

    # Refresh token rotation must have actually happened via the real store.
    assert credential_store.get(PROVIDER, CRED_REFRESH_TOKEN) == "rotated-refresh-token"

    # credentials_metadata (Hardening Finding 2) must reflect the connection.
    meta = db.execute("SELECT connected FROM credentials_metadata WHERE provider = 'strava'").fetchone()
    assert meta["connected"] == 1

    # Epic 6, slice 5: normalized_activities must now actually be
    # populated — this table sat empty since Epic 0 (BL-003) until this
    # slice wired the Normalization Engine into the Sync Engine.
    canonical_rows = db.execute(
        "SELECT discipline, training_load_method FROM normalized_activities "
        "WHERE provider = 'strava' ORDER BY external_id"
    ).fetchall()
    assert len(canonical_rows) == 2
    assert canonical_rows[0]["discipline"] == "cycling"  # Ride -> cycling, per the taxonomy
    assert canonical_rows[0]["training_load_method"] == "unknown"  # no AthleteProfile supplied (Q1)


def test_second_sync_run_only_fetches_activities_after_checkpoint(db, credential_store):
    """Feature 1.2's core promise: after-timestamp polling means a second
    run doesn't re-fetch everything — proven through the real checkpoint
    table, not assumed."""
    credential_store.set(PROVIDER, CRED_REFRESH_TOKEN, "initial-refresh-token")
    engine = SynchronizationEngine(db)

    fake_1 = FakeStravalibClient()
    fake_1.script_refresh_success(
        "access-1", "refresh-1", int((datetime.now(timezone.utc) + timedelta(hours=6)).timestamp())
    )
    fake_1.script_activities([_fake_activity(id_=1, start_date="2026-01-05T07:00:00+00:00")])
    engine.run_once([StravaConnector(credential_store, stravalib_client=fake_1)])

    fake_2 = FakeStravalibClient()
    fake_2.script_activities([_fake_activity(id_=2, start_date="2026-01-06T07:00:00+00:00")])
    # Access token from the first run is still valid, no refresh needed here.
    connector_2 = StravaConnector(credential_store, stravalib_client=fake_2)
    engine.run_once([connector_2])

    called_after = fake_2.get_activities_calls[0]["after"]
    assert called_after == datetime.fromisoformat("2026-01-05T07:00:00+00:00")
    rows = db.execute("SELECT COUNT(*) AS c FROM raw_activities").fetchone()
    assert rows["c"] == 2
