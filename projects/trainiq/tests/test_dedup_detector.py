"""
Tests for issue #37 — Cross-provider activity deduplication
(trainiq.dedup.detector).

Covers the requirements doc's acceptance criteria #7 (exact match, near
match, non-match, epoch-vs-ISO formats) plus idempotency, the generalized
winner rule, and a full run against the existing
scenario_cross_provider_duplicates() fixture.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from trainiq.dedup import detector
from trainiq.storage.schema import open_db


def _insert_activity(
    conn,
    provider: str,
    external_id: str,
    start_time: str,
    duration_s: int = 1800,
    discipline: str = "cycling",
    source_confidence: float = 1.0,
):
    conn.execute(
        """
        INSERT INTO normalized_activities
            (provider, external_id, start_time, duration_s, discipline, distance_m,
             avg_hr, max_hr, avg_power, max_power, calories,
             training_load, training_load_method, source_confidence)
        VALUES (?, ?, ?, ?, ?, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, ?)
        """,
        (provider, external_id, start_time, duration_s, discipline, source_confidence),
    )
    conn.commit()
    row = conn.execute(
        "SELECT id FROM normalized_activities WHERE provider = ? AND external_id = ?",
        (provider, external_id),
    ).fetchone()
    return row["id"]


def _iso(dt: datetime) -> str:
    return dt.isoformat()


@pytest.fixture
def conn(tmp_path):
    c = open_db(tmp_path / "trainiq.db")
    yield c
    c.close()


def test_exact_start_time_match_is_linked(conn):
    base = datetime(2026, 1, 5, 7, 0, 0, tzinfo=timezone.utc)
    _insert_activity(conn, "peloton", "p-1", str(int(base.timestamp())), duration_s=1800, discipline="cycling")
    _insert_activity(conn, "strava", "s-1", _iso(base), duration_s=1800, discipline="cycling")

    result = detector.run_backfill(conn)

    assert result.linked == 1
    assert result.flagged == 0
    row = conn.execute("SELECT confidence_score, resolution FROM dedup_links").fetchone()
    assert row["confidence_score"] == 1.0
    assert row["resolution"].startswith(detector.RESOLUTION_PREFIX_LINKED)


def test_near_match_within_tolerance_is_linked(conn):
    """Reproduces scenario_cross_provider_duplicates()'s own numbers: 180s
    apart, duration 1800 vs. 1790s, both cycling -> confidence 0.7, linked."""
    base = datetime(2026, 1, 5, 7, 0, 0, tzinfo=timezone.utc)
    skewed = base + timedelta(minutes=3)
    _insert_activity(conn, "peloton", "p-2", str(int(base.timestamp())), duration_s=1800, discipline="cycling")
    _insert_activity(conn, "strava", "s-2", _iso(skewed), duration_s=1790, discipline="cycling")

    result = detector.run_backfill(conn)

    assert result.linked == 1
    assert result.flagged == 0
    row = conn.execute("SELECT confidence_score, resolution FROM dedup_links").fetchone()
    assert row["confidence_score"] == 0.7
    assert row["resolution"] == "linked:primary=peloton"


def test_non_match_two_different_activities_same_day(conn):
    """A morning ride and an evening run, more than TIME_WINDOW_S apart:
    must not be linked, and run_backfill() must not raise."""
    morning = datetime(2026, 1, 5, 7, 0, 0, tzinfo=timezone.utc)
    evening = datetime(2026, 1, 5, 19, 0, 0, tzinfo=timezone.utc)
    _insert_activity(conn, "peloton", "p-3", str(int(morning.timestamp())), duration_s=1800, discipline="cycling")
    _insert_activity(conn, "strava", "s-3", _iso(evening), duration_s=2400, discipline="running")

    result = detector.run_backfill(conn)

    assert result.candidate_pairs == 0
    assert result.linked == 0
    assert result.flagged == 0
    assert conn.execute("SELECT COUNT(*) AS c FROM dedup_links").fetchone()["c"] == 0


def test_epoch_int_vs_iso8601_format_handling(conn):
    """A peloton row stored exactly as _upsert_normalized_activity persists
    it (text-encoded epoch int, via SQLite TEXT affinity on a bound int)
    paired with a strava row whose start_time is a real ISO 8601 string."""
    base = datetime(2026, 1, 5, 7, 0, 0, tzinfo=timezone.utc)
    epoch_int = int(base.timestamp())

    conn.execute(
        """
        INSERT INTO normalized_activities
            (provider, external_id, start_time, duration_s, discipline, distance_m,
             avg_hr, max_hr, avg_power, max_power, calories,
             training_load, training_load_method, source_confidence)
        VALUES ('peloton', 'p-4', ?, 1800, 'cycling', NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, 1.0)
        """,
        (epoch_int,),
    )
    conn.commit()
    stored = conn.execute(
        "SELECT start_time FROM normalized_activities WHERE external_id = 'p-4'"
    ).fetchone()["start_time"]
    assert stored == str(epoch_int)

    _insert_activity(conn, "strava", "s-4", _iso(base), duration_s=1800, discipline="cycling")

    result = detector.run_backfill(conn)

    assert result.linked == 1
    row = conn.execute("SELECT confidence_score FROM dedup_links").fetchone()
    assert row["confidence_score"] == 1.0


def test_flagged_not_merged_below_threshold(conn):
    """Near the edge of TIME_WINDOW_S, only discipline matching (no
    duration match): passes the primary gate but scores below
    AUTO_LINK_THRESHOLD -> flagged, not auto-linked."""
    base = datetime(2026, 1, 5, 7, 0, 0, tzinfo=timezone.utc)
    far = base + timedelta(seconds=290)
    _insert_activity(conn, "peloton", "p-5", str(int(base.timestamp())), duration_s=1800, discipline="cycling")
    _insert_activity(conn, "strava", "s-5", _iso(far), duration_s=3600, discipline="cycling")

    result = detector.run_backfill(conn)

    assert result.candidate_pairs == 1
    assert result.linked == 0
    assert result.flagged == 1
    row = conn.execute("SELECT confidence_score, resolution FROM dedup_links").fetchone()
    assert row["confidence_score"] < detector.AUTO_LINK_THRESHOLD
    assert row["resolution"] == detector.RESOLUTION_FLAGGED


def test_idempotent_rerun_does_not_duplicate(conn):
    base = datetime(2026, 1, 5, 7, 0, 0, tzinfo=timezone.utc)
    _insert_activity(conn, "peloton", "p-6", str(int(base.timestamp())), duration_s=1800, discipline="cycling")
    _insert_activity(conn, "strava", "s-6", _iso(base), duration_s=1800, discipline="cycling")

    first = detector.run_backfill(conn)
    assert first.linked == 1
    assert first.skipped_existing == 0

    second = detector.run_backfill(conn)
    assert second.linked == 0
    assert second.flagged == 0
    assert second.skipped_existing == 1

    assert conn.execute("SELECT COUNT(*) AS c FROM dedup_links").fetchone()["c"] == 1


def test_generalized_winner_rule_strava_vs_strava_unofficial(conn):
    base = datetime(2026, 1, 5, 7, 0, 0, tzinfo=timezone.utc)
    _insert_activity(conn, "strava", "s-7", _iso(base), duration_s=1800, discipline="cycling")
    _insert_activity(conn, "strava_unofficial", "su-7", _iso(base), duration_s=1800, discipline="cycling")

    result = detector.run_backfill(conn)

    assert result.linked == 1
    row = conn.execute("SELECT resolution FROM dedup_links").fetchone()
    assert row["resolution"] == "linked:primary=strava"


def test_full_fixture_scenario_six_pairs_linked(conn):
    """The smaller, deterministic stand-in for the issue's reported 54
    production pairs: run_backfill() against the dataset
    scenario_cross_provider_duplicates() produces (6 days of Peloton +
    Strava, ~3min clock skew) must link all 6, flag/skip none."""
    from trainiq.synthetic_dataset import _daterange, _iso as _iso_str
    from datetime import date

    start = date(2026, 1, 5)
    for i, d in enumerate(_daterange(start, 6)):
        base_hour = 7
        peloton_iso = _iso_str(d, base_hour, 0)
        peloton_epoch = int(datetime.fromisoformat(peloton_iso).timestamp())
        _insert_activity(
            conn, "peloton", f"dup-p-{i}", str(peloton_epoch), duration_s=1800, discipline="cycling"
        )
        _insert_activity(
            conn, "strava", f"dup-s-{i}", _iso_str(d, base_hour, 3), duration_s=1790, discipline="cycling"
        )

    result = detector.run_backfill(conn)

    assert result.scanned == 12
    assert result.candidate_pairs == 6
    assert result.linked == 6
    assert result.flagged == 0
    assert result.skipped_existing == 0


def test_primary_activity_ids_excludes_only_auto_linked_secondary(conn):
    base = datetime(2026, 1, 5, 7, 0, 0, tzinfo=timezone.utc)
    linked_a = _insert_activity(conn, "peloton", "pa-1", str(int(base.timestamp())), duration_s=1800, discipline="cycling")
    linked_b = _insert_activity(conn, "strava", "pa-2", _iso(base), duration_s=1800, discipline="cycling")

    far = base + timedelta(seconds=290)
    flagged_a = _insert_activity(conn, "peloton", "pa-3", str(int(base.timestamp())), duration_s=1800, discipline="cycling")
    flagged_b = _insert_activity(conn, "strava", "pa-4", _iso(far), duration_s=3600, discipline="cycling")

    detector.run_backfill(conn)

    primary_ids = detector.primary_activity_ids(conn)

    assert linked_a in primary_ids
    assert linked_b not in primary_ids
    assert flagged_a in primary_ids
    assert flagged_b in primary_ids


def test_normalize_start_time_dispatches_by_provider():
    epoch = 1791134056
    dt = detector.normalize_start_time("peloton", str(epoch))
    assert dt == datetime.fromtimestamp(epoch, tz=timezone.utc)

    iso = "2026-10-04T18:14:16+00:00"
    assert detector.normalize_start_time("strava", iso) == datetime.fromisoformat(iso)
    assert detector.normalize_start_time("strava_unofficial", iso) == datetime.fromisoformat(iso)

    with pytest.raises(ValueError):
        detector.normalize_start_time("peloton_csv", "123")


def test_primary_provider_rules():
    assert detector.primary_provider("peloton", "strava") == "peloton"
    assert detector.primary_provider("strava", "peloton") == "peloton"
    assert detector.primary_provider("strava", "strava_unofficial") == "strava"
    assert detector.primary_provider("strava_unofficial", "strava") == "strava"

    with pytest.raises(ValueError):
        detector.primary_provider("strava", "strava")

    with pytest.raises(ValueError):
        detector.primary_provider("strava", "peloton_csv")
