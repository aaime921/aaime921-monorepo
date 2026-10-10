"""Shared fixtures for the export test suite (issue #71). Existing test
files each define their own local `db` fixture (which overrides this one
for them, per pytest's fixture-scoping rules) — this is additive, not a
change to any existing test's behavior."""

from __future__ import annotations

from pathlib import Path

import pytest

from trainiq.storage.schema import open_db


@pytest.fixture
def db(tmp_path: Path):
    conn = open_db(tmp_path / "trainiq.db")
    yield conn
    conn.close()


_ACTIVITY_DEFAULTS = dict(
    provider="strava", external_id=None, start_time=None, duration_s=1800,
    discipline="cycling", distance_m=None, avg_hr=None, max_hr=None,
    avg_power=None, max_power=None, calories=None,
    elevation_gain_m=None, moving_time_s=None, is_indoor=None,
    training_load=None, training_load_method="unknown", source_confidence=0.5,
    activity_title=None, instructor_name=None, class_type=None,
    planned_duration_s=None, provider_class_id=None, sport_type_raw=None,
    difficulty_estimate=None,
    hr_zone_1_s=None, hr_zone_2_s=None, hr_zone_3_s=None, hr_zone_4_s=None, hr_zone_5_s=None,
    effort_points=None, performance_fetch_status=None, avg_pace_s_per_km=None, total_output_kj=None,
)


def insert_activity(conn, **overrides) -> int:
    """Test-only helper: inserts a normalized_activities row with sensible
    defaults, returning its id. `external_id`/`start_time` are required
    overrides (every call site supplies its own)."""
    values = {**_ACTIVITY_DEFAULTS, **overrides}
    columns = list(values.keys())
    cursor = conn.execute(
        f"INSERT INTO normalized_activities ({', '.join(columns)}) "
        f"VALUES ({', '.join('?' for _ in columns)})",
        tuple(values[c] for c in columns),
    )
    conn.commit()
    return cursor.lastrowid


def insert_weigh_in(conn, **overrides) -> int:
    defaults = dict(
        provider="eufy", external_id=None, timestamp=None, weight_kg=None,
        body_fat_pct=None, muscle_mass_pct=None,
        is_weight_flagged_implausible=0, weight_plausibility_reason=None,
        bo_confirmed_valid=0, bo_confirmed_at=None,
        is_body_fat_flagged_implausible=0, body_fat_plausibility_reason=None,
    )
    values = {**defaults, **overrides}
    columns = list(values.keys())
    cursor = conn.execute(
        f"INSERT INTO weigh_ins ({', '.join(columns)}) "
        f"VALUES ({', '.join('?' for _ in columns)})",
        tuple(values[c] for c in columns),
    )
    conn.commit()
    return cursor.lastrowid
