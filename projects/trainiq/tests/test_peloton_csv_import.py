"""
Tests for trainiq.csv_import.peloton_csv — ARCH-PEL-003: timezone
canonicalization to UTC ISO8601 with explicit +00:00 offset.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from trainiq.csv_import.peloton_csv import (
    PROVIDER,
    UnknownTimezoneLabelError,
    _parse_timestamp,
    compute_external_id,
    import_peloton_csv,
)
from trainiq.storage.schema import open_db


# --- Test 1-3: the three real, verified timezone labels --------------------

def test_utc_suffix_produces_explicit_utc_offset():
    result = _parse_timestamp("2026-01-05 06:30 (UTC)")
    assert result.isoformat() == "2026-01-05T06:30:00+00:00"


def test_plus_01_suffix_converts_to_utc():
    result = _parse_timestamp("2026-06-22 18:43 (+01)")
    assert result.isoformat() == "2026-06-22T17:43:00+00:00"


def test_bst_suffix_converts_to_utc_using_real_date_based_dst():
    """Uses a real date from the actual CSV (2025-09-03), not an assumed
    one — September is genuinely within the UK's DST window, so the
    correct conversion is -1h, the same as +01 would produce for this
    specific date. This is the point: BST is resolved via zoneinfo's
    date-aware calculation, not a hardcoded 'BST always means +01' rule
    that would be wrong outside the real DST window."""
    result = _parse_timestamp("2025-09-03 21:38 (BST)")
    assert result.isoformat() == "2025-09-03T20:38:00+00:00"


# --- Test 4: every result is timezone-aware, never naive -------------------

def test_all_three_labels_produce_timezone_aware_results():
    for value in ("2026-01-05 06:30 (UTC)", "2026-06-22 18:43 (+01)", "2025-09-03 21:38 (BST)"):
        result = _parse_timestamp(value)
        assert result.tzinfo is not None, f"{value!r} produced a naive datetime"


# --- Test 5: unknown timezone label fails explicitly ------------------------

def test_unknown_timezone_label_raises_explicitly():
    with pytest.raises(UnknownTimezoneLabelError):
        _parse_timestamp("2026-06-22 18:43 (CEST)")


def test_unknown_timezone_label_error_names_the_bad_label():
    with pytest.raises(UnknownTimezoneLabelError, match="CEST"):
        _parse_timestamp("2026-06-22 18:43 (CEST)")


# --- Test 6: identity invariance — timezone fix must not change external_id ---

def test_external_id_unaffected_by_timezone_conversion():
    """compute_external_id() operates on the RAW row (timestamp string
    with suffix intact, never the parsed/converted datetime) — verified
    directly by inspecting the function, and confirmed here: the same raw
    row produces the same external_id regardless of what _parse_timestamp()
    does internally."""
    row = {
        "Workout Timestamp": "2026-06-22 18:43 (+01)",
        "Title": "60 min Power Zone Ride",
        "Length (minutes)": "60",
    }
    id_1 = compute_external_id(row)
    id_2 = compute_external_id(row)
    assert id_1 == id_2
    # Confirm it does NOT depend on the converted value — construct a row
    # with a different suffix but same raw timestamp string is not
    # meaningful (suffix IS part of the raw string), so instead confirm
    # the function never calls the timestamp parser at all, by checking
    # a malformed/unparseable timestamp still produces an id (parsing
    # would raise, identity must not).
    row_bad_tz = {**row, "Workout Timestamp": "2026-06-22 18:43 (NOTREAL)"}
    id_3 = compute_external_id(row_bad_tz)
    assert id_3 is not None  # identity computed successfully even though _parse_timestamp would raise on this


# --- Test 7: idempotency on the real CSV, with timezone conversion active ---

def test_real_csv_import_is_idempotent(tmp_path: Path):
    db_path = tmp_path / "test.db"
    conn = open_db(db_path)
    csv_path = "/home/claude/peloton_work/aimea75_workouts.csv"

    result_1 = import_peloton_csv(csv_path, conn)
    assert result_1.records_inserted == 120
    assert result_1.records_updated == 0

    result_2 = import_peloton_csv(csv_path, conn)
    assert result_2.records_inserted == 0
    assert result_2.records_updated == 120

    total = conn.execute("SELECT COUNT(*) as c FROM normalized_activities").fetchone()["c"]
    assert total == 120


# --- Test 8 covered by the full suite run itself, not a unit test here -----


# --- Full CSV regression: every check the deliverable requires -------------

def test_real_csv_full_regression(tmp_path: Path):
    db_path = tmp_path / "test.db"
    conn = open_db(db_path)
    csv_path = "/home/claude/peloton_work/aimea75_workouts.csv"

    result = import_peloton_csv(csv_path, conn)

    assert result.records_seen == 120
    assert result.records_inserted == 120
    assert result.unique_external_ids == 120
    assert result.collisions == []

    rows = conn.execute("SELECT provider, discipline, start_time, avg_hr FROM normalized_activities").fetchall()
    assert len(rows) == 120
    assert all(r["provider"] == PROVIDER for r in rows)

    discipline_counts = {}
    for r in rows:
        discipline_counts[r["discipline"]] = discipline_counts.get(r["discipline"], 0) + 1
    assert discipline_counts.get("cycling") == 118
    assert discipline_counts.get("other") == 2

    assert 4.15 in [r["avg_hr"] for r in rows]  # HR=4.15 preserved as raw observation

    # No naive timestamps, all end in explicit +00:00
    naive = [r["start_time"] for r in rows if not r["start_time"].endswith("+00:00")]
    assert naive == [], f"Found non-UTC-explicit timestamps: {naive}"


# --- Cross-provider compatibility: Peloton CSV format vs Strava format -----

def test_peloton_csv_timestamp_format_matches_strava_format():
    """Strava's real format (verified in connectors/strava.py:
    activity.start_date.isoformat(), stravalib always UTC-normalized) is
    ISO8601, timezone-aware, explicit +00:00 offset. This is a minimal,
    declared fixture — NOT production data — representing that exact
    shape, to confirm the Peloton CSV importer now produces a
    lexicographically and semantically comparable format."""
    strava_like_fixture = datetime(2026, 1, 5, 6, 30, 0, tzinfo=timezone.utc).isoformat()
    assert strava_like_fixture == "2026-01-05T06:30:00+00:00"

    peloton_csv_result = _parse_timestamp("2026-01-05 06:30 (UTC)").isoformat()

    assert peloton_csv_result == strava_like_fixture
    # Both are now directly, correctly comparable as UTC instants —
    # a later Strava activity and a later Peloton CSV activity on the
    # same day will sort correctly against each other.
    earlier_peloton = _parse_timestamp("2026-01-05 05:00 (UTC)").isoformat()
    assert earlier_peloton < strava_like_fixture  # lexicographic == chronological, since both are UTC-explicit
