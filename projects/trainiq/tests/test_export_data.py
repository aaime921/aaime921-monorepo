"""Tests for trainiq.export.data (issue #71) — shared read access for every
export renderer."""

from __future__ import annotations

from datetime import date, datetime, timezone

from trainiq.export.data import (
    Activity,
    load_activities,
    load_weigh_ins,
    parse_timestamp,
    resolve_as_of,
    sport_of,
)

from tests.conftest import insert_activity, insert_weigh_in


def _activity(**kwargs) -> Activity:
    defaults = dict(
        id=1, provider="strava", start_time=datetime(2026, 1, 1, tzinfo=timezone.utc),
        duration_s=1800, moving_time_s=None, discipline="cycling", distance_m=None,
        avg_hr=None, avg_power=None, training_load=None, training_load_method="unknown",
        activity_title=None, instructor_name=None, class_type=None, sport_type_raw=None,
        avg_pace_s_per_km=None,
    )
    defaults.update(kwargs)
    return Activity(**defaults)


# --- sport_of ---------------------------------------------------------------

def test_sport_of_cycling_is_ride():
    assert sport_of(_activity(discipline="cycling")) == "ride"


def test_sport_of_running_is_run():
    assert sport_of(_activity(discipline="running")) == "run"


def test_sport_of_strava_walk_via_sport_type_raw():
    assert sport_of(_activity(discipline="other", sport_type_raw="Walk")) == "walk"


def test_sport_of_peloton_walk_via_class_type():
    assert sport_of(_activity(discipline="other", class_type="walking_class")) == "walk"


def test_sport_of_unrecognized_other_falls_back_to_other():
    assert sport_of(_activity(discipline="other", sport_type_raw="Yoga")) == "other"
    assert sport_of(_activity(discipline="strength")) == "other"


# --- parse_timestamp ---------------------------------------------------------
# BO rework (issue #71): the real DB mixes epoch-second strings (Peloton,
# Eufy) with ISO 8601 (Strava/Strava Unofficial) in the same column; QA's
# fixtures were ISO-only, so `trainiq export` crashed on the real DB.

def test_parse_timestamp_epoch_seconds_string():
    assert parse_timestamp("1757357927") == datetime.fromtimestamp(1757357927, tz=timezone.utc)


def test_parse_timestamp_iso_with_offset():
    assert parse_timestamp("2023-10-08T16:16:17+00:00") == datetime(
        2023, 10, 8, 16, 16, 17, tzinfo=timezone.utc
    )


def test_parse_timestamp_naive_iso_treated_as_utc():
    assert parse_timestamp("2026-01-01T07:00:00").tzinfo is not None


# --- load_activities ---------------------------------------------------------

def test_load_activities_excludes_dedup_secondary_side(db):
    kept_id = insert_activity(db, external_id="1", start_time="2026-01-01T07:00:00+00:00")
    excluded_id = insert_activity(db, external_id="2", start_time="2026-01-01T07:01:00+00:00")

    activities = load_activities(db, primary_ids={kept_id})

    assert [a.id for a in activities] == [kept_id]
    assert excluded_id not in [a.id for a in activities]


def test_load_activities_sorted_by_start_time_then_id(db):
    id_a = insert_activity(db, external_id="a", start_time="2026-01-02T07:00:00+00:00")
    id_b = insert_activity(db, external_id="b", start_time="2026-01-01T07:00:00+00:00")

    activities = load_activities(db, primary_ids={id_a, id_b})

    assert [a.id for a in activities] == [id_b, id_a]


def test_load_activities_respects_since_until_window(db):
    too_early = insert_activity(db, external_id="1", start_time="2025-12-01T07:00:00+00:00")
    in_window = insert_activity(db, external_id="2", start_time="2026-01-15T07:00:00+00:00")
    too_late = insert_activity(db, external_id="3", start_time="2026-03-01T07:00:00+00:00")
    primary_ids = {too_early, in_window, too_late}

    activities = load_activities(
        db, primary_ids,
        since=datetime(2026, 1, 1, tzinfo=timezone.utc),
        until=datetime(2026, 1, 31, tzinfo=timezone.utc),
    )

    assert [a.id for a in activities] == [in_window]


def test_load_activities_naive_start_time_treated_as_utc(db):
    activity_id = insert_activity(db, external_id="1", start_time="2026-01-01T07:00:00")

    activities = load_activities(db, primary_ids={activity_id})

    assert activities[0].start_time.tzinfo is not None


def test_load_activities_peloton_epoch_start_time(db):
    """Peloton stores start_time as an epoch-second string, not ISO 8601
    (BO, issue #71 rework) — the crash on the BO's real DB."""
    activity_id = insert_activity(db, provider="peloton", external_id="1", start_time="1757357927")

    activities = load_activities(db, primary_ids={activity_id})

    assert activities[0].start_time == datetime.fromtimestamp(1757357927, tz=timezone.utc)


def test_load_activities_mixed_epoch_and_iso_sort_correctly(db):
    """A Peloton (epoch) row and a Strava Unofficial (ISO) row in the same
    table sort by true chronological order, not by raw-string order."""
    earlier_peloton = insert_activity(
        db, provider="peloton", external_id="1", start_time="1700000000"
    )  # 2023-11-14
    later_strava = insert_activity(
        db, provider="strava_unofficial", external_id="2", start_time="2026-01-01T07:00:00+00:00"
    )

    activities = load_activities(db, primary_ids={earlier_peloton, later_strava})

    assert [a.id for a in activities] == [earlier_peloton, later_strava]


# --- load_weigh_ins ----------------------------------------------------------

def test_load_weigh_ins_excludes_flagged_unless_bo_confirmed(db):
    plausible = insert_weigh_in(db, external_id="1", timestamp="2026-01-01T07:00:00+00:00", weight_kg=80.0)
    flagged = insert_weigh_in(
        db, external_id="2", timestamp="2026-01-02T07:00:00+00:00", weight_kg=25.0,
        is_weight_flagged_implausible=1,
    )
    confirmed = insert_weigh_in(
        db, external_id="3", timestamp="2026-01-03T07:00:00+00:00", weight_kg=79.0,
        is_weight_flagged_implausible=1, bo_confirmed_valid=1,
    )

    weigh_ins = load_weigh_ins(db)

    ids = {w.id for w in weigh_ins}
    assert plausible in ids
    assert confirmed in ids
    assert flagged not in ids


def test_load_weigh_ins_eufy_epoch_timestamp(db):
    """Eufy stores weigh_ins.timestamp as an epoch-second string, not ISO
    8601 (BO, issue #71 rework)."""
    weigh_in_id = insert_weigh_in(db, external_id="1", timestamp="1748027437", weight_kg=80.0)

    weigh_ins = load_weigh_ins(db)

    assert weigh_ins[0].id == weigh_in_id
    assert weigh_ins[0].timestamp == datetime.fromtimestamp(1748027437, tz=timezone.utc)


# --- resolve_as_of -----------------------------------------------------------

def test_resolve_as_of_override_wins(db):
    insert_activity(db, external_id="1", start_time="2026-05-01T07:00:00+00:00")
    assert resolve_as_of(db, date(2026, 1, 1)) == date(2026, 1, 1)


def test_resolve_as_of_defaults_to_latest_activity_date(db):
    insert_activity(db, external_id="1", start_time="2026-01-01T07:00:00+00:00")
    insert_activity(db, external_id="2", start_time="2026-03-15T07:00:00+00:00")

    assert resolve_as_of(db, None) == date(2026, 3, 15)


def test_resolve_as_of_uses_weigh_in_when_later_than_last_activity(db):
    insert_activity(db, external_id="1", start_time="2026-01-01T07:00:00+00:00")
    insert_weigh_in(db, external_id="1", timestamp="2026-02-10T07:00:00+00:00", weight_kg=80.0)

    assert resolve_as_of(db, None) == date(2026, 2, 10)


def test_resolve_as_of_empty_db_falls_back_to_today(db):
    result = resolve_as_of(db, None)
    assert result == datetime.now(timezone.utc).date()


def test_resolve_as_of_mixed_epoch_and_iso_picks_true_latest(db):
    """A plain SQL `MAX()` on the raw TEXT column is lexicographic: every
    ISO string ("2024-...") sorts after every 10-digit epoch string
    ("17...") on the leading character alone, regardless of which one is
    chronologically later. Here the epoch-stamped Peloton row is the real
    latest; a lexicographic MAX() would wrongly pick the older ISO row."""
    insert_activity(
        db, provider="strava_unofficial", external_id="1", start_time="2024-01-01T00:00:00+00:00"
    )
    insert_activity(db, provider="peloton", external_id="2", start_time="1793000000")  # 2026-10-30

    assert resolve_as_of(db, None) == datetime.fromtimestamp(1793000000, tz=timezone.utc).date()
