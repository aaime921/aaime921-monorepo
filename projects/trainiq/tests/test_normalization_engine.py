"""
Tests for Epic 6, slice 5's normalization engine entry point,
build_canonical_record(). Pure function over a normalized dict — no Sync
Engine, no persistence yet (that's this slice's OTHER half, tested
separately in test_sync_engine.py).
"""

from __future__ import annotations

from trainiq.athlete.profile import AthleteProfile
from trainiq.connectors.base import RecordKind
from trainiq.normalization.engine import build_canonical_record


# --- ACTIVITY records --------------------------------------------------

def test_activity_record_combines_taxonomy_confidence_and_load():
    normalized = {
        "external_id": "1", "start_time": "2026-01-05T07:00:00+00:00", "duration_s": 1800,
        "discipline_raw": "Ride", "avg_hr": 145, "max_hr": 168, "avg_power": None,
        "max_power": None, "distance_m": 30000.0, "calories": None,
    }
    profile = AthleteProfile(sex="male", resting_hr=50, max_hr=190)

    record = build_canonical_record("strava", RecordKind.ACTIVITY, normalized, profile)

    assert record["discipline"] == "cycling"  # taxonomy applied
    assert record["training_load"] is not None  # TRIMP computed
    assert record["training_load_method"] == "trimp"
    assert 0.0 <= record["source_confidence"] <= 1.0  # confidence computed
    assert record["provider"] == "strava"
    assert record["external_id"] == "1"


def test_activity_record_with_no_profile_has_unknown_load_but_full_taxonomy_and_confidence():
    """Q1's resolution, exercised at the full pipeline level: even with no
    AthleteProfile, discipline and confidence are still fully computed —
    only training_load is affected."""
    normalized = {
        "external_id": "2", "start_time": "2026-01-05T07:00:00+00:00", "duration_s": 1800,
        "discipline_raw": "strength", "avg_hr": 130, "avg_power": None,
        "distance_m": None, "calories": None,
    }

    record = build_canonical_record("peloton", RecordKind.ACTIVITY, normalized, athlete_profile=None)

    assert record["discipline"] == "strength"  # taxonomy unaffected by missing profile
    assert record["training_load"] is None
    assert record["training_load_method"] == "unknown"
    assert record["source_confidence"] == 0.25  # avg_hr present out of 4 optional fields


def test_activity_record_unrecognized_discipline_falls_back_to_other():
    normalized = {
        "external_id": "3", "start_time": "2026-01-05T07:00:00+00:00", "duration_s": 1200,
        "discipline_raw": "underwater_basket_weaving", "avg_hr": None,
    }

    record = build_canonical_record("strava", RecordKind.ACTIVITY, normalized)

    assert record["discipline"] == "other"


# --- WEIGH_IN records ----------------------------------------------------

def test_weigh_in_record_is_a_near_pass_through():
    normalized = {
        "external_id": "w1", "timestamp": "2026-01-05T06:30:00+00:00",
        "weight_kg": 75.4, "body_fat_pct": 18.2, "muscle_mass_pct": 34.1,
    }

    record = build_canonical_record("eufy", RecordKind.WEIGH_IN, normalized)

    assert record["weight_kg"] == 75.4
    assert record["body_fat_pct"] == 18.2
    assert "discipline" not in record  # no discipline concept for weigh-ins
    assert "training_load" not in record  # no load concept for weigh-ins


def test_weigh_in_record_never_fabricates_missing_body_composition():
    normalized = {"external_id": "w2", "timestamp": "2026-01-05T06:30:00+00:00", "weight_kg": 80.0}

    record = build_canonical_record("eufy", RecordKind.WEIGH_IN, normalized)

    assert record["weight_kg"] == 80.0
    assert record["body_fat_pct"] is None
    assert record["muscle_mass_pct"] is None


def test_weigh_in_record_has_no_source_confidence_key_pending_bl_009():
    """Documents BL-009 directly: no source_confidence key is produced for
    weigh-ins today, because the target table has nowhere to store it."""
    normalized = {"external_id": "w3", "timestamp": "2026-01-05T06:30:00+00:00", "weight_kg": 80.0}

    record = build_canonical_record("eufy", RecordKind.WEIGH_IN, normalized)

    assert "source_confidence" not in record
