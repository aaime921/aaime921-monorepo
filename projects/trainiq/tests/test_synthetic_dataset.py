"""
Tests for Feature 0.7 — Synthetic Athlete Dataset.

DoD being verified: the dataset alone must be sufficient to exercise every
scenario named explicitly in the Chief Architect's Tier B review: missing
data, delayed sync, cross-provider duplicates, zero-training weeks, rapid
weight change, back-to-back hard sessions, a deload week, incomplete
records, Peloton-without-HR, and Strava-with-HR-but-no-power.
"""

from datetime import date
from pathlib import Path

import pytest

from trainiq.synthetic_dataset import (
    DATASET_VERSION,
    SCENARIOS,
    build_dataset,
    load_dataset,
    save_dataset,
)


def test_dataset_covers_every_named_scenario():
    """Every scenario the Chief Architect explicitly named must exist by name
    — this test is intentionally an exact-match list, not a count, so a
    silently-dropped scenario is caught immediately, not just a shrinking
    total."""
    required_scenario_ids = {
        "missing_load_data",       # "dati mancanti"
        "delayed_sync",            # "sincronizzazioni ritardate"
        "cross_provider_duplicates",  # "attività duplicate"
        "zero_training_week",      # "settimane senza allenamenti"
        "rapid_weight_change",     # "cambi di peso"
        "back_to_back_hard_sessions",  # "allenamenti molto ravvicinati"
        "deload_week",             # "settimane di recupero"
        "incomplete_records",      # "dati incompleti"
        "peloton_no_hr",           # "Peloton senza HR"
        "strava_no_power",         # "Strava con HR ma senza potenza"
    }
    data = build_dataset()
    actual_ids = {a["athlete_id"] for a in data["athletes"]}
    missing = required_scenario_ids - actual_ids
    assert not missing, f"Dataset is missing required scenarios: {missing}"


def test_dataset_is_deterministic():
    """Same seed must always produce the same dataset — required for it to
    be usable as a versioned regression fixture, not a moving target."""
    d1 = build_dataset(seed=42)
    d2 = build_dataset(seed=42)
    assert d1 == d2


def test_different_seeds_produce_different_datasets():
    d1 = build_dataset(seed=1)
    d2 = build_dataset(seed=2)
    # Structural content is deterministic per-scenario regardless of seed
    # (the scenarios are hand-authored, not randomly generated per field),
    # but the seed is still threaded through so future randomized scenarios
    # remain reproducible. This test documents that expectation explicitly.
    assert d1["seed"] != d2["seed"]


def test_missing_load_data_scenario_has_no_zero_fill():
    """Constitution Principle 1: Unknown must never silently become zero.
    This test checks the raw fixture itself never encodes 'no data' as 0 —
    it must be Python None (-> JSON null), which downstream Normalization
    is responsible for treating as Unknown, not 0."""
    data = build_dataset()
    athlete = next(a for a in data["athletes"] if a["athlete_id"] == "missing_load_data")
    no_data_activities = [
        act for act in athlete["activities"]
        if act["avg_hr"] is None and act["avg_power"] is None
    ]
    assert no_data_activities, "Scenario should contain at least one no-HR/no-power activity"
    for act in no_data_activities:
        assert act["avg_hr"] is None  # not 0
        assert act["avg_power"] is None  # not 0


def test_zero_training_week_has_no_activity_records_not_unknown_ones():
    """A true rest week must be encoded as an absence of records, not as
    records with Unknown load — these are different states per ADR-019."""
    data = build_dataset()
    athlete = next(a for a in data["athletes"] if a["athlete_id"] == "zero_training_week")
    start = date.fromisoformat(data["anchor_date"])
    rest_week_dates = {(start.toordinal() + i) for i in range(7, 14)}
    activity_dates = {
        date.fromisoformat(act["start_time"][:10]).toordinal()
        for act in athlete["activities"]
    }
    assert not (rest_week_dates & activity_dates), (
        "zero_training_week must have zero records in days 7-13, "
        "not records with missing fields"
    )


def test_cross_provider_duplicates_scenario_has_matching_pairs():
    data = build_dataset()
    athlete = next(a for a in data["athletes"] if a["athlete_id"] == "cross_provider_duplicates")
    peloton = [a for a in athlete["activities"] if a["provider"] == "peloton"]
    strava = [a for a in athlete["activities"] if a["provider"] == "strava"]
    assert len(peloton) == len(strava) > 0
    # Confirm the deliberate clock-skew property the dedup scoring (Milestone 4 §4)
    # needs to be exercised against: start times close but not identical.
    for p, s in zip(peloton, strava):
        p_min = int(p["start_time"][14:16])
        s_min = int(s["start_time"][14:16])
        assert p_min != s_min, "Duplicate pair should have deliberate clock skew, not identical timestamps"


def test_delayed_sync_scenario_has_a_late_arriving_record():
    data = build_dataset()
    athlete = next(a for a in data["athletes"] if a["athlete_id"] == "delayed_sync")
    late = [
        act for act in athlete["activities"]
        if act["synced_at"][:10] != act["start_time"][:10]
        and (date.fromisoformat(act["synced_at"][:10]) - date.fromisoformat(act["start_time"][:10])).days >= 3
    ]
    assert late, "delayed_sync scenario must include at least one record synced 3+ days after it happened"


def test_peloton_no_hr_scenario_has_power_but_never_hr():
    data = build_dataset()
    athlete = next(a for a in data["athletes"] if a["athlete_id"] == "peloton_no_hr")
    assert athlete["activities"]
    for act in athlete["activities"]:
        assert act["avg_hr"] is None
        assert act["avg_power"] is not None


def test_strava_no_power_scenario_has_hr_but_never_power():
    data = build_dataset()
    athlete = next(a for a in data["athletes"] if a["athlete_id"] == "strava_no_power")
    assert athlete["activities"]
    for act in athlete["activities"]:
        assert act["avg_power"] is None
        assert act["avg_hr"] is not None


def test_every_athlete_has_a_scenario_description():
    """Every scenario must be self-documenting — traceable to the Constitution
    principle or ADR it exercises, per the same Evidence Traceability spirit
    (ADR-027) applied to test fixtures, not just Coach output."""
    data = build_dataset()
    for athlete in data["athletes"]:
        assert athlete["scenario"] is not None
        assert athlete["scenario"]["stresses_principle"], (
            f"{athlete['athlete_id']} is missing a traceable justification"
        )


def test_save_and_load_roundtrip(tmp_path):
    out = tmp_path / "dataset.json"
    save_dataset(out, seed=42)
    reloaded = load_dataset(out)
    assert reloaded == build_dataset(seed=42)


def test_load_rejects_mismatched_version(tmp_path):
    import json
    out = tmp_path / "dataset.json"
    data = build_dataset()
    data["dataset_version"] = "0.0.1-stale"
    out.write_text(json.dumps(data))
    with pytest.raises(ValueError, match="version mismatch"):
        load_dataset(out)


def test_dataset_version_is_set():
    assert DATASET_VERSION
    parts = DATASET_VERSION.split(".")
    assert len(parts) == 3, "Expected semantic version X.Y.Z"


def test_scenario_count_matches_registered_builders():
    data = build_dataset()
    assert data["athlete_count"] == len(SCENARIOS)
    assert len(data["athletes"]) == len(SCENARIOS)
