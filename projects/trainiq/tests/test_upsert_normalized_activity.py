"""
Tests for trainiq.sync.engine.upsert_normalized_activity() — specifically
issue #46's COALESCE-based handling of the 6 new class-metadata columns
(activity_title, instructor_name, class_type, planned_duration_s,
provider_class_id, sport_type_raw).

Every pre-existing column (discipline, training_load, etc.) keeps today's
unconditional-overwrite behavior; these 6 are the only ones for which a
None in the incoming record must NOT blow away an already-stored value.
This is load-bearing for the renormalize-safety fix (see
trainiq/connectors/peloton.py's download() docstring and the architecture
doc, "The COALESCE-based upsert"): without it, re-running
renormalize_provider() for Peloton would silently wipe every
already-backfilled class title/instructor/type back to NULL.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from trainiq.storage.schema import open_db
from trainiq.sync.engine import upsert_normalized_activity


@pytest.fixture
def db(tmp_path: Path):
    conn = open_db(tmp_path / "trainiq.db")
    yield conn
    conn.close()


def _base_record(**overrides) -> dict:
    record = {
        "provider": "peloton",
        "external_id": "ext-1",
        "start_time": "2026-01-01T00:00:00+00:00",
        "duration_s": 300,
        "discipline": "cycling",
        "distance_m": 1000.0,
        "avg_hr": None,
        "max_hr": None,
        "avg_power": 200,
        "max_power": None,
        "calories": 100,
        "elevation_gain_m": None,
        "moving_time_s": None,
        "is_indoor": None,
        "training_load": None,
        "training_load_method": "unknown",
        "source_confidence": 0.9,
        "activity_title": None,
        "instructor_name": None,
        "class_type": None,
        "planned_duration_s": None,
        "provider_class_id": None,
        "sport_type_raw": None,
    }
    record.update(overrides)
    return record


def test_insert_with_all_six_new_fields_populated_stores_as_given(db):
    record = _base_record(
        activity_title="Power Zone Max", instructor_name="Matt Wilpers",
        class_type="power_zone_max", planned_duration_s=2700, provider_class_id="ride-1",
        sport_type_raw="Ride",
    )

    outcome = upsert_normalized_activity(db, record)
    db.commit()

    assert outcome == "inserted"
    row = dict(db.execute(
        "SELECT * FROM normalized_activities WHERE provider = ? AND external_id = ?",
        (record["provider"], record["external_id"]),
    ).fetchone())
    assert row["activity_title"] == "Power Zone Max"
    assert row["instructor_name"] == "Matt Wilpers"
    assert row["class_type"] == "power_zone_max"
    assert row["planned_duration_s"] == 2700
    assert row["provider_class_id"] == "ride-1"
    assert row["sport_type_raw"] == "Ride"


def test_update_with_all_six_new_fields_none_preserves_existing_values(db):
    """The COALESCE proof: re-upserting with None for all 6 new fields
    (e.g. a renormalize_provider() re-run against an old raw payload with
    no _class_* keys) must NOT wipe already-backfilled class metadata."""
    first = _base_record(
        activity_title="Power Zone Max", instructor_name="Matt Wilpers",
        class_type="power_zone_max", planned_duration_s=2700, provider_class_id="ride-1",
        sport_type_raw="Ride",
    )
    upsert_normalized_activity(db, first)
    db.commit()

    second = _base_record(discipline="cycling", avg_power=210)  # all 6 new fields default to None
    outcome = upsert_normalized_activity(db, second)
    db.commit()

    assert outcome == "updated"
    row = dict(db.execute(
        "SELECT * FROM normalized_activities WHERE provider = ? AND external_id = ?",
        (second["provider"], second["external_id"]),
    ).fetchone())
    # The 6 new fields are unchanged from the first upsert.
    assert row["activity_title"] == "Power Zone Max"
    assert row["instructor_name"] == "Matt Wilpers"
    assert row["class_type"] == "power_zone_max"
    assert row["planned_duration_s"] == 2700
    assert row["provider_class_id"] == "ride-1"
    assert row["sport_type_raw"] == "Ride"
    # A pre-existing column still overwrites unconditionally, proving this
    # is a targeted exception, not a blanket "ignore the new record."
    assert row["avg_power"] == 210


def test_update_with_real_values_for_the_six_new_fields_overwrites(db):
    """Proves COALESCE doesn't just always preserve — a real incoming
    value still wins."""
    first = _base_record(class_type="not_a_class")
    upsert_normalized_activity(db, first)
    db.commit()

    second = _base_record(
        activity_title="Endurance Ride", instructor_name="Robin Arzon",
        class_type="endurance", planned_duration_s=1800, provider_class_id="ride-2",
        sport_type_raw="VirtualRide",
    )
    upsert_normalized_activity(db, second)
    db.commit()

    row = dict(db.execute(
        "SELECT * FROM normalized_activities WHERE provider = ? AND external_id = ?",
        (second["provider"], second["external_id"]),
    ).fetchone())
    assert row["activity_title"] == "Endurance Ride"
    assert row["instructor_name"] == "Robin Arzon"
    assert row["class_type"] == "endurance"
    assert row["planned_duration_s"] == 1800
    assert row["provider_class_id"] == "ride-2"
    assert row["sport_type_raw"] == "VirtualRide"
