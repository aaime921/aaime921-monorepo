"""Tests for trainiq.export.data (issue #71) — shared read access for every
export renderer."""

from __future__ import annotations

from datetime import date, datetime, timezone

from trainiq.export.data import Activity, load_activities, load_weigh_ins, resolve_as_of, sport_of

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
