"""Cross-cutting tests for trainiq.export.run_export (issue #71): the
pieces that only show up once every renderer is wired together —
determinism, the size target, dedup counted once across every file, and
directory creation."""

from __future__ import annotations

from datetime import date
from pathlib import Path

from trainiq.export import run_export

from tests.conftest import insert_activity, insert_weigh_in

_EXPECTED_FILES = {
    "profile.md", "recent.md", "recent.json", "last_done.md",
    "load.md", "load.json", "weight.md", "weight.json", "performance.md",
}


def _seed_small_dataset(conn):
    primary = insert_activity(
        conn, external_id="1", start_time="2026-10-05T07:00:00+00:00",
        discipline="cycling", duration_s=1800, avg_power=200, training_load=75.0,
        training_load_method="tss",
    )
    secondary = insert_activity(
        conn, external_id="2", start_time="2026-10-05T07:01:00+00:00",
        discipline="cycling", duration_s=1800, avg_power=200, training_load=75.0,
        training_load_method="tss",
    )
    conn.execute(
        "INSERT INTO dedup_links (activity_id_a, activity_id_b, confidence_score, resolution) "
        "VALUES (?, ?, 0.9, 'linked:primary=strava')",
        (min(primary, secondary), max(primary, secondary)),
    )
    conn.commit()
    insert_weigh_in(conn, external_id="1", timestamp="2026-10-05T07:00:00+00:00", weight_kg=80.0)
    return primary, secondary


def test_run_export_creates_out_dir_and_all_files(db, tmp_path: Path):
    _seed_small_dataset(db)
    out_dir = tmp_path / "export_out"

    written = run_export(db, out_dir, tmp_path / "config.json", as_of=date(2026, 10, 10))

    assert out_dir.exists()
    assert {p.name for p in written} == _EXPECTED_FILES


def test_run_export_is_deterministic_same_db_same_as_of(db, tmp_path: Path):
    _seed_small_dataset(db)
    config_path = tmp_path / "config.json"

    out_a = tmp_path / "a"
    out_b = tmp_path / "b"
    run_export(db, out_a, config_path, as_of=date(2026, 10, 10))
    run_export(db, out_b, config_path, as_of=date(2026, 10, 10))

    for name in _EXPECTED_FILES:
        assert (out_a / name).read_bytes() == (out_b / name).read_bytes(), name


def test_run_export_default_as_of_is_deterministic_without_wall_clock(db, tmp_path: Path):
    """No --as-of given: resolve_as_of() must anchor to the latest data
    date, not datetime.now() — two runs back to back must still match."""
    _seed_small_dataset(db)
    config_path = tmp_path / "config.json"

    out_a = tmp_path / "a"
    out_b = tmp_path / "b"
    run_export(db, out_a, config_path, as_of=None)
    run_export(db, out_b, config_path, as_of=None)

    for name in _EXPECTED_FILES:
        assert (out_a / name).read_bytes() == (out_b / name).read_bytes(), name


def test_run_export_dedup_pair_counts_once_in_recent_and_load(db, tmp_path: Path):
    _seed_small_dataset(db)

    written = run_export(db, tmp_path / "out", tmp_path / "config.json", as_of=date(2026, 10, 10))

    import json
    recent_payload = json.loads((tmp_path / "out" / "recent.json").read_text())
    assert len(recent_payload["activities"]) == 1

    load_payload = json.loads((tmp_path / "out" / "load.json").read_text())
    last_week = load_payload["weeks"][-1]
    assert last_week["load"] == 75.0  # not 150.0 — the secondary side isn't double-counted


def test_run_export_each_file_is_under_50kb_on_a_large_dataset(db, tmp_path: Path):
    """AC 1/2: a target of < 50 KB per file, proven against a dataset much
    larger than the BO's real one (row caps in recent.md/last_done.md are
    the mechanism that keeps this true regardless of history length)."""
    rows = [
        (
            "strava", str(i), f"2024-01-{(i % 28) + 1:02d}T07:00:00+00:00",
            1800, "cycling" if i % 2 == 0 else "running", 50.0, "tss", 0.5,
            f"Some Fairly Long Class Title Number {i}", f"Instructor {i % 80}",
        )
        for i in range(2000)
    ]
    db.executemany(
        "INSERT INTO normalized_activities "
        "(provider, external_id, start_time, duration_s, discipline, training_load, "
        " training_load_method, source_confidence, activity_title, instructor_name) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        rows,
    )
    # A realistic-but-generous recent window (~1.5 activities/day, well
    # under recent.md's 200-row cap) — the cap itself is exercised
    # separately in test_export_recent.py; this test is about the 50 KB
    # target at a realistic single-athlete data volume, not the cap.
    recent_rows = [
        (
            "strava", f"recent-{i}", f"2026-10-{(i % 30) + 1:02d}T07:00:00+00:00",
            1800, "cycling", 50.0, "tss", 0.5,
            f"Recent Class Title {i}", f"Instructor {i % 30}",
        )
        for i in range(45)
    ]
    db.executemany(
        "INSERT INTO normalized_activities "
        "(provider, external_id, start_time, duration_s, discipline, training_load, "
        " training_load_method, source_confidence, activity_title, instructor_name) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        recent_rows,
    )
    weigh_in_rows = [
        ("eufy", f"w{i}", f"2024-{(i % 12) + 1:02d}-{(i % 28) + 1:02d}T07:00:00+00:00", 80.0 + (i % 10))
        for i in range(500)
    ]
    db.executemany(
        "INSERT INTO weigh_ins (provider, external_id, timestamp, weight_kg) VALUES (?, ?, ?, ?)",
        weigh_in_rows,
    )
    db.commit()

    written = run_export(db, tmp_path / "out", tmp_path / "config.json", as_of=date(2026, 10, 10))

    for path in written:
        assert path.stat().st_size < 50 * 1024, f"{path.name} is {path.stat().st_size} bytes"
