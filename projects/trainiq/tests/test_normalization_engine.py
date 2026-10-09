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


# --- WEIGH_IN plausibility flagging (ADR-039 / Issue #38, corrected by #42)

_BASELINE = [84.0, 85.0, 85.5, 86.0, 83.5]  # athlete's established 80-88kg range


def test_weigh_in_normal_reading_within_baseline_is_not_flagged():
    normalized = {
        "external_id": "w4", "timestamp": "2026-01-05T06:30:00+00:00",
        "weight_kg": 85.0, "body_fat_pct": 18.0,
    }

    record = build_canonical_record(
        "eufy", RecordKind.WEIGH_IN, normalized, recent_weights_kg=_BASELINE
    )

    assert record["is_weight_flagged_implausible"] is False
    assert record["weight_plausibility_reason"] is None
    assert record["is_body_fat_flagged_implausible"] is False
    assert record["body_fat_plausibility_reason"] is None


def test_weigh_in_obvious_outlier_reproducing_issue_evidence_is_flagged():
    """Reproduces the issue's own evidence: 20.7 kg / 5.0% body fat against
    an 80+ kg baseline. body_fat_pct=5.0 is above the 3.0 floor, so only
    the weight axis trips here — this was already true under the old
    combined rule too (the body-fat floor never factored into flagging
    this specific reading; the weight-deviation axis alone did)."""
    normalized = {
        "external_id": "w5", "timestamp": "2026-07-06T18:43:00+00:00",
        "weight_kg": 20.7, "body_fat_pct": 5.0,
    }

    record = build_canonical_record(
        "eufy", RecordKind.WEIGH_IN, normalized, recent_weights_kg=_BASELINE
    )

    assert record["is_weight_flagged_implausible"] is True
    assert record["weight_plausibility_reason"] is not None
    assert record["is_body_fat_flagged_implausible"] is False


def test_weigh_in_borderline_reading_resolves_deterministically():
    """Median of _BASELINE is 85.0; 25% deviation threshold -> 63.75 is the
    exact boundary (strict '>' means not flagged)."""
    at_boundary = {
        "external_id": "w6", "timestamp": "2026-01-05T06:30:00+00:00",
        "weight_kg": 63.75, "body_fat_pct": 18.0,
    }
    just_over = {
        "external_id": "w7", "timestamp": "2026-01-05T06:30:00+00:00",
        "weight_kg": 63.0, "body_fat_pct": 18.0,
    }

    record_at_boundary = build_canonical_record(
        "eufy", RecordKind.WEIGH_IN, at_boundary, recent_weights_kg=_BASELINE
    )
    record_just_over = build_canonical_record(
        "eufy", RecordKind.WEIGH_IN, just_over, recent_weights_kg=_BASELINE
    )

    assert record_at_boundary["is_weight_flagged_implausible"] is False
    assert record_just_over["is_weight_flagged_implausible"] is True


def test_weigh_in_with_no_recent_weights_defaults_to_not_flagged():
    """A connector/caller that doesn't pass recent_weights_kg (e.g. existing
    callers before this feature) gets the old pass-through behavior —
    insufficient history skips the weight-deviation axis entirely."""
    normalized = {
        "external_id": "w8", "timestamp": "2026-01-05T06:30:00+00:00",
        "weight_kg": 20.0, "body_fat_pct": 18.0,
    }

    record = build_canonical_record("eufy", RecordKind.WEIGH_IN, normalized)

    assert record["is_weight_flagged_implausible"] is False


def test_weigh_in_body_fat_zero_sentinel_does_not_flag_weight():
    """Core regression (Issue #42, AC6): a reading like 82.2 kg with
    body_fat_pct=0.0 (already normalized to the connector's sentinel
    value as of this build_canonical_record() call — this function
    doesn't know about Eufy's connector, only the value it's given) must
    not flag the weight. A literal 0 also never trips the body-fat axis,
    since the `> 0` guard excludes it there too."""
    normalized = {
        "external_id": "w9", "timestamp": "2026-09-11T08:00:00+00:00",
        "weight_kg": 82.2, "body_fat_pct": 0.0,
    }

    record = build_canonical_record(
        "eufy", RecordKind.WEIGH_IN, normalized, recent_weights_kg=_BASELINE
    )

    assert record["is_weight_flagged_implausible"] is False
    assert record["is_body_fat_flagged_implausible"] is False


def test_weigh_in_non_zero_sub_floor_body_fat_flags_only_that_axis():
    """Proves the decoupling, not just sentinel cleanup: a genuinely
    implausible non-zero body-fat value with a normal weight must flag
    only the body-fat axis (AC2/AC3)."""
    normalized = {
        "external_id": "w10", "timestamp": "2026-01-05T06:30:00+00:00",
        "weight_kg": 85.0, "body_fat_pct": 1.5,
    }

    record = build_canonical_record(
        "eufy", RecordKind.WEIGH_IN, normalized, recent_weights_kg=_BASELINE
    )

    assert record["is_weight_flagged_implausible"] is False
    assert record["is_body_fat_flagged_implausible"] is True
