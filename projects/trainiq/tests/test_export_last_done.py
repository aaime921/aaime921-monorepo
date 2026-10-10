"""Tests for trainiq.export.last_done (issue #71, AC 4)."""

from __future__ import annotations

from datetime import date

from trainiq.export import last_done

from tests.conftest import insert_activity


def test_render_picks_most_recent_per_sport(db):
    older_run = insert_activity(
        db, external_id="1", start_time="2026-09-01T07:00:00+00:00", discipline="running",
    )
    newer_run = insert_activity(
        db, external_id="2", start_time="2026-09-20T07:00:00+00:00", discipline="running",
    )

    md = last_done.render(db, primary_ids={older_run, newer_run}, as_of=date(2026, 10, 10))

    assert "2026-09-20" in md
    assert "2026-09-01" not in md


def test_render_groups_by_peloton_class_type_and_instructor(db):
    activity_id = insert_activity(
        db, external_id="1", start_time="2026-09-20T07:00:00+00:00", discipline="cycling",
        provider="peloton", class_type="power_zone_max", instructor_name="Matt Wilpers",
    )

    md = last_done.render(db, primary_ids={activity_id}, as_of=date(2026, 10, 10))

    assert "power_zone_max" in md
    assert "Matt Wilpers" in md


def test_render_respects_as_of(db):
    activity_id = insert_activity(
        db, external_id="1", start_time="2026-12-01T07:00:00+00:00", discipline="running",
    )

    md = last_done.render(db, primary_ids={activity_id}, as_of=date(2026, 10, 10))

    assert "2026-12-01" not in md


def test_render_no_data_does_not_crash(db):
    md = last_done.render(db, primary_ids=set(), as_of=date(2026, 10, 10))
    assert "sport: run" in md
