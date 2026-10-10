"""Tests for trainiq.export.performance (issue #71, AC 7)."""

from __future__ import annotations

from datetime import date

from trainiq.export import performance

from tests.conftest import insert_activity


def test_render_best_20min_power_among_cycling_rows_over_20_minutes(db):
    too_short = insert_activity(
        db, external_id="1", start_time="2026-09-01T07:00:00+00:00",
        discipline="cycling", duration_s=600, avg_power=300,
    )
    long_enough = insert_activity(
        db, external_id="2", start_time="2026-09-02T07:00:00+00:00",
        discipline="cycling", duration_s=1200, avg_power=250,
    )

    md = performance.render(db, primary_ids={too_short, long_enough}, as_of=date(2026, 10, 10))

    assert "250 W" in md
    assert "proxy" in md.lower()


def test_render_longest_run_and_walk(db):
    run_id = insert_activity(
        db, external_id="1", start_time="2026-09-01T07:00:00+00:00",
        discipline="running", distance_m=10000.0,
    )
    walk_id = insert_activity(
        db, external_id="2", start_time="2026-09-02T07:00:00+00:00",
        discipline="other", sport_type_raw="Walk", distance_m=9000.0,
    )

    md = performance.render(db, primary_ids={run_id, walk_id}, as_of=date(2026, 10, 10))

    assert "10.00 km" in md
    assert "9.00 km" in md


def test_render_monthly_pace_trend(db):
    activity_id = insert_activity(
        db, external_id="1", start_time="2026-09-15T07:00:00+00:00",
        discipline="running", avg_pace_s_per_km=330,
    )

    md = performance.render(db, primary_ids={activity_id}, as_of=date(2026, 10, 10))

    assert "2026-09" in md
    assert "5:30 /km" in md


def test_render_no_data_does_not_crash(db):
    md = performance.render(db, primary_ids=set(), as_of=date(2026, 10, 10))
    assert "Best 20-min power: -" in md
