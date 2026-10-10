"""Tests for trainiq.export.load (issue #71, AC 5)."""

from __future__ import annotations

import json
from datetime import date

from trainiq.export import load

from tests.conftest import insert_activity


def test_render_constant_daily_load_converges_ctl_and_atl_to_that_load(db):
    """Property check from the architecture doc's test strategy: a
    constant daily load for long enough must converge CTL/ATL toward that
    same value (EWMA's defining property). 365 days is ~8.7 CTL time
    constants (tau=42d) — enough for (1-k)^n to be negligible."""
    start = date(2025, 1, 1)
    n_days = 365
    rows = [
        (
            "strava", str(i), f"{start.fromordinal(start.toordinal() + i).isoformat()}T07:00:00+00:00",
            1800, "cycling", 50.0, "tss", 0.5,
        )
        for i in range(n_days)
    ]
    db.executemany(
        "INSERT INTO normalized_activities "
        "(provider, external_id, start_time, duration_s, discipline, training_load, "
        " training_load_method, source_confidence) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        rows,
    )
    db.commit()
    ids = {row["id"] for row in db.execute("SELECT id FROM normalized_activities")}

    as_of = start.fromordinal(start.toordinal() + n_days - 1)
    _, js = load.render(db, primary_ids=ids, as_of=as_of)
    last_week = json.loads(js)["weeks"][-1]

    assert abs(last_week["ctl"] - 50.0) < 1.0
    assert abs(last_week["atl"] - 50.0) < 1.0
    assert abs(last_week["tsb"]) < 1.0


def test_render_counts_activities_without_load_separately_from_zero(db):
    """ADR-016: a missing load must not be indistinguishable from a day
    that genuinely had zero training stress."""
    activity_id = insert_activity(
        db, external_id="1", start_time="2026-10-05T07:00:00+00:00",
        discipline="running", training_load=None, training_load_method="unknown",
    )

    _, js = load.render(db, primary_ids={activity_id}, as_of=date(2026, 10, 10))

    payload = json.loads(js)
    assert payload["activities_without_load"] == 1


def test_render_excludes_dedup_secondary_side_from_weekly_load(db):
    primary = insert_activity(
        db, external_id="1", start_time="2026-10-05T07:00:00+00:00",
        discipline="cycling", training_load=100.0, training_load_method="tss",
    )
    secondary = insert_activity(
        db, external_id="2", start_time="2026-10-05T07:01:00+00:00",
        discipline="cycling", training_load=100.0, training_load_method="tss",
    )

    _, js = load.render(db, primary_ids={primary}, as_of=date(2026, 10, 10))

    last_week = json.loads(js)["weeks"][-1]
    assert last_week["load"] == 100.0


def test_render_no_activities_does_not_crash(db):
    md, js = load.render(db, primary_ids=set(), as_of=date(2026, 10, 10))
    payload = json.loads(js)
    assert payload["weeks"][-1]["ctl"] == 0.0
    assert payload["weeks"][0]["ctl"] is None  # outside the single-day EWMA run
    assert "CTL" in md
