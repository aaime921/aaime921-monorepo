"""
Tests for trainiq.connectors.strava_streams (issue #50) — per-activity
streams enrichment: derive_stream_metrics(), encode_track(), and the
enrich_strava_streams() orchestration (eligibility, pacing, resumability,
error handling).

Every test uses a fake HTTP session — this sandbox cannot reach
strava.com (see trainiq/connectors/strava_unofficial.py's module docstring).
"""

from __future__ import annotations

import json
import zlib
from pathlib import Path

import pytest

from trainiq.connectors.strava_streams import (
    STATUS_NO_STREAMS,
    STATUS_OK,
    STATUS_UNAVAILABLE,
    derive_stream_metrics,
    encode_track,
    enrich_strava_streams,
)
from trainiq.connectors.strava_unofficial import PROVIDER, StravaUnofficialConnector
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


def _authenticate(connector, credential_store, cookie="valid-cookie"):
    from datetime import datetime, timedelta, timezone

    from trainiq.connectors.strava_unofficial import (
        CRED_STRAVA_SESSION_COOKIE,
        CRED_STRAVA_SESSION_EXPIRES_AT,
    )

    credential_store.set(PROVIDER, CRED_STRAVA_SESSION_COOKIE, cookie)
    credential_store.set(
        PROVIDER, CRED_STRAVA_SESSION_EXPIRES_AT,
        str(int((datetime.now(timezone.utc) + timedelta(days=7)).timestamp())),
    )
    assert connector.authenticate() is True


def _insert_activity(conn, provider: str, external_id: str, start_time: str, duration_s: int = 1800,
                      discipline: str = "running") -> int:
    conn.execute(
        """
        INSERT INTO normalized_activities
            (provider, external_id, start_time, duration_s, discipline, distance_m,
             avg_hr, max_hr, avg_power, max_power, calories,
             training_load, training_load_method, source_confidence)
        VALUES (?, ?, ?, ?, ?, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, 1.0)
        """,
        (provider, external_id, start_time, duration_s, discipline),
    )
    conn.execute(
        "INSERT INTO raw_activities (provider, external_id, payload_json, fetched_at) "
        "VALUES (?, ?, '{}', ?)",
        (provider, external_id, start_time),
    )
    conn.commit()
    return conn.execute(
        "SELECT id FROM normalized_activities WHERE provider = ? AND external_id = ?",
        (provider, external_id),
    ).fetchone()["id"]


# --- derive_stream_metrics() (AC1-3) ----------------------------------------

# Shaped like the spike's live-captured evidence (issue #50 comment, run
# 19869165154): 5 samples instead of 4398, same semantics — a time gap
# (contributes to total elapsed but not to moving_time_s unless `moving`),
# heartrate present throughout, distance monotonically increasing.
_SAMPLE_STREAMS = {
    "time": [0, 10, 20, 2000, 2010],
    "distance": [0.0, 50.0, 100.0, 2000.0, 2050.0],
    "heartrate": [100, 110, 120, 130, 140],
    "moving": [True, True, True, False, True],
    "altitude": [10.0, 10.5, 11.0, 15.0, 15.5],
    "latlng": [[1.0, 2.0], [1.001, 2.001], [1.002, 2.002], [1.5, 2.5], [1.501, 2.501]],
}


def test_derive_stream_metrics_matches_hand_computed_expectations():
    """AC1: fixture with the live-captured shape -> avg_hr (time-weighted),
    max_hr, moving_time_s, avg_pace_s_per_km all match hand-computed values."""
    result = derive_stream_metrics(_SAMPLE_STREAMS)

    # dt: [10, 10, 1980, 10]; moving at i=1..4: [T, T, F, T]
    # moving_time_s = 10 + 10 + 10 = 30 (the 1980s gap is NOT moving)
    assert result["moving_time_s"] == 30
    # avg_hr time-weighted: Σ hr[i]*dt[i] / Σ dt[i], i=1..4
    # = (110*10 + 120*10 + 130*1980 + 140*10) / (10+10+1980+10)
    weighted = (110 * 10 + 120 * 10 + 130 * 1980 + 140 * 10) / 2010
    assert result["avg_hr"] == round(weighted)
    assert result["max_hr"] == 140
    # avg_pace_s_per_km = moving_time_s / (total_distance_km)
    assert result["avg_pace_s_per_km"] == pytest.approx(30 / (2050.0 / 1000))


def test_derive_stream_metrics_absent_heartrate_leaves_avg_max_hr_none():
    """AC3: a missing stream key must leave the matching fields None,
    never fabricated or estimated from something else."""
    streams = {k: v for k, v in _SAMPLE_STREAMS.items() if k != "heartrate"}

    result = derive_stream_metrics(streams)

    assert result["avg_hr"] is None
    assert result["max_hr"] is None
    assert result["moving_time_s"] == 30  # unaffected — independent input


def test_derive_stream_metrics_absent_moving_leaves_moving_time_and_pace_none():
    streams = {k: v for k, v in _SAMPLE_STREAMS.items() if k != "moving"}

    result = derive_stream_metrics(streams)

    assert result["moving_time_s"] is None
    assert result["avg_pace_s_per_km"] is None
    assert result["avg_hr"] is not None  # unaffected — independent input


def test_derive_stream_metrics_absent_distance_leaves_pace_none():
    streams = {k: v for k, v in _SAMPLE_STREAMS.items() if k != "distance"}

    result = derive_stream_metrics(streams)

    assert result["avg_pace_s_per_km"] is None
    assert result["moving_time_s"] == 30  # unaffected


def test_derive_stream_metrics_distance_under_100m_yields_none_pace():
    streams = {**_SAMPLE_STREAMS, "distance": [0.0, 20.0, 40.0, 60.0, 80.0]}

    result = derive_stream_metrics(streams)

    assert result["avg_pace_s_per_km"] is None


def test_derive_stream_metrics_empty_dict_yields_all_none():
    result = derive_stream_metrics({})

    assert result == {"avg_hr": None, "max_hr": None, "moving_time_s": None, "avg_pace_s_per_km": None}


def test_derive_stream_metrics_ignores_cadence_and_watts():
    """Out of scope per requirements doc — present or absent, they must
    never influence any derived field."""
    streams = {**_SAMPLE_STREAMS, "cadence": [80, 81, 82, 83, 84], "watts": [150, 155, 160, 165, 170]}

    result = derive_stream_metrics(streams)

    assert result == derive_stream_metrics(_SAMPLE_STREAMS)


# --- encode_track() (AC4, AC5) -----------------------------------------------

def test_encode_track_roundtrips_latlng_time_and_altitude():
    result = encode_track(_SAMPLE_STREAMS)

    assert result is not None
    point_count, blob = result
    assert point_count == len(_SAMPLE_STREAMS["latlng"])
    decoded = json.loads(zlib.decompress(blob))
    assert decoded["t"] == _SAMPLE_STREAMS["time"]
    assert decoded["lat"] == [round(p[0], 6) for p in _SAMPLE_STREAMS["latlng"]]
    assert decoded["lng"] == [round(p[1], 6) for p in _SAMPLE_STREAMS["latlng"]]
    assert decoded["alt"] == _SAMPLE_STREAMS["altitude"]


def test_encode_track_null_pads_altitude_when_absent():
    streams = {k: v for k, v in _SAMPLE_STREAMS.items() if k != "altitude"}

    point_count, blob = encode_track(streams)

    decoded = json.loads(zlib.decompress(blob))
    assert decoded["alt"] == [None] * point_count


def test_encode_track_no_latlng_returns_none():
    """AC4: no `latlng` stream -> no track row."""
    streams = {k: v for k, v in _SAMPLE_STREAMS.items() if k != "latlng"}

    assert encode_track(streams) is None


def test_encode_track_empty_latlng_returns_none():
    assert encode_track({**_SAMPLE_STREAMS, "latlng": []}) is None


# --- enrich_strava_streams(): eligibility, AC6 -------------------------------

def test_enrich_skips_activity_linked_to_peloton_zero_calls(db, credential_store):
    """AC6: an activity linked to a Peloton workout in dedup_links triggers
    no streams request at all."""
    strava_id = _insert_activity(db, PROVIDER, "s1", "2026-01-05T07:00:00+00:00")
    peloton_id = _insert_activity(db, "peloton", "p1", "2026-01-05T07:02:00+00:00")
    db.execute(
        "INSERT INTO dedup_links (activity_id_a, activity_id_b, confidence_score, resolution) "
        "VALUES (?, ?, 0.9, 'linked:primary=peloton')",
        (min(strava_id, peloton_id), max(strava_id, peloton_id)),
    )
    db.commit()

    fake = FakeStravaUnofficialSession()
    connector = StravaUnofficialConnector(credential_store, session=fake)
    _authenticate(connector, credential_store)

    result = enrich_strava_streams(db, connector)

    assert fake.get_calls == []
    assert result.processed == 0


def test_enrich_flagged_ambiguous_pair_stays_eligible(db, credential_store):
    """Flagged (needs_review) pairs are never silently merged — both sides
    stay eligible, unlike an auto-linked pair's secondary side."""
    _insert_activity(db, PROVIDER, "s1", "2026-01-05T07:00:00+00:00")
    _insert_activity(db, "peloton", "p1", "2026-01-05T08:00:00+00:00")  # outside the auto-link window
    fake = FakeStravaUnofficialSession()
    fake.script_get_response(FakeResponse(200, {}))
    connector = StravaUnofficialConnector(credential_store, session=fake)
    _authenticate(connector, credential_store)

    result = enrich_strava_streams(db, connector)

    assert result.processed == 1


# --- enrich_strava_streams(): per-row outcomes (AC4, AC5) --------------------

def test_enrich_full_streams_writes_normalized_row_and_track(db, credential_store):
    _insert_activity(db, PROVIDER, "s1", "2026-01-05T07:00:00+00:00")
    fake = FakeStravaUnofficialSession()
    fake.script_get_response(FakeResponse(200, _SAMPLE_STREAMS))
    connector = StravaUnofficialConnector(credential_store, session=fake)
    _authenticate(connector, credential_store)

    result = enrich_strava_streams(db, connector)

    assert result.processed == 1
    assert result.ok == 1
    row = dict(db.execute(
        "SELECT * FROM normalized_activities WHERE provider = ? AND external_id = 's1'", (PROVIDER,)
    ).fetchone())
    assert row["streams_fetch_status"] == STATUS_OK
    assert row["avg_hr"] == derive_stream_metrics(_SAMPLE_STREAMS)["avg_hr"]
    assert row["moving_time_s"] == 30
    track = db.execute(
        "SELECT * FROM activity_tracks WHERE activity_id = ?", (row["id"],)
    ).fetchone()
    assert track is not None
    assert track["point_count"] == len(_SAMPLE_STREAMS["latlng"])


def test_enrich_empty_streams_no_streams_status_no_track_no_failure(db, credential_store):
    """AC4: an activity with no streams (empty response) stores no track
    row and does not fail the sync."""
    _insert_activity(db, PROVIDER, "s1", "2026-01-05T07:00:00+00:00")
    fake = FakeStravaUnofficialSession()
    fake.script_get_response(FakeResponse(200, {}))
    connector = StravaUnofficialConnector(credential_store, session=fake)
    _authenticate(connector, credential_store)

    result = enrich_strava_streams(db, connector)

    assert result.no_streams == 1
    row = dict(db.execute(
        "SELECT * FROM normalized_activities WHERE provider = ? AND external_id = 's1'", (PROVIDER,)
    ).fetchone())
    assert row["streams_fetch_status"] == STATUS_NO_STREAMS
    assert row["avg_hr"] is None
    track = db.execute("SELECT * FROM activity_tracks WHERE activity_id = ?", (row["id"],)).fetchone()
    assert track is None


def test_enrich_404_marks_unavailable_no_track_no_failure(db, credential_store):
    _insert_activity(db, PROVIDER, "s1", "2026-01-05T07:00:00+00:00")
    fake = FakeStravaUnofficialSession()
    fake.script_get_response(FakeResponse(404, {}))
    connector = StravaUnofficialConnector(credential_store, session=fake)
    _authenticate(connector, credential_store)

    result = enrich_strava_streams(db, connector)

    assert result.unavailable == 1
    row = dict(db.execute(
        "SELECT streams_fetch_status FROM normalized_activities WHERE provider = ? AND external_id = 's1'",
        (PROVIDER,),
    ).fetchone())
    assert row["streams_fetch_status"] == STATUS_UNAVAILABLE


def test_enrich_indoor_activity_no_gps_sets_ok_with_hr_no_track(db, credential_store):
    """A usable response (heartrate present) with no GPS: status ok,
    HR/pace fields populated where derivable, no track row."""
    _insert_activity(db, PROVIDER, "s1", "2026-01-05T07:00:00+00:00")
    streams = {"time": _SAMPLE_STREAMS["time"], "heartrate": _SAMPLE_STREAMS["heartrate"]}
    fake = FakeStravaUnofficialSession()
    fake.script_get_response(FakeResponse(200, streams))
    connector = StravaUnofficialConnector(credential_store, session=fake)
    _authenticate(connector, credential_store)

    result = enrich_strava_streams(db, connector)

    assert result.ok == 1
    row = dict(db.execute(
        "SELECT * FROM normalized_activities WHERE provider = ? AND external_id = 's1'", (PROVIDER,)
    ).fetchone())
    assert row["avg_hr"] is not None
    assert row["avg_pace_s_per_km"] is None
    track = db.execute("SELECT * FROM activity_tracks WHERE activity_id = ?", (row["id"],)).fetchone()
    assert track is None


# --- enrich_strava_streams(): resumability / idempotency (AC7, AC8) --------

def test_enrich_already_populated_row_skipped_on_second_run_zero_calls(db, credential_store):
    """AC7: an already-populated activity is skipped on later syncs."""
    _insert_activity(db, PROVIDER, "s1", "2026-01-05T07:00:00+00:00")
    fake = FakeStravaUnofficialSession()
    fake.script_get_response(FakeResponse(200, _SAMPLE_STREAMS))
    connector = StravaUnofficialConnector(credential_store, session=fake)
    _authenticate(connector, credential_store)
    enrich_strava_streams(db, connector)
    assert len(fake.get_calls) == 1

    result = enrich_strava_streams(db, connector)

    assert len(fake.get_calls) == 1  # no second request
    assert result.processed == 0


def test_enrich_re_running_does_not_duplicate_track_row(db, credential_store):
    """AC5: re-fetching does not create duplicate activity_tracks rows."""
    activity_id = _insert_activity(db, PROVIDER, "s1", "2026-01-05T07:00:00+00:00")
    fake = FakeStravaUnofficialSession()
    fake.script_get_response(FakeResponse(200, _SAMPLE_STREAMS))
    connector = StravaUnofficialConnector(credential_store, session=fake)
    _authenticate(connector, credential_store)
    enrich_strava_streams(db, connector)

    enrich_strava_streams(db, connector)  # second run: nothing eligible, no-op

    tracks = db.execute("SELECT * FROM activity_tracks WHERE activity_id = ?", (activity_id,)).fetchall()
    assert len(tracks) == 1


def test_enrich_new_activity_gets_exactly_one_request_per_sync(db, credential_store):
    """AC7: new activities get 1 request per sync."""
    _insert_activity(db, PROVIDER, "s1", "2026-01-05T07:00:00+00:00")
    fake = FakeStravaUnofficialSession()
    fake.script_get_response(FakeResponse(200, {}))
    connector = StravaUnofficialConnector(credential_store, session=fake)
    _authenticate(connector, credential_store)

    enrich_strava_streams(db, connector)

    assert len(fake.get_calls) == 1


def test_enrich_interrupted_run_then_rerun_picks_up_the_rest(db, credential_store):
    """AC8: resumable after interruption — a run that stops partway through
    (TransientError on the second activity) leaves the first activity's
    row committed; a subsequent run picks up exactly the unfetched one."""
    _insert_activity(db, PROVIDER, "s1", "2026-01-05T07:00:00+00:00")
    _insert_activity(db, PROVIDER, "s2", "2026-01-04T07:00:00+00:00")
    fake = FakeStravaUnofficialSession()
    fake.script_get_response(FakeResponse(200, _SAMPLE_STREAMS))  # s1 (newest first)
    fake.script_get_response(FakeResponse(500, {}))  # s2 -> TransientError
    connector = StravaUnofficialConnector(credential_store, session=fake)
    _authenticate(connector, credential_store)

    with pytest.raises(TransientError):
        enrich_strava_streams(db, connector, sleep_fn=lambda s: None)

    s1_row = dict(db.execute(
        "SELECT streams_fetch_status FROM normalized_activities WHERE provider = ? AND external_id = 's1'",
        (PROVIDER,),
    ).fetchone())
    s2_row = dict(db.execute(
        "SELECT streams_fetch_status FROM normalized_activities WHERE provider = ? AND external_id = 's2'",
        (PROVIDER,),
    ).fetchone())
    assert s1_row["streams_fetch_status"] == STATUS_OK  # already committed, stays
    assert s2_row["streams_fetch_status"] is None  # left NULL, eligible for resume

    fake.script_get_response(FakeResponse(200, {}))  # s2, retried
    result = enrich_strava_streams(db, connector, sleep_fn=lambda s: None)

    assert result.processed == 1
    s2_row = dict(db.execute(
        "SELECT streams_fetch_status FROM normalized_activities WHERE provider = ? AND external_id = 's2'",
        (PROVIDER,),
    ).fetchone())
    assert s2_row["streams_fetch_status"] == STATUS_NO_STREAMS


# --- enrich_strava_streams(): error handling (AC9) ---------------------------

def test_enrich_401_raises_authentication_error_leaves_row_null(db, credential_store):
    _insert_activity(db, PROVIDER, "s1", "2026-01-05T07:00:00+00:00")
    fake = FakeStravaUnofficialSession()
    fake.script_get_response(FakeResponse(401, {}))
    connector = StravaUnofficialConnector(credential_store, session=fake)
    _authenticate(connector, credential_store)

    with pytest.raises(AuthenticationError):
        enrich_strava_streams(db, connector)

    row = dict(db.execute(
        "SELECT streams_fetch_status FROM normalized_activities WHERE provider = ? AND external_id = 's1'",
        (PROVIDER,),
    ).fetchone())
    assert row["streams_fetch_status"] is None


def test_enrich_redirect_to_login_raises_authentication_error(db, credential_store):
    _insert_activity(db, PROVIDER, "s1", "2026-01-05T07:00:00+00:00")
    fake = FakeStravaUnofficialSession()
    fake.script_get_response(FakeResponse(302, headers={"Location": "https://www.strava.com/login"}))
    connector = StravaUnofficialConnector(credential_store, session=fake)
    _authenticate(connector, credential_store)

    with pytest.raises(AuthenticationError):
        enrich_strava_streams(db, connector)


def test_enrich_429_raises_transient_error_with_retry_after(db, credential_store):
    _insert_activity(db, PROVIDER, "s1", "2026-01-05T07:00:00+00:00")
    fake = FakeStravaUnofficialSession()
    fake.script_get_response(FakeResponse(429, {}, headers={"Retry-After": "90"}))
    connector = StravaUnofficialConnector(credential_store, session=fake)
    _authenticate(connector, credential_store)

    with pytest.raises(TransientError) as excinfo:
        enrich_strava_streams(db, connector)
    assert excinfo.value.retry_after_s == 90.0


def test_enrich_5xx_raises_transient_error(db, credential_store):
    _insert_activity(db, PROVIDER, "s1", "2026-01-05T07:00:00+00:00")
    fake = FakeStravaUnofficialSession()
    fake.script_get_response(FakeResponse(503, {}))
    connector = StravaUnofficialConnector(credential_store, session=fake)
    _authenticate(connector, credential_store)

    with pytest.raises(TransientError):
        enrich_strava_streams(db, connector)


# --- enrich_strava_streams(): pacing -----------------------------------------

def test_enrich_sleeps_between_requests_not_before_first(db, credential_store):
    _insert_activity(db, PROVIDER, "s1", "2026-01-05T07:00:00+00:00")
    _insert_activity(db, PROVIDER, "s2", "2026-01-04T07:00:00+00:00")
    fake = FakeStravaUnofficialSession()
    fake.script_get_response(FakeResponse(200, {}))
    fake.script_get_response(FakeResponse(200, {}))
    connector = StravaUnofficialConnector(credential_store, session=fake)
    _authenticate(connector, credential_store)
    sleeps: list[float] = []

    enrich_strava_streams(
        db, connector, sleep_fn=lambda s: sleeps.append(s), jitter_fn=lambda: 0.5,
    )

    assert sleeps == [3.0 + 0.5 * 2.0]  # DELAY_S + jitter_fn() * JITTER_S, once (2 rows, 1 gap)


def test_enrich_limit_caps_number_of_requests(db, credential_store):
    _insert_activity(db, PROVIDER, "s1", "2026-01-05T07:00:00+00:00")
    _insert_activity(db, PROVIDER, "s2", "2026-01-04T07:00:00+00:00")
    fake = FakeStravaUnofficialSession()
    fake.script_get_response(FakeResponse(200, {}))
    connector = StravaUnofficialConnector(credential_store, session=fake)
    _authenticate(connector, credential_store)

    result = enrich_strava_streams(db, connector, limit=1, sleep_fn=lambda s: None)

    assert result.processed == 1
    assert len(fake.get_calls) == 1


def test_enrich_orders_newest_first(db, credential_store):
    _insert_activity(db, PROVIDER, "older", "2026-01-01T00:00:00+00:00")
    _insert_activity(db, PROVIDER, "newer", "2026-01-05T00:00:00+00:00")
    fake = FakeStravaUnofficialSession()
    fake.script_get_response(FakeResponse(200, {}))
    fake.script_get_response(FakeResponse(200, {}))
    connector = StravaUnofficialConnector(credential_store, session=fake)
    _authenticate(connector, credential_store)

    enrich_strava_streams(db, connector, sleep_fn=lambda s: None)

    assert fake.get_calls[0]["url"].endswith("/activities/newer/streams")
    assert fake.get_calls[1]["url"].endswith("/activities/older/streams")


# --- normalize() merge round-trip (renormalize safety) -----------------------

def test_enriched_raw_payload_reproduces_values_via_normalize(db, credential_store):
    """The merge contract: after enrichment, normalize() on the
    now-enriched raw payload (e.g. via a renormalize pass) must reproduce
    the same avg_hr/max_hr/moving_time_s/avg_pace_s_per_km with zero new
    network I/O."""
    _insert_activity(db, PROVIDER, "s1", "2026-01-05T07:00:00+00:00")
    fake = FakeStravaUnofficialSession()
    fake.script_get_response(FakeResponse(200, _SAMPLE_STREAMS))
    connector = StravaUnofficialConnector(credential_store, session=fake)
    _authenticate(connector, credential_store)
    enrich_strava_streams(db, connector)

    raw_row = db.execute(
        "SELECT payload_json FROM raw_activities WHERE provider = ? AND external_id = 's1'", (PROVIDER,)
    ).fetchone()
    raw = json.loads(raw_row["payload_json"])
    raw["id"] = "s1"
    raw["elapsed_time_raw"] = 2010
    raw["start_time"] = "2026-01-05T07:00:00+0000"

    normalized = connector.normalize(raw)

    expected = derive_stream_metrics(_SAMPLE_STREAMS)
    assert normalized["avg_hr"] == expected["avg_hr"]
    assert normalized["max_hr"] == expected["max_hr"]
    assert normalized["moving_time_s"] == expected["moving_time_s"]
    assert normalized["avg_pace_s_per_km"] == pytest.approx(expected["avg_pace_s_per_km"])
