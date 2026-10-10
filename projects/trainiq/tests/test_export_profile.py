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


def test_render_ftp_history_peloton_epoch_start_time(db, tmp_path: Path):
    """FTP Test rides are Peloton classes, so start_time is an epoch-second
    string, not ISO 8601 (BO, issue #71 rework) — raw string-slicing the
    column (the previous implementation) silently produced a garbage date
    instead of crashing, since epoch strings and ISO strings are both just
    text."""
    insert_activity(
        db, provider="peloton", external_id="1", start_time="1757357927",  # 2025-09-08
        discipline="cycling", activity_title="20-min FTP Test", duration_s=1200, avg_power=171,
    )

    md = profile.render(db, tmp_path / "config.json", date(2026, 10, 10))

    assert "2025-09-08" in md


def test_render_ftp_history_shows_estimate_marks_current_and_best(db, tmp_path: Path):
    """BO (2026-10-10): estimated FTP = round(0.95 * avg power), with the
    current profile FTP and the best estimate both marked. Matches the
    BO's real-data example (issue #71 rework comment)."""
    save_athlete_profile(
        db, AthleteProfile(sex="male", date_of_birth="1990-01-01", resting_hr=69, max_hr=181, ftp_watts=166)
    )
    insert_activity(
        db, external_id="1", start_time="2025-09-18T07:00:00+00:00",
        activity_title="FTP Test", duration_s=1200, avg_power=171,
    )
    insert_activity(
        db, external_id="2", start_time="2025-10-15T07:00:00+00:00",
        activity_title="FTP Test", duration_s=1200, avg_power=175,
    )
    insert_activity(
        db, external_id="3", start_time="2026-01-28T07:00:00+00:00",
        activity_title="FTP Test", duration_s=1200, avg_power=192,
    )

    md = profile.render(db, tmp_path / "config.json", date(2026, 10, 10))

    assert "| 2025-09-18 | 171 | 162 |  |" in md
    assert "| 2025-10-15 | 175 | 166 | current |" in md
    assert "| 2026-01-28 | 192 | 182 | best |" in md


def test_render_ftp_history_excludes_aborted_attempts_under_15_min(db, tmp_path: Path):
    """BO (2026-10-10): a warm-up or aborted attempt shorter than 15 min is
    excluded entirely, not shown with a blank estimate — matches the
    real-data example of a 4-min aborted attempt on 2026-09-29."""
    insert_activity(
        db, external_id="1", start_time="2026-09-29T07:00:00+00:00",
        activity_title="FTP Test (aborted)", duration_s=240, avg_power=300,
    )
    insert_activity(
        db, external_id="2", start_time="2026-09-29T08:00:00+00:00",
        activity_title="FTP Test", duration_s=1200, avg_power=175,
    )

    md = profile.render(db, tmp_path / "config.json", date(2026, 10, 10))

    assert md.count("2026-09-29") == 1
    assert "300" not in md


def test_render_ftp_history_no_qualifying_rides(db, tmp_path: Path):
    insert_activity(
        db, external_id="1", start_time="2026-01-01T07:00:00+00:00",
        activity_title="FTP Test (aborted)", duration_s=120, avg_power=300,
    )

    md = profile.render(db, tmp_path / "config.json", date(2026, 10, 10))

    assert "No qualifying FTP Test rides found." in md
