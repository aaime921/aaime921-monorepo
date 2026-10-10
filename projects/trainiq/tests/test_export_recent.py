"""Tests for trainiq.export.recent (issue #71, AC 3)."""

from __future__ import annotations

import json
from datetime import date

from trainiq.export import recent

from tests.conftest import insert_activity


def test_render_includes_activity_within_30_days(db):
    activity_id = insert_activity(
        db, external_id="1", start_time="2026-09-20T07:00:00+00:00",
        discipline="running", duration_s=1800, distance_m=5000.0,
        avg_hr=150, activity_title="Morning Run",
    )

    md, js = recent.render(db, primary_ids={activity_id}, as_of=date(2026, 10, 10))

    assert "Morning Run" in md
    payload = json.loads(js)
    assert payload["activities"][0]["activity_title"] == "Morning Run"


def test_render_excludes_activity_older_than_30_days(db):
    activity_id = insert_activity(
        db, external_id="1", start_time="2026-08-01T07:00:00+00:00",
        discipline="running",
    )

    md, js = recent.render(db, primary_ids={activity_id}, as_of=date(2026, 10, 10))

    assert "2026-08-01" not in md
    assert json.loads(js)["activities"] == []


def test_render_missing_values_are_dash_never_invented(db):
    activity_id = insert_activity(
        db, external_id="1", start_time="2026-10-05T07:00:00+00:00",
        discipline="running", avg_hr=None, avg_power=None, distance_m=None,
    )

    md, _ = recent.render(db, primary_ids={activity_id}, as_of=date(2026, 10, 10))

    rows = [line for line in md.split("\n") if line.startswith("| 2026-10-05")]
    assert len(rows) == 1
    assert "-" in rows[0]


def test_render_pace_only_shown_for_runs(db):
    run_id = insert_activity(
        db, external_id="1", start_time="2026-10-05T07:00:00+00:00",
        discipline="running", avg_pace_s_per_km=330,
    )
    ride_id = insert_activity(
        db, external_id="2", start_time="2026-10-06T07:00:00+00:00",
        discipline="cycling", avg_pace_s_per_km=None,
    )

    md, js = recent.render(db, primary_ids={run_id, ride_id}, as_of=date(2026, 10, 10))

    payload = json.loads(js)
    run_record = next(r for r in payload["activities"] if r["sport"] == "run")
    ride_record = next(r for r in payload["activities"] if r["sport"] == "ride")
    assert run_record["pace_s_per_km"] == 330
    assert ride_record["pace_s_per_km"] is None
    assert "5:30 /km" in md


def test_render_newest_first_and_truncation(db):
    ids = set()
    for day in range(1, 8):
        ids.add(insert_activity(
            db, external_id=str(day), start_time=f"2026-10-0{day}T07:00:00+00:00",
            discipline="running",
        ))

    import trainiq.export.recent as recent_module
    original_max = recent_module.MAX_ROWS
    recent_module.MAX_ROWS = 3
    try:
        md, js = recent.render(db, primary_ids=ids, as_of=date(2026, 10, 10))
    finally:
        recent_module.MAX_ROWS = original_max

    payload = json.loads(js)
    assert payload["truncated_count"] == 4
    assert len(payload["activities"]) == 3
    assert payload["activities"][0]["date"] == "2026-10-07"  # newest first
    assert "Truncated: 4 older rows omitted." in md


def test_render_uses_moving_time_when_present(db):
    """issue #79: a walk left running (elapsed 537 min, moving 86 min) must
    show the moving time, not the elapsed time, in both the table and the
    JSON `duration_s` field."""
    activity_id = insert_activity(
        db, external_id="1", start_time="2026-09-26T07:00:00+00:00",
        discipline="other", sport_type_raw="Walk", duration_s=537 * 60, moving_time_s=86 * 60,
    )

    md, js = recent.render(db, primary_ids={activity_id}, as_of=date(2026, 10, 10))

    assert "86 min" in md
    assert "537 min" not in md
    assert json.loads(js)["activities"][0]["duration_s"] == 86 * 60


def test_render_falls_back_to_duration_when_moving_time_missing(db):
    activity_id = insert_activity(
        db, external_id="1", start_time="2026-10-05T07:00:00+00:00",
        discipline="running", duration_s=1800, moving_time_s=None,
    )

    md, js = recent.render(db, primary_ids={activity_id}, as_of=date(2026, 10, 10))

    assert "30 min" in md
    assert json.loads(js)["activities"][0]["duration_s"] == 1800


def test_render_excludes_dedup_secondary_side(db):
    primary = insert_activity(
        db, external_id="1", start_time="2026-10-05T07:00:00+00:00", discipline="cycling",
    )
    secondary = insert_activity(
        db, external_id="2", start_time="2026-10-05T07:01:00+00:00", discipline="cycling",
    )

    md, js = recent.render(db, primary_ids={primary}, as_of=date(2026, 10, 10))

    assert len(json.loads(js)["activities"]) == 1
