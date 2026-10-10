"""Tests for trainiq.export.profile (issue #71, AC 2; issue #79 rework)."""

from __future__ import annotations

from datetime import date
from pathlib import Path

from trainiq.athlete.profile import AthleteProfile
from trainiq.athlete.store import save_athlete_profile
from trainiq.config import set_weight_goal
from trainiq.dedup.detector import primary_activity_ids
from trainiq.export import profile

from tests.conftest import insert_activity


def _render(conn, config_path, as_of):
    return profile.render(conn, config_path, as_of, primary_activity_ids(conn))


def test_render_includes_athlete_facts(db, tmp_path: Path):
    save_athlete_profile(
        db, AthleteProfile(sex="male", date_of_birth="1990-01-01", resting_hr=69, max_hr=181, ftp_watts=166)
    )
    config_path = tmp_path / "config.json"

    md = _render(db, config_path, date(2026, 10, 10))

    assert "166 W" in md
    assert "69 bpm" in md
    assert "181 bpm" in md
    assert "male" in md
    assert "1990-01-01" in md


def test_render_with_no_profile_stored_does_not_crash(db, tmp_path: Path):
    md = _render(db, tmp_path / "config.json", date(2026, 10, 10))
    assert "No athlete profile stored" in md


def test_render_weight_goal_not_configured(db, tmp_path: Path):
    md = _render(db, tmp_path / "config.json", date(2026, 10, 10))
    assert "not configured" in md.lower()


def test_render_weight_goal_configured(db, tmp_path: Path):
    config_path = tmp_path / "config.json"
    set_weight_goal(config_path, start_weight_kg=82.1, goal_weight_kg=72.0)

    md = _render(db, config_path, date(2026, 10, 10))

    assert "82.1 kg" in md
    assert "72.0 kg" in md


def test_render_ftp_history_lists_ftp_test_rides(db, tmp_path: Path):
    insert_activity(
        db, external_id="1", start_time="2026-01-01T07:00:00+00:00",
        discipline="cycling", activity_title="20-min FTP Test", avg_power=180,
    )
    insert_activity(
        db, external_id="2", start_time="2026-02-01T07:00:00+00:00",
        discipline="cycling", activity_title="Power Zone Endurance", avg_power=180,
    )

    md = _render(db, tmp_path / "config.json", date(2026, 10, 10))

    assert "2026-01-01" in md
    assert "2026-02-01" not in md


def test_render_ftp_history_respects_as_of(db, tmp_path: Path):
    insert_activity(
        db, external_id="1", start_time="2026-12-01T07:00:00+00:00",
        discipline="cycling", activity_title="FTP Test",
    )

    md = _render(db, tmp_path / "config.json", date(2026, 10, 10))

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

    md = _render(db, tmp_path / "config.json", date(2026, 10, 10))

    assert "2025-09-08" in md


def test_render_ftp_history_shows_estimate_marks_latest_and_best(db, tmp_path: Path):
    """BO (2026-10-10, issue #79): estimated FTP = round(0.95 * avg power).
    The most recent test is "latest", the highest estimate is "best" — they
    can be different rows — and the profile's current FTP is never marked
    on a row; it's shown as its own separate line."""
    save_athlete_profile(
        db, AthleteProfile(sex="male", date_of_birth="1990-01-01", resting_hr=69, max_hr=181, ftp_watts=166)
    )
    insert_activity(
        db, external_id="1", start_time="2025-09-18T07:00:00+00:00",
        activity_title="FTP Test", duration_s=1200, avg_power=160,
    )
    insert_activity(
        db, external_id="2", start_time="2025-10-15T07:00:00+00:00",
        activity_title="FTP Test", duration_s=1200, avg_power=180,
    )
    insert_activity(
        db, external_id="3", start_time="2026-01-28T07:00:00+00:00",
        activity_title="FTP Test", duration_s=1200, avg_power=200,
    )
    insert_activity(
        db, external_id="4", start_time="2026-09-29T07:00:00+00:00",
        activity_title="FTP Test", duration_s=1200, avg_power=176,
    )

    md = _render(db, tmp_path / "config.json", date(2026, 10, 10))

    assert "| 2025-09-18 | 160 | 152 |  |" in md
    assert "| 2025-10-15 | 180 | 171 |  |" in md
    assert "| 2026-01-28 | 200 | 190 | best |" in md
    assert "| 2026-09-29 | 176 | 167 | latest |" in md
    assert "| current |" not in md
    assert "Current FTP (profile): 166 W" in md


def test_render_ftp_history_profile_ftp_shown_when_no_tests(db, tmp_path: Path):
    save_athlete_profile(
        db, AthleteProfile(sex="male", date_of_birth="1990-01-01", resting_hr=69, max_hr=181, ftp_watts=166)
    )

    md = _render(db, tmp_path / "config.json", date(2026, 10, 10))

    assert "No qualifying FTP Test rides found." in md
    assert "Current FTP (profile): 166 W" in md


def test_render_ftp_history_profile_ftp_dash_when_no_profile(db, tmp_path: Path):
    md = _render(db, tmp_path / "config.json", date(2026, 10, 10))

    assert "Current FTP (profile): -" in md


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

    md = _render(db, tmp_path / "config.json", date(2026, 10, 10))

    assert md.count("2026-09-29") == 1
    assert "300" not in md


def test_render_ftp_history_aborted_attempt_uses_moving_time(db, tmp_path: Path):
    """issue #79: the 15-min minimum is checked against moving time when
    present, not elapsed time — an aborted attempt that was left running
    (long elapsed, short moving) must still be excluded."""
    insert_activity(
        db, external_id="1", start_time="2026-09-29T07:00:00+00:00",
        activity_title="FTP Test (aborted)", duration_s=3600, moving_time_s=240, avg_power=300,
    )

    md = _render(db, tmp_path / "config.json", date(2026, 10, 10))

    assert "No qualifying FTP Test rides found." in md


def test_render_ftp_history_no_qualifying_rides(db, tmp_path: Path):
    insert_activity(
        db, external_id="1", start_time="2026-01-01T07:00:00+00:00",
        activity_title="FTP Test (aborted)", duration_s=120, avg_power=300,
    )

    md = _render(db, tmp_path / "config.json", date(2026, 10, 10))

    assert "No qualifying FTP Test rides found." in md


def test_render_ftp_history_linked_pair_counts_once(db, tmp_path: Path):
    """issue #79: a linked Peloton/Strava pair of the same FTP test must
    not produce the duplicate `-`/`-` row that `export/profile.py` used to
    emit for the Strava copy (no power data on that side) — filtering on
    `primary_ids`, like every other renderer, fixes it."""
    primary = insert_activity(
        db, provider="peloton", external_id="1", start_time="1757357927",  # 2025-09-08
        discipline="cycling", activity_title="20-min FTP Test", duration_s=1200, avg_power=180,
    )
    secondary = insert_activity(
        db, provider="strava", external_id="2", start_time="2025-09-08T07:05:00+00:00",
        discipline="cycling", activity_title="20-min FTP Test", duration_s=1200, avg_power=None,
    )
    db.execute(
        "INSERT INTO dedup_links (activity_id_a, activity_id_b, confidence_score, resolution) "
        "VALUES (?, ?, 0.9, 'linked:primary=peloton')",
        (min(primary, secondary), max(primary, secondary)),
    )
    db.commit()

    md = _render(db, tmp_path / "config.json", date(2026, 10, 10))

    assert md.count("2025-09-08") == 1
    assert "| 2025-09-08 | 180 | 171 | latest, best |" in md


def test_render_ftp_history_same_day_duplicate_keeps_higher_power(db, tmp_path: Path):
    """issue #79 (defensive, per the Architect's design): two primary rows
    sharing a date — not a dedup-linked pair, just two distinct qualifying
    rides on the same day — keep the one with the larger avg_power
    (non-NULL over NULL) rather than showing two rows for that date."""
    insert_activity(
        db, external_id="1", start_time="2026-09-29T07:00:00+00:00",
        activity_title="FTP Test", duration_s=1200, avg_power=150,
    )
    insert_activity(
        db, external_id="2", start_time="2026-09-29T19:00:00+00:00",
        activity_title="FTP Test", duration_s=1200, avg_power=190,
    )

    md = _render(db, tmp_path / "config.json", date(2026, 10, 10))

    assert md.count("2026-09-29") == 1
    assert "| 2026-09-29 | 190 | 180 | latest, best |" in md
