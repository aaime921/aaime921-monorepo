"""Tests for trainiq.export.profile (issue #71, AC 2)."""

from __future__ import annotations

from datetime import date
from pathlib import Path

from trainiq.athlete.profile import AthleteProfile
from trainiq.athlete.store import save_athlete_profile
from trainiq.config import set_weight_goal
from trainiq.export import profile

from tests.conftest import insert_activity


def test_render_includes_athlete_facts(db, tmp_path: Path):
    save_athlete_profile(
        db, AthleteProfile(sex="male", date_of_birth="1990-01-01", resting_hr=69, max_hr=181, ftp_watts=166)
    )
    config_path = tmp_path / "config.json"

    md = profile.render(db, config_path, date(2026, 10, 10))

    assert "166 W" in md
    assert "69 bpm" in md
    assert "181 bpm" in md
    assert "male" in md
    assert "1990-01-01" in md


def test_render_with_no_profile_stored_does_not_crash(db, tmp_path: Path):
    md = profile.render(db, tmp_path / "config.json", date(2026, 10, 10))
    assert "No athlete profile stored" in md


def test_render_weight_goal_not_configured(db, tmp_path: Path):
    md = profile.render(db, tmp_path / "config.json", date(2026, 10, 10))
    assert "not configured" in md.lower()


def test_render_weight_goal_configured(db, tmp_path: Path):
    config_path = tmp_path / "config.json"
    set_weight_goal(config_path, start_weight_kg=82.1, goal_weight_kg=72.0)

    md = profile.render(db, config_path, date(2026, 10, 10))

    assert "82.1 kg" in md
    assert "72.0 kg" in md


def test_render_ftp_history_lists_ftp_test_rides(db, tmp_path: Path):
    insert_activity(
        db, external_id="1", start_time="2026-01-01T07:00:00+00:00",
        discipline="cycling", activity_title="20-min FTP Test",
    )
    insert_activity(
        db, external_id="2", start_time="2026-02-01T07:00:00+00:00",
        discipline="cycling", activity_title="Power Zone Endurance",
    )

    md = profile.render(db, tmp_path / "config.json", date(2026, 10, 10))

    assert "2026-01-01" in md
    assert "2026-02-01" not in md


def test_render_ftp_history_respects_as_of(db, tmp_path: Path):
    insert_activity(
        db, external_id="1", start_time="2026-12-01T07:00:00+00:00",
        discipline="cycling", activity_title="FTP Test",
    )

    md = profile.render(db, tmp_path / "config.json", date(2026, 10, 10))

    assert "2026-12-01" not in md
