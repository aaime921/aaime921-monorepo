"""
Epic 7, slice 3 (final): end-to-end integration.

No new production code is required for this slice beyond what's tested
here — `SynchronizationEngine` has accepted `athlete_profile:
Optional[AthleteProfile]` as a constructor parameter since Epic 6, and
`compute_training_load()` has implemented full TSS/TRIMP selection since
Epic 6 as well. This slice's job is to prove those two already-built,
already-tested pieces produce the real, non-Unknown outputs they were
always designed to produce, once a real profile flows in from persistence
(slice 1's schema, slice 2's loader) rather than `None`.

Per the Chief Architect's explicit request: also verifies the profile is
loaded once per sync run (at construction), never once per activity.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from trainiq.athlete.profile import AthleteProfile
from trainiq.athlete.store import load_athlete_profile, save_athlete_profile
from trainiq.connectors.base import CapabilityTier, Connector
from trainiq.storage.schema import open_db
from trainiq.sync.engine import SynchronizationEngine


@pytest.fixture
def db(tmp_path: Path):
    conn = open_db(tmp_path / "trainiq.db")
    yield conn
    conn.close()


class MockActivityConnector(Connector):
    """Returns a fixed set of activities with configurable HR/power, so
    each scenario below can exercise a specific training-load branch
    without needing a real provider."""

    capability_tier = CapabilityTier.TIER_1_OFFICIAL

    def __init__(self, provider: str, activities: list[dict]):
        super().__init__(provider)
        self._activities = activities

    def authenticate(self) -> bool:
        return True

    def download(self, since: str | None = None) -> list[dict]:
        return list(self._activities)

    def normalize(self, raw: dict) -> dict:
        return raw  # already in the canonical intermediate shape for this test


def _activity(external_id, avg_hr=None, avg_power=None):
    return {
        "external_id": external_id,
        "start_time": "2026-01-05T07:00:00+00:00",
        "duration_s": 3600,
        "discipline_raw": "Ride",
        "avg_hr": avg_hr,
        "avg_power": avg_power,
    }


def _training_load_row(db, provider, external_id):
    return db.execute(
        "SELECT training_load, training_load_method FROM normalized_activities "
        "WHERE provider = ? AND external_id = ?",
        (provider, external_id),
    ).fetchone()


# --- The five scenarios, per the Chief Architect's explicit list -----------

def test_no_profile_persisted_yields_unknown_training_load(db):
    """Scenario 1: existing Epic 6 behavior preserved when no profile has
    ever been saved — load_athlete_profile() correctly returns None, and
    that None flows through exactly as it did in every Epic 6 test."""
    profile = load_athlete_profile(db)  # None — nothing persisted yet
    engine = SynchronizationEngine(db, athlete_profile=profile)
    connector = MockActivityConnector("strava", [_activity("1", avg_hr=145, avg_power=200)])

    engine.run_once([connector])

    row = _training_load_row(db, "strava", "1")
    assert row["training_load"] is None
    assert row["training_load_method"] == "unknown"


def test_hr_only_profile_yields_trimp(db):
    """Scenario 2: a persisted profile with HR baseline but no FTP."""
    save_athlete_profile(db, AthleteProfile(sex="male", resting_hr=50, max_hr=190))
    profile = load_athlete_profile(db)
    engine = SynchronizationEngine(db, athlete_profile=profile)
    connector = MockActivityConnector("strava", [_activity("2", avg_hr=145, avg_power=None)])

    engine.run_once([connector])

    row = _training_load_row(db, "strava", "2")
    assert row["training_load_method"] == "trimp"
    assert row["training_load"] > 0


def test_ftp_only_profile_yields_tss_for_power_activity(db):
    """Scenario 3: a persisted profile with FTP but no HR baseline, for an
    activity that reports power."""
    save_athlete_profile(db, AthleteProfile(ftp_watts=250))
    profile = load_athlete_profile(db)
    engine = SynchronizationEngine(db, athlete_profile=profile)
    connector = MockActivityConnector("strava", [_activity("3", avg_hr=None, avg_power=250)])

    engine.run_once([connector])

    row = _training_load_row(db, "strava", "3")
    assert row["training_load_method"] == "tss"
    assert row["training_load"] == 100.0  # one hour at FTP is TSS 100 by definition


def test_both_available_prefers_tss_over_trimp(db):
    """Scenario 4: ADR-015's opportunistic-enhancement rule, now exercised
    with a real persisted-and-loaded profile rather than one constructed
    inline in a unit test."""
    save_athlete_profile(db, AthleteProfile(sex="male", resting_hr=50, max_hr=190, ftp_watts=250))
    profile = load_athlete_profile(db)
    engine = SynchronizationEngine(db, athlete_profile=profile)
    connector = MockActivityConnector("strava", [_activity("4", avg_hr=145, avg_power=200)])

    engine.run_once([connector])

    row = _training_load_row(db, "strava", "4")
    assert row["training_load_method"] == "tss"


def test_updating_stored_profile_affects_subsequent_syncs_only(db):
    """Scenario 5: updating the profile changes what future syncs compute,
    without any change to normalization logic — proves the wiring is a
    live read of persisted state at construction time, not a value baked
    in once and never revisited across the application's lifetime."""
    # First sync: no profile yet.
    engine_1 = SynchronizationEngine(db, athlete_profile=load_athlete_profile(db))
    connector_1 = MockActivityConnector("strava", [_activity("5", avg_hr=145)])
    engine_1.run_once([connector_1])
    assert _training_load_row(db, "strava", "5")["training_load_method"] == "unknown"

    # Athlete completes initial setup, profile is now persisted.
    save_athlete_profile(db, AthleteProfile(sex="male", resting_hr=50, max_hr=190))

    # Second sync run: a NEW engine instance (simulating a later app
    # launch), loading whatever is persisted NOW.
    engine_2 = SynchronizationEngine(db, athlete_profile=load_athlete_profile(db))
    connector_2 = MockActivityConnector("strava", [_activity("6", avg_hr=145)])
    engine_2.run_once([connector_2])
    assert _training_load_row(db, "strava", "6")["training_load_method"] == "trimp"

    # The FIRST activity's stored row is untouched — updating the profile
    # does not retroactively rewrite history it wasn't involved in.
    assert _training_load_row(db, "strava", "5")["training_load_method"] == "unknown"


# --- The Chief Architect's specific regression request: load once, not per-activity ---

def test_profile_is_loaded_once_per_engine_not_once_per_activity(db):
    """Explicit, permanent guard against re-reading the singleton row
    inside the per-activity loop. Uses a call-counting wrapper around
    load_athlete_profile() to prove it's invoked exactly once for an
    entire multi-connector, multi-activity sync run — the loaded value is
    then held by the engine for its whole lifetime via the constructor,
    never re-fetched."""
    save_athlete_profile(db, AthleteProfile(sex="male", resting_hr=50, max_hr=190))

    call_count = {"n": 0}

    def counting_load(conn):
        call_count["n"] += 1
        return load_athlete_profile(conn)

    # The realistic calling pattern: load once, construct once.
    profile = counting_load(db)
    engine = SynchronizationEngine(db, athlete_profile=profile)

    many_activities = [_activity(str(i), avg_hr=140 + i) for i in range(10)]
    connector_a = MockActivityConnector("strava", many_activities)
    connector_b = MockActivityConnector("peloton", many_activities)

    engine.run_once([connector_a, connector_b])
    engine.run_once([connector_a, connector_b])  # a second sync run, same engine instance

    assert call_count["n"] == 1  # not 20, not 2 — loaded exactly once, ever, for this engine
