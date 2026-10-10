"""
tests/test_live_fixtures.py — regression tests against real captured API
responses (issue #83).

Four real-data bugs (#45, #46, #71, #72) previously passed QA on
hand-written fixtures because those fixtures didn't match the real APIs'
actual shapes. Every test in this module therefore runs the EXISTING,
real production code path (connectors' normalize()/parsing helpers, the
class-catalog builder, the export data layer and renderers) directly
against the real captured files in tests/fixtures/live/ — see that
directory's README.md for the shape gotcha each file documents. Expected
values are derived from each fixture's own content (and the README's
documented gotchas), never invented.

Constraints (AC 11): read-only — fixtures are only ever opened "r", never
written — and no network at all. The autouse `no_network` fixture below
fails loudly on any real `socket.socket.connect()`, so an accidental live
HTTP call cannot silently succeed, hang, or be swallowed by a try/except
somewhere in the call path.
"""

from __future__ import annotations

import json
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

import pytest

from trainiq.connectors.eufy import EufyConnector
from trainiq.connectors.peloton import (
    CRED_EMAIL as PELOTON_CRED_EMAIL,
    CRED_PASSWORD as PELOTON_CRED_PASSWORD,
    PERFORMANCE_FETCH_STATUS_OK,
    PROVIDER as PELOTON_PROVIDER,
    SESSION_RIDE_ID_FIELD,
    PelotonConnector,
    _extract_ride_metadata,
    _parse_performance_response,
    _resolve_distance_m,
    _resolve_distance_unit_token,
)
from trainiq.connectors.strava_streams import derive_stream_metrics
from trainiq.connectors.strava_unofficial import (
    PROVIDER as STRAVA_UNOFFICIAL_PROVIDER,
    StravaUnofficialConnector,
    _convert_local_epoch_to_utc,
    _parse_offset_aware_start_time,
)
from trainiq.credentials.store import CredentialStore
from trainiq.dedup.detector import primary_activity_ids
from trainiq.export import classes, last_done, load, performance, profile, recent, weight
from trainiq.export.data import load_activities, load_weigh_ins, parse_timestamp, resolve_as_of
from trainiq.normalization.taxonomy import Discipline, map_discipline
from trainiq.storage.schema import open_db

from tests.conftest import insert_activity

LIVE = Path(__file__).parent / "fixtures" / "live"


def live(name: str) -> Any:
    """Loads `tests/fixtures/live/{name}.json`'s `"response"` key — opened
    "r" only, never written. For every fixture but `db_samples`, that
    value is the single API payload. `db_samples`'s `"response"` is
    itself the whole-dict shape (every table, by name) — returned here
    exactly as captured, not narrowed further."""
    with open(LIVE / f"{name}.json", "r", encoding="utf-8") as f:
        return json.load(f)["response"]


# --- AC 11: read-only, no network, enforced -----------------------------

@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    """Fails loudly, rather than hanging or silently "working," if any
    code path under test tries a real network call. Every HTTP-backed
    case in this module injects a fake session instead of the connector's
    default `_RequestsSession`, so this should never actually fire — it
    exists to make a future accidental real call impossible to miss."""

    def _refuse(*args, **kwargs):
        raise AssertionError(
            "test_live_fixtures.py must never make a real network call "
            "(AC 11) — a fake session should have been injected instead"
        )

    monkeypatch.setattr("socket.socket.connect", _refuse)
    monkeypatch.setattr("socket.socket.connect_ex", _refuse)


@pytest.fixture(autouse=True)
def in_memory_keyring():
    """Same pattern as tests/test_peloton_connector.py,
    tests/test_strava_unofficial_connector.py, tests/test_eufy_connector.py
    — CredentialStore backs secrets with the OS keyring by default, which
    this sandbox cannot reach."""
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


# --- AC 1: coverage guard — every fixture file is actually used here ----

def test_every_live_fixture_file_is_referenced_by_name():
    """A future fixture that nobody wires up must fail this test, not pass
    silently — the whole point of #83 is that an unexercised fixture is as
    dangerous as no fixture at all."""
    source = Path(__file__).read_text(encoding="utf-8")
    fixture_stems = sorted(p.stem for p in LIVE.glob("*.json"))

    assert fixture_stems, "tests/fixtures/live/ has no *.json fixtures — README misconfigured?"
    missing = [stem for stem in fixture_stems if stem not in source]
    assert missing == [], f"fixture(s) not referenced by name anywhere in this module: {missing}"


# --- shared small fakes for the one HTTP-backed Peloton case (case 2) ---

class _FakeResponse:
    def __init__(self, status_code: int, json_body: dict | None = None, headers: dict | None = None):
        self.status_code = status_code
        self._json_body = json_body or {}
        self.headers = headers or {}

    def json(self):
        return self._json_body


class _FakePelotonSession:
    """Minimal fake mirroring tests/test_peloton_connector.py's
    FakePelotonSession — just enough for this module's one HTTP-backed
    case (fetch_class_session(), case 2): a scripted login POST and a
    scripted GET response queue."""

    def __init__(self):
        self._post_response: _FakeResponse | None = None
        self._get_responses: list[_FakeResponse] = []

    def script_post_response(self, response: _FakeResponse):
        self._post_response = response

    def script_get_response(self, response: _FakeResponse):
        self._get_responses.append(response)

    def post(self, url, json=None, headers=None):
        return self._post_response

    def get(self, url, params=None, headers=None):
        return self._get_responses.pop(0)


class TestPeloton:
    # --- case 1: peloton_user_workouts_page -----------------------------

    def test_workouts_page_items_normalize_without_raising_and_never_guess_distance(self, credential_store):
        """AC2: PelotonConnector.normalize() must not raise on any real
        workouts-page item, and distance_m must be None (never fabricated
        from the list endpoint's own unreliable `distance` field — issue
        #45/#57) when no performance summary has been attached this pass."""
        connector = PelotonConnector(credential_store)
        response = live("peloton_user_workouts_page")

        for item in response["data"]:
            result = connector.normalize(item)

            assert result["distance_m"] is None
            # start_time is passed through unchanged (an epoch int — this
            # project stores Peloton's start_time as epoch seconds, per
            # db_samples.json's column_types_note), and that epoch value
            # correctly represents a real UTC instant (no raise converting
            # it, and the wall-clock year is sane).
            assert result["start_time"] == item["start_time"]
            as_utc = datetime.fromtimestamp(result["start_time"], tz=timezone.utc)
            assert 2020 <= as_utc.year <= 2030

    def test_workouts_page_item_distance_m_only_when_performance_summary_attached(self, credential_store):
        """AC2: distance_m = the performance summary's own value times its
        own resolved unit's multiplier, built the SAME way download() does
        (peloton.py lines ~1056-1071): merge
        _parse_performance_response(peloton_performance_graph) onto the
        workout dict under the `_distance_value`/`_distance_unit`/
        `_performance_fetch_status` keys normalize() actually reads."""
        connector = PelotonConnector(credential_store)
        item = live("peloton_user_workouts_page")["data"][0]
        performance_graph_response = live("peloton_performance_graph")
        performance_fields = _parse_performance_response(performance_graph_response)
        unit_token = _resolve_distance_unit_token(performance_fields["distance_unit_raw"])
        assert unit_token is not None  # sanity: the fixture's own display_unit must resolve

        raw = {
            **item,
            "_performance_fetch_status": PERFORMANCE_FETCH_STATUS_OK,
            "_avg_hr": performance_fields["avg_hr"],
            "_max_hr": performance_fields["max_hr"],
            "_max_power": performance_fields["max_power"],
            "_distance_value": performance_fields["distance_value"],
            "_distance_unit": unit_token,
        }

        result = connector.normalize(raw)

        expected_distance_m = _resolve_distance_m(performance_fields["distance_value"], unit_token)
        assert expected_distance_m is not None
        assert result["distance_m"] == pytest.approx(expected_distance_m)
        assert result["avg_hr"] == performance_fields["avg_hr"]
        assert result["max_hr"] == performance_fields["max_hr"]
        assert result["max_power"] == performance_fields["max_power"]

    # --- case 2: peloton_session -----------------------------------------

    def test_session_fixture_resolves_to_class_ride_id_not_session_id(self, credential_store):
        """Issue #46 -> #58: fetch_class_session()'s SESSION_RIDE_ID_FIELD
        lookup must resolve to the fixture's own `ride_id` (the class),
        never its `id` (the session itself)."""
        response = live("peloton_session")
        fake = _FakePelotonSession()
        fake.script_post_response(_FakeResponse(200, {"session_id": "fake-session"}))
        credential_store.set(PELOTON_PROVIDER, PELOTON_CRED_EMAIL, "athlete@example.com")
        credential_store.set(PELOTON_PROVIDER, PELOTON_CRED_PASSWORD, "pw")
        connector = PelotonConnector(credential_store, session=fake)
        assert connector.authenticate() is True
        fake.script_get_response(_FakeResponse(200, response))

        session = connector.fetch_class_session("some-peloton-id")

        assert session[SESSION_RIDE_ID_FIELD] == response["ride_id"]
        assert session[SESSION_RIDE_ID_FIELD] != response["id"]

    # --- case 3: peloton_ride_details ------------------------------------

    def test_ride_details_extracts_class_metadata_from_nested_ride_and_top_level_class_types(self):
        """Issue #58: `ride` is nested, `class_types` a top-level sibling
        list — _extract_ride_metadata() must read both correctly."""
        response = live("peloton_ride_details")
        ride_id = response["ride"]["id"]

        fields = _extract_ride_metadata(response, ride_id)

        assert fields["activity_title"] == response["ride"]["title"]
        assert fields["instructor_name"] == response["ride"]["instructor"]["name"]
        assert fields["class_type"] == ", ".join(t["name"] for t in response["class_types"])
        assert fields["planned_duration_s"] == response["ride"]["duration"]
        assert fields["provider_class_id"] == ride_id
        assert fields["difficulty_estimate"] == response["ride"]["difficulty_estimate"]

    # --- case 4: peloton_performance_graph --------------------------------

    def test_performance_graph_parses_hr_power_distance_by_slug_not_position(self):
        """Issue #47/#57: metrics/summaries are looked up by `slug`, never
        positionally."""
        response = live("peloton_performance_graph")

        result = _parse_performance_response(response)

        hr_metric = next(m for m in response["metrics"] if m["slug"] == "heart_rate")
        output_metric = next(m for m in response["metrics"] if m["slug"] == "output")
        distance_summary = next(s for s in response["summaries"] if s["slug"] == "distance")

        assert result["avg_hr"] == round(hr_metric["average_value"])
        assert result["max_hr"] == round(hr_metric["max_value"])
        assert result["max_power"] == round(output_metric["max_value"])
        assert result["distance_value"] == distance_summary["value"]
        assert result["distance_unit_raw"] == distance_summary["display_unit"]

    # --- case 5: peloton_metadata_mappings + peloton_archived_rides -------

    def test_metadata_mappings_and_archived_rides_through_class_catalog_builder(self, db):
        """Issue #72: classes.build() against a fake ClassCatalog that
        returns the two real fixtures, seeded with one Peloton cycling
        history row whose class_type is a tag the real metadata_mappings
        fixture actually carries (picked from the fixture itself, never
        hardcoded) — must yield status "ok" and at least one candidate."""
        metadata = live("peloton_metadata_mappings")
        archived = live("peloton_archived_rides")

        active_cycling_types = [
            c for c in metadata["class_types"]
            if c.get("fitness_discipline") == "cycling" and c.get("is_active")
        ]
        assert active_cycling_types  # sanity: the fixture has >=1 usable tag
        seed_tag = active_cycling_types[0]["name"]

        activity_id = insert_activity(
            db, provider="peloton", external_id="live-fixture-class-catalog",
            start_time="2026-10-01T07:00:00+00:00", discipline="cycling",
            class_type=seed_tag,
        )

        class _FixtureCatalog:
            def fetch_ride_metadata_mappings(self):
                return metadata

            def fetch_archived_classes(self, class_type_id, duration_s, limit=8):
                return archived

        data = classes.build(db, {activity_id}, as_of=date(2026, 10, 10), catalog=_FixtureCatalog())

        assert data["status"] == "ok"
        candidates = [c for section in data["sections"] for c in section["classes"]]
        assert len(candidates) >= 1

    # --- case 6: peloton_api_me (issue #45 fix) ---------------------------

    def test_api_me_has_no_distance_unit_field_degrades_safely(self):
        """Issue #45 -> #57: the BO's real /api/me response has NO
        distance_unit field at all (not merely an unrecognized value) —
        _resolve_distance_unit_token(None) must degrade to None, never
        raise or guess a unit."""
        response = live("peloton_api_me")
        assert "distance_unit" not in response  # the exact gotcha this fixture documents

        assert _resolve_distance_unit_token(response.get("distance_unit")) is None


class TestStrava:
    # --- case 7: strava_training_activities_page --------------------------

    def test_training_activities_normalize_uses_start_time_not_local_raw_and_maps_sport(self, credential_store):
        """Issue #43: the UTC start_time must derive from the fixture's own
        `start_time` field, never from `start_date_local_raw` (a LOCAL
        wall-clock epoch, not UTC, despite its name) — proven here by
        showing the two inputs actually disagree on this real record, and
        that normalize() picked the one that matches `start_time`."""
        connector = StravaUnofficialConnector(credential_store, local_timezone="UTC")
        response = live("strava_training_activities_page")

        for model in response["models"]:
            result = connector.normalize(model)

            correct_from_start_time = _parse_offset_aware_start_time(model["start_time"])
            wrong_from_local_raw = _convert_local_epoch_to_utc(model["start_date_local_raw"], "UTC")
            assert correct_from_start_time != wrong_from_local_raw  # the fixture's own fields disagree
            assert result["start_time"] == correct_from_start_time
            assert result["start_time"] != wrong_from_local_raw

            # Every item in this real page is a Ride -> sport is mapped,
            # not left as the unmapped raw string.
            assert model["activity_type_display_name"] == "Ride"
            discipline = map_discipline(STRAVA_UNOFFICIAL_PROVIDER, result["discipline_raw"])
            assert discipline == Discipline.CYCLING

    # --- case 8: strava_activity_streams ------------------------------------

    def test_activity_streams_derive_plausible_metrics_and_none_for_missing_sensor(self):
        """Issue #50: derive_stream_metrics() on the real streams response.
        This real capture's series is trimmed to 8 points covering ~22.7 m
        total (README: "long lists and time series are trimmed") — too
        short for the >=100 m pace guard in
        strava_streams._avg_pace_s_per_km(), so pace is correctly None on
        this specific fixture (a real, derived-from-the-fixture result, not
        an invented expectation); avg_hr and moving_time_s are still
        populated and plausible. A missing sensor key (heartrate) must
        yield None, never a fabricated 0."""
        response = live("strava_activity_streams")

        result = derive_stream_metrics(response)

        assert result["avg_hr"] is not None
        assert 30 <= result["avg_hr"] <= 220  # plausible human heart rate
        assert result["moving_time_s"] is not None
        assert 0 <= result["moving_time_s"] <= response["time"][-1] - response["time"][0]
        assert response["distance"][-1] < 100  # confirms the pace-guard reasoning above
        assert result["avg_pace_s_per_km"] is None

        missing_heartrate = {k: v for k, v in response.items() if k != "heartrate"}
        result_missing = derive_stream_metrics(missing_heartrate)
        assert result_missing["avg_hr"] is None  # missing sensor -> None, never 0
        assert result_missing["max_hr"] is None


class TestEufy:
    # --- case 9: eufy_device_data_item ------------------------------------

    def test_device_data_item_weight_deci_kg_and_body_fat_zero_sentinel(self, credential_store):
        """Issue #1: scale_data.weight is deci-kg, divided by 10 here.
        Issue #42: a literal 0 on body_fat is Eufy's "not measured"
        sentinel, normalized to None. This real capture's own body_fat
        reading is non-zero (a genuine measurement), so the zero-sentinel
        path is exercised on a copy with body_fat forced to 0 — the one
        value this specific capture doesn't itself contain — while the
        deci-kg conversion is asserted straight off the real captured
        value."""
        connector = EufyConnector(credential_store)
        item = live("eufy_device_data_item")

        result = connector.normalize(item)

        assert result["weight_kg"] == pytest.approx(item["scale_data"]["weight"] / 10)
        assert item["scale_data"]["body_fat"] != 0
        assert result["body_fat_pct"] == item["scale_data"]["body_fat"]

        zero_body_fat_item = {**item, "scale_data": {**item["scale_data"], "body_fat": 0}}
        zero_result = connector.normalize(zero_body_fat_item)
        assert zero_result["body_fat_pct"] is None


class TestDbExport:
    # --- case 10: db_samples ----------------------------------------------

    def test_db_samples_timestamps_parse_and_export_pipeline_runs_without_raising(self, db, tmp_path):
        """Issue #71: real DB rows (schema v11) with a genuine mix of
        epoch-seconds (Peloton) and ISO 8601 (Strava) TEXT timestamps in
        the SAME table must parse via export.data.parse_timestamp() and
        flow through load_activities()/load_weigh_ins() and every renderer
        without raising — including sorting the mixed set, the exact
        operation #71's bug broke (TypeError comparing a naive and an
        aware/mismatched-type value)."""
        samples = live("db_samples")
        activity_rows = samples["normalized_activities"]
        weigh_in_rows = samples["weigh_ins"]

        # Both real TEXT shapes parse to tz-aware datetimes, no raise.
        for row in activity_rows:
            parsed = parse_timestamp(row["start_time"])
            assert parsed.tzinfo is not None
        for row in weigh_in_rows:
            parsed = parse_timestamp(row["timestamp"])
            assert parsed.tzinfo is not None

        # Insert using the fixture's OWN column names (never the fixture
        # itself) — see module docstring's AC 12 note: if these ever
        # diverge from storage/schema.py's actual columns, this INSERT
        # fails loudly, which is itself the finding to act on.
        activity_columns = list(activity_rows[0].keys())
        for row in activity_rows:
            db.execute(
                f"INSERT INTO normalized_activities ({', '.join(activity_columns)}) "
                f"VALUES ({', '.join('?' for _ in activity_columns)})",
                tuple(row[c] for c in activity_columns),
            )
        weigh_in_columns = list(weigh_in_rows[0].keys())
        for row in weigh_in_rows:
            db.execute(
                f"INSERT INTO weigh_ins ({', '.join(weigh_in_columns)}) "
                f"VALUES ({', '.join('?' for _ in weigh_in_columns)})",
                tuple(row[c] for c in weigh_in_columns),
            )
        athlete_profile_rows = samples.get("athlete_profile") or []
        for row in athlete_profile_rows:
            columns = list(row.keys())
            db.execute(
                f"INSERT INTO athlete_profile ({', '.join(columns)}) "
                f"VALUES ({', '.join('?' for _ in columns)})",
                tuple(row[c] for c in columns),
            )
        db.commit()

        ids = primary_activity_ids(db)
        as_of = resolve_as_of(db, None)

        activities = load_activities(db, ids)
        weigh_ins = load_weigh_ins(db)
        assert len(activities) == len(activity_rows)
        # The mixed epoch/ISO set sorts cleanly — load_activities() already
        # sorts internally (that call above didn't raise), and re-sorting
        # the resulting start_times here pins that no TypeError is lurking
        # in a comparison pytest's internals might not otherwise exercise.
        sorted(a.start_time for a in activities)
        sorted(w.timestamp for w in weigh_ins)

        config_path = tmp_path / "config.json"
        recent.render(db, ids, as_of)
        load.render(db, ids, as_of)
        performance.render(db, ids, as_of)
        last_done.render(db, ids, as_of)
        weight.render(db, config_path, as_of)
        profile.render(db, config_path, as_of, ids)
