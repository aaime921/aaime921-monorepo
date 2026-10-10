"""Tests for trainiq.export.weight (issue #71, AC 6)."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

from trainiq.config import set_weight_goal
from trainiq.export import weight

from tests.conftest import insert_weigh_in


def test_render_weekly_average_excludes_flagged_readings(db, tmp_path: Path):
    insert_weigh_in(db, external_id="1", timestamp="2026-10-05T07:00:00+00:00", weight_kg=80.0)
    insert_weigh_in(
        db, external_id="2", timestamp="2026-10-05T19:00:00+00:00", weight_kg=25.0,
        is_weight_flagged_implausible=1,
    )

    _, js = weight.render(db, tmp_path / "config.json", date(2026, 10, 10))

    weeks_with_data = [w for w in json.loads(js)["weeks"] if w["avg_weight_kg"] is not None]
    assert len(weeks_with_data) == 1
    assert weeks_with_data[0]["avg_weight_kg"] == 80.0


def test_render_rate_of_change_needs_at_least_two_weeks(db, tmp_path: Path):
    insert_weigh_in(db, external_id="1", timestamp="2026-10-05T07:00:00+00:00", weight_kg=80.0)

    _, js = weight.render(db, tmp_path / "config.json", date(2026, 10, 10))

    assert json.loads(js)["rate_kg_per_week"] is None


def test_render_rate_of_change_negative_for_weight_loss(db, tmp_path: Path):
    insert_weigh_in(db, external_id="1", timestamp="2026-09-01T07:00:00+00:00", weight_kg=85.0)
    insert_weigh_in(db, external_id="2", timestamp="2026-10-05T07:00:00+00:00", weight_kg=80.0)

    _, js = weight.render(db, tmp_path / "config.json", date(2026, 10, 10))

    assert json.loads(js)["rate_kg_per_week"] < 0


def test_render_goal_not_configured(db, tmp_path: Path):
    md, js = weight.render(db, tmp_path / "config.json", date(2026, 10, 10))
    assert "not configured" in md.lower()
    assert json.loads(js)["goal"] is None


def test_render_progress_to_goal(db, tmp_path: Path):
    config_path = tmp_path / "config.json"
    set_weight_goal(config_path, start_weight_kg=82.1, goal_weight_kg=72.0)
    insert_weigh_in(db, external_id="1", timestamp="2026-10-05T07:00:00+00:00", weight_kg=80.0)

    md, js = weight.render(db, config_path, date(2026, 10, 10))

    assert "kg lost" in md
    assert "kg remaining" in md
    assert json.loads(js)["latest_avg_weight_kg"] == 80.0
