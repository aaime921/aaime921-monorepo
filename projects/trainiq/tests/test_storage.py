"""
Tests for Feature 0.3 — SQLite storage layer.

DoD being verified: fresh install creates schema v1; a simulated v1->v2
migration runs cleanly against a pre-populated database and a backup
exists afterward.
"""

import sqlite3

from trainiq.storage import schema


EXPECTED_TABLES = {
    "schema_version",
    "raw_activities",
    "normalized_activities",
    "weigh_ins",
    "dedup_links",
    "sync_checkpoints",
    "connector_state",
    "credentials_metadata",
    "athlete_profile",
}


def test_fresh_install_creates_schema_v1(tmp_path):
    db_path = tmp_path / "trainiq.db"
    version = schema.migrate(db_path)
    assert version == schema.CURRENT_SCHEMA_VERSION

    conn = sqlite3.connect(db_path)
    tables = {
        row[0] for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )
    }
    conn.close()
    assert EXPECTED_TABLES <= tables


def test_fresh_install_produces_no_backup():
    """There's nothing to back up on a fresh install — backup only makes
    sense when there's a prior database to protect."""
    import tempfile
    from pathlib import Path
    with tempfile.TemporaryDirectory() as d:
        db_path = Path(d) / "trainiq.db"
        result = schema.backup_database(db_path)
        assert result is None


def test_migrate_is_idempotent(tmp_path):
    db_path = tmp_path / "trainiq.db"
    schema.migrate(db_path)
    version_again = schema.migrate(db_path)
    assert version_again == schema.CURRENT_SCHEMA_VERSION


def test_open_db_returns_working_connection(tmp_path):
    db_path = tmp_path / "trainiq.db"
    conn = schema.open_db(db_path)
    conn.execute(
        "INSERT INTO raw_activities (provider, external_id, payload_json, fetched_at) "
        "VALUES (?, ?, ?, ?)",
        ("strava", "123", "{}", "2026-01-01T00:00:00Z"),
    )
    conn.commit()
    row = conn.execute("SELECT COUNT(*) FROM raw_activities").fetchone()
    assert row[0] == 1
    conn.close()


def test_normalized_activities_unique_constraint_prevents_duplicates(tmp_path):
    """Directly required by ADR-008's idempotent-upsert design — a
    (provider, external_id) pair must never produce two rows."""
    db_path = tmp_path / "trainiq.db"
    conn = schema.open_db(db_path)
    conn.execute(
        "INSERT INTO normalized_activities "
        "(provider, external_id, start_time, duration_s, discipline, source_confidence) "
        "VALUES ('strava', 'abc', '2026-01-01T00:00:00Z', 3600, 'Cycling', 1.0)"
    )
    conn.commit()
    try:
        conn.execute(
            "INSERT INTO normalized_activities "
            "(provider, external_id, start_time, duration_s, discipline, source_confidence) "
            "VALUES ('strava', 'abc', '2026-01-01T00:00:00Z', 3600, 'Cycling', 1.0)"
        )
        conn.commit()
        raised = False
    except sqlite3.IntegrityError:
        raised = True
    conn.close()
    assert raised, "Duplicate (provider, external_id) should violate the UNIQUE constraint"


def test_migration_backs_up_existing_database(tmp_path):
    """Simulates a v1->v2-style migration: pre-populate a v1 DB, then
    request a migration again and confirm a timestamped backup is created
    before anything else happens."""
    db_path = tmp_path / "trainiq.db"
    schema.migrate(db_path)  # creates v1
    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO raw_activities (provider, external_id, payload_json, fetched_at) "
        "VALUES ('strava', '1', '{}', '2026-01-01T00:00:00Z')"
    )
    conn.commit()
    conn.close()

    # Re-running migrate() against an existing DB must back it up first,
    # even though there's no v2 migration defined yet (simulating the
    # "backup happens before migration logic runs" ordering).
    schema.migrate(db_path)
    backups = list((db_path.parent / "backups").glob("*.bak"))
    assert backups, "Expected at least one backup after migrating an existing database"


def test_backup_retention_keeps_only_last_five(tmp_path):
    db_path = tmp_path / "trainiq.db"
    schema.migrate(db_path)
    for _ in range(8):
        schema.backup_database(db_path)
    backups = list((db_path.parent / "backups").glob("*.bak"))
    assert len(backups) <= 5


def test_get_schema_version_on_nonexistent_db_reports_zero(tmp_path):
    db_path = tmp_path / "does_not_exist.db"
    conn = sqlite3.connect(db_path)
    assert schema.get_schema_version(conn) == 0
    conn.close()


# --- Epic 7, slice 1: athlete_profile schema (isolated from any loader/wiring) ---

def test_v3_migration_creates_athlete_profile_table(tmp_path):
    db_path = tmp_path / "trainiq.db"
    version = schema.migrate(db_path)
    assert version == schema.CURRENT_SCHEMA_VERSION

    conn = sqlite3.connect(db_path)
    columns = {row[1] for row in conn.execute("PRAGMA table_info(athlete_profile)")}
    conn.close()
    assert columns == {"id", "sex", "date_of_birth", "resting_hr", "max_hr", "ftp_watts"}


def test_athlete_profile_every_field_is_nullable_except_id(tmp_path):
    """Milestone B: 'missing values reduce confidence, never block
    functionality' — every field except the singleton id must accept
    NULL, since a freshly-installed TrainIQ has no athlete data yet."""
    db_path = tmp_path / "trainiq.db"
    schema.migrate(db_path)
    conn = sqlite3.connect(db_path)

    conn.execute("INSERT INTO athlete_profile (id) VALUES (1)")
    conn.commit()
    row = conn.execute("SELECT * FROM athlete_profile").fetchone()
    conn.close()

    assert row == (1, None, None, None, None, None)


def test_athlete_profile_singleton_constraint_rejects_a_second_row(tmp_path):
    """The specific property the singleton pattern exists to guarantee:
    the database itself, not application discipline, must make a second
    profile impossible."""
    db_path = tmp_path / "trainiq.db"
    schema.migrate(db_path)
    conn = sqlite3.connect(db_path)
    conn.execute("INSERT INTO athlete_profile (id, sex) VALUES (1, 'male')")
    conn.commit()

    import pytest
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT INTO athlete_profile (id, sex) VALUES (2, 'female')")
    conn.close()


def test_athlete_profile_singleton_constraint_rejects_any_id_other_than_one(tmp_path):
    db_path = tmp_path / "trainiq.db"
    schema.migrate(db_path)
    conn = sqlite3.connect(db_path)

    import pytest
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT INTO athlete_profile (id, sex) VALUES (2, 'male')")
    conn.close()


def test_athlete_profile_can_be_updated_in_place(tmp_path):
    """The realistic usage pattern: the singleton row gets updated over
    time (the athlete corrects their FTP, adds a resting HR later), not
    recreated. INSERT OR REPLACE on the fixed id=1 achieves this cleanly."""
    db_path = tmp_path / "trainiq.db"
    schema.migrate(db_path)
    conn = sqlite3.connect(db_path)
    conn.execute("INSERT INTO athlete_profile (id, sex, resting_hr) VALUES (1, 'male', 50)")
    conn.commit()

    conn.execute(
        "INSERT OR REPLACE INTO athlete_profile (id, sex, resting_hr, ftp_watts) VALUES (1, 'male', 50, 250)"
    )
    conn.commit()

    row = conn.execute("SELECT sex, resting_hr, ftp_watts FROM athlete_profile").fetchone()
    count = conn.execute("SELECT COUNT(*) FROM athlete_profile").fetchone()[0]
    conn.close()

    assert row == ("male", 50, 250)
    assert count == 1  # still exactly one row, not two


def test_v2_to_v3_migration_preserves_existing_data(tmp_path):
    """Standard upgrade-path check, same shape as the v1->v2 precedent
    this project already established: a database that predates
    athlete_profile must migrate cleanly without losing anything already
    stored, and a backup must exist afterward."""
    db_path = tmp_path / "trainiq.db"
    schema.migrate(db_path)  # fresh install, already at CURRENT_SCHEMA_VERSION

    # Simulate "was already at v2" by manually rolling schema_version back
    # and undoing everything v3+ added, then re-migrating. weigh_ins must
    # also be rolled back to its pre-v4 shape (ADR-039) — otherwise
    # re-running the v4 migration script below would try to add columns
    # that already exist.
    conn = sqlite3.connect(db_path)
    conn.execute("DROP TABLE athlete_profile")
    conn.execute("DROP TABLE weigh_ins")
    conn.execute(
        """
        CREATE TABLE weigh_ins (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            provider TEXT NOT NULL,
            external_id TEXT NOT NULL,
            timestamp TEXT NOT NULL,
            weight_kg REAL,
            body_fat_pct REAL,
            muscle_mass_pct REAL,
            UNIQUE(provider, external_id)
        )
        """
    )
    conn.execute("UPDATE schema_version SET version = 2")
    conn.execute(
        "INSERT INTO credentials_metadata (provider, connected) VALUES ('strava', 1)"
    )
    conn.commit()
    conn.close()

    version = schema.migrate(db_path)

    assert version == schema.CURRENT_SCHEMA_VERSION
    conn = sqlite3.connect(db_path)
    preserved = conn.execute(
        "SELECT connected FROM credentials_metadata WHERE provider = 'strava'"
    ).fetchone()
    conn.close()
    assert preserved == (1,)  # pre-existing data survived the migration
    backups = list((db_path.parent / "backups").glob("*.bak"))
    assert len(backups) >= 1


# --- Acceptance Review: multi-version migration integrity ---

def test_v1_database_migrates_directly_to_current_version_in_one_call(tmp_path):
    """A real user's database could realistically be frozen at v1 (last
    opened before ADR-038 or Epic 7 shipped) and then upgraded straight to
    whatever CURRENT_SCHEMA_VERSION is now, in a single migrate() call —
    not step-by-step under test control. Every prior migration test
    exercises one step (v1 fresh install, v1->v2, v2->v3) in isolation;
    this is the first test proving the full chain composes correctly."""
    db_path = tmp_path / "trainiq.db"
    conn = sqlite3.connect(db_path)
    conn.executescript(schema._MIGRATIONS[1])
    conn.execute("UPDATE schema_version SET version = 1")
    conn.execute("INSERT INTO credentials_metadata (provider, connected) VALUES ('strava', 1)")
    conn.commit()
    conn.close()

    version = schema.migrate(db_path)

    assert version == schema.CURRENT_SCHEMA_VERSION
    conn = sqlite3.connect(db_path)
    tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    connector_state_cols = {row[1] for row in conn.execute("PRAGMA table_info(connector_state)")}
    preserved = conn.execute("SELECT connected FROM credentials_metadata WHERE provider='strava'").fetchone()
    conn.close()

    assert "athlete_profile" in tables
    assert "state_entered_at" in connector_state_cols  # ADR-038's columns present
    assert preserved == (1,)  # data from the oldest version survived every intermediate step


# --- ADR-039 / Issue #38: weigh-in plausibility flagging --------------------

def test_v4_migration_adds_plausibility_columns_to_weigh_ins(tmp_path):
    db_path = tmp_path / "trainiq.db"
    version = schema.migrate(db_path)
    assert version == schema.CURRENT_SCHEMA_VERSION

    conn = sqlite3.connect(db_path)
    columns = {row[1] for row in conn.execute("PRAGMA table_info(weigh_ins)")}
    conn.close()
    assert {
        "is_flagged_implausible", "plausibility_reason",
        "bo_confirmed_valid", "bo_confirmed_at",
    } <= columns


def test_v4_migration_new_columns_default_to_unflagged_unconfirmed(tmp_path):
    """Defaults matter here specifically because every pre-existing row
    created before this migration must come through as 'not flagged, not
    confirmed' — never NULL/true by accident — per AC8's requirement that
    no pre-v4 assertion elsewhere in this suite is disturbed."""
    db_path = tmp_path / "trainiq.db"
    conn = sqlite3.connect(db_path)
    conn.executescript(schema._MIGRATIONS[1])
    conn.execute("UPDATE schema_version SET version = 1")
    conn.execute(
        "INSERT INTO weigh_ins (provider, external_id, timestamp, weight_kg) "
        "VALUES ('eufy', 'w1', '2026-01-01T00:00:00Z', 85.0)"
    )
    conn.commit()
    conn.close()

    schema.migrate(db_path)

    conn = sqlite3.connect(db_path)
    row = conn.execute(
        "SELECT is_flagged_implausible, bo_confirmed_valid, bo_confirmed_at "
        "FROM weigh_ins WHERE external_id = 'w1'"
    ).fetchone()
    conn.close()
    assert row == (0, 0, None)


def test_v4_backfill_flags_exactly_the_issues_known_bad_rows(tmp_path):
    """AC4's proof: fixture rows reproducing the issue's exact evidence
    table (7 implausible readings interleaved, by timestamp, with normal
    80-88.3 kg readings), inserted directly at schema v3. After migrate()
    to v4, exactly those 7 rows must end up flagged and nothing else."""
    db_path = tmp_path / "trainiq.db"
    conn = sqlite3.connect(db_path)
    conn.executescript(schema._MIGRATIONS[1])
    conn.executescript(schema._MIGRATIONS[2])
    conn.executescript(schema._MIGRATIONS[3])
    conn.execute("UPDATE schema_version SET version = 3")

    normal_readings = [
        ("n1", "2025-01-01T08:00:00Z", 82.0, 18.0),
        ("n2", "2025-03-01T08:00:00Z", 84.5, 17.5),
        ("n3", "2025-05-01T08:00:00Z", 83.0, 18.2),
        ("n4", "2025-09-14T18:50:00Z", 85.0, 17.0),
        ("n5", "2026-07-05T10:00:00Z", 88.3, 16.0),
        ("n6", "2026-07-06T18:00:00Z", 86.0, 17.8),
        ("n7", "2026-09-05T19:00:00Z", 80.0, 19.0),
    ]
    bad_readings = [
        ("bad1", "2025-09-14T18:52:00Z", 19.15, 0.0),
        ("bad2", "2025-09-14T18:53:00Z", 19.15, 5.0),
        ("bad3", "2025-09-20T09:00:00Z", 25.0, 0.0),
        ("bad4", "2025-10-01T09:00:00Z", 30.0, 5.0),
        ("bad5", "2026-07-05T10:53:00Z", 20.55, 0.0),
        ("bad6", "2026-07-06T18:43:00Z", 20.7, 5.0),
        ("bad7", "2026-09-05T19:30:00Z", 35.8, 0.0),
    ]
    for external_id, timestamp, weight_kg, body_fat_pct in normal_readings + bad_readings:
        conn.execute(
            "INSERT INTO weigh_ins (provider, external_id, timestamp, weight_kg, body_fat_pct) "
            "VALUES ('eufy', ?, ?, ?, ?)",
            (external_id, timestamp, weight_kg, body_fat_pct),
        )
    conn.commit()
    conn.close()

    version = schema.migrate(db_path)
    assert version == schema.CURRENT_SCHEMA_VERSION

    conn = sqlite3.connect(db_path)
    flagged = {
        row[0] for row in conn.execute(
            "SELECT external_id FROM weigh_ins WHERE is_flagged_implausible = 1"
        )
    }
    conn.close()

    assert flagged == {eid for eid, *_ in bad_readings}
