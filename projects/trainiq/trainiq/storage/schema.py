"""
trainiq.storage.schema — Feature 0.3

SQLite schema v1 (Milestone 4 §5) plus a hand-written, numbered migration
runner (Milestone 4's explicit rejection of Alembic as unnecessary machinery
for a single-user local file — Decision Matrix 11.1).

Tables, per Milestone 4 §5:
  raw_activities        — unmodified provider payloads
  normalized_activities — canonical post-Normalization schema (Milestone A §7)
  weigh_ins              — Eufy-specific body-composition records
  dedup_links             — merge/flag decisions with confidence scores
  sync_checkpoints        — per-provider resume points (ADR-008)
  connector_state         — Provider State Machine state (ADR-010)
  credentials_metadata    — NON-secret metadata only; secrets live in Keychain
  schema_version           — single-row migration tracking
"""

from __future__ import annotations

import shutil
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from trainiq.storage.backfill import backfill_weigh_in_plausibility, decouple_weigh_in_plausibility

CURRENT_SCHEMA_VERSION = 11

_MIGRATIONS: dict[int, str] = {
    1: """
        CREATE TABLE schema_version (
            version INTEGER NOT NULL
        );
        INSERT INTO schema_version (version) VALUES (0);

        CREATE TABLE raw_activities (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            provider TEXT NOT NULL,
            external_id TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            fetched_at TEXT NOT NULL,
            UNIQUE(provider, external_id)
        );

        CREATE TABLE normalized_activities (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            provider TEXT NOT NULL,
            external_id TEXT NOT NULL,
            start_time TEXT NOT NULL,
            duration_s INTEGER NOT NULL,
            discipline TEXT NOT NULL,
            distance_m REAL,
            avg_hr INTEGER,
            max_hr INTEGER,
            avg_power INTEGER,
            max_power INTEGER,
            calories INTEGER,
            training_load REAL,
            training_load_method TEXT,
            source_confidence REAL NOT NULL,
            UNIQUE(provider, external_id)
        );
        CREATE INDEX idx_normalized_activities_provider_extid
            ON normalized_activities(provider, external_id);
        CREATE INDEX idx_normalized_activities_start_time
            ON normalized_activities(start_time);

        CREATE TABLE weigh_ins (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            provider TEXT NOT NULL,
            external_id TEXT NOT NULL,
            timestamp TEXT NOT NULL,
            weight_kg REAL,
            body_fat_pct REAL,
            muscle_mass_pct REAL,
            UNIQUE(provider, external_id)
        );

        CREATE TABLE dedup_links (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            activity_id_a INTEGER NOT NULL REFERENCES normalized_activities(id),
            activity_id_b INTEGER NOT NULL REFERENCES normalized_activities(id),
            confidence_score REAL NOT NULL,
            resolution TEXT NOT NULL
        );

        CREATE TABLE sync_checkpoints (
            provider TEXT NOT NULL,
            strategy TEXT NOT NULL DEFAULT 'default',
            last_success_at TEXT,
            last_cursor TEXT,
            PRIMARY KEY (provider, strategy)
        );

        CREATE TABLE connector_state (
            provider TEXT PRIMARY KEY,
            state TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            detail TEXT
        );

        CREATE TABLE credentials_metadata (
            provider TEXT PRIMARY KEY,
            connected INTEGER NOT NULL DEFAULT 0,
            last_refreshed_at TEXT
        );
    """,
    # ADR-038: Connector Lifecycle Policy. Four new columns on
    # connector_state, exactly as specified in the ADR §3 — this is the
    # first real (non-synthetic) schema migration this project has run.
    2: """
        ALTER TABLE connector_state ADD COLUMN state_entered_at TEXT;
        ALTER TABLE connector_state ADD COLUMN last_attempt_at TEXT;
        ALTER TABLE connector_state ADD COLUMN attempt_count_in_state INTEGER NOT NULL DEFAULT 0;
        ALTER TABLE connector_state ADD COLUMN next_eligible_retry_at TEXT;
    """,
    # Epic 7, slice 1: Athlete Knowledge Model persistence. Per Milestone B
    # §2/ADR-020: persist only irreducible facts, never anything derivable.
    # Every field is nullable — Milestone B's "missing values reduce
    # confidence, never block functionality" applies here directly.
    #
    # Singleton pattern (id INTEGER PRIMARY KEY CHECK (id = 1)): TrainIQ is
    # single-user throughout this project's design, so "exactly one
    # athlete profile, ever" is enforced by the database itself rather
    # than left as an application-level convention someone could
    # accidentally violate later. Unlike credentials_metadata (correctly
    # keyed per-provider, since multiple providers exist), there is no
    # natural key here — the CHECK constraint is the key.
    #
    # date_of_birth is persisted but deliberately NOT consumed by the
    # training-load engine in this epic (Chief Architect decision,
    # Question A of the Epic 7 Discovery Report) — age-based max_hr
    # estimation is an explicitly separate, un-scoped future capability,
    # not part of Epic 7's persistence-and-wiring responsibility.
    3: """
        CREATE TABLE athlete_profile (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            sex TEXT,
            date_of_birth TEXT,
            resting_hr INTEGER,
            max_hr INTEGER,
            ftp_watts INTEGER
        );
    """,
    # ADR-039 / Issue #38: weigh-in plausibility flagging.
    # is_flagged_implausible/plausibility_reason are the rule's own verdict,
    # overwritten on every re-evaluation (e.g. a full Eufy resync, BL-006).
    # bo_confirmed_valid/bo_confirmed_at are BO-owned — sync code must never
    # write them (see sync/engine.py's _upsert_weigh_in column list).
    4: """
        ALTER TABLE weigh_ins ADD COLUMN is_flagged_implausible INTEGER NOT NULL DEFAULT 0;
        ALTER TABLE weigh_ins ADD COLUMN plausibility_reason TEXT;
        ALTER TABLE weigh_ins ADD COLUMN bo_confirmed_valid INTEGER NOT NULL DEFAULT 0;
        ALTER TABLE weigh_ins ADD COLUMN bo_confirmed_at TEXT;
    """,
    # ADR-039 (corrected by Issue #42): decouple body-fat plausibility from
    # weight plausibility. A body-fat verdict must never suppress the weight.
    5: """
        ALTER TABLE weigh_ins RENAME COLUMN is_flagged_implausible TO is_weight_flagged_implausible;
        ALTER TABLE weigh_ins RENAME COLUMN plausibility_reason TO weight_plausibility_reason;
        ALTER TABLE weigh_ins ADD COLUMN is_body_fat_flagged_implausible INTEGER NOT NULL DEFAULT 0;
        ALTER TABLE weigh_ins ADD COLUMN body_fat_plausibility_reason TEXT;
    """,
    # Issue #46: class title/instructor/class type/planned length (Peloton)
    # and raw name/sport_type (Strava, Strava-unofficial). All 6 nullable —
    # providers that don't populate a given column simply leave it NULL,
    # same pattern athlete_profile's and weigh_ins' nullable columns already
    # use. See docs/trainiq/architecture/46-peloton-strava-class-metadata.md
    # ("Why one table, not activity_details") for why these live directly on
    # normalized_activities rather than a separate table.
    # Renumbered 5 -> 6 when merged after Issue #42 (which took v5).
    6: """
        ALTER TABLE normalized_activities ADD COLUMN activity_title TEXT;
        ALTER TABLE normalized_activities ADD COLUMN instructor_name TEXT;
        ALTER TABLE normalized_activities ADD COLUMN class_type TEXT;
        ALTER TABLE normalized_activities ADD COLUMN planned_duration_s INTEGER;
        ALTER TABLE normalized_activities ADD COLUMN provider_class_id TEXT;
        ALTER TABLE normalized_activities ADD COLUMN sport_type_raw TEXT;
    """,
    # Issue #48: elevation gain, moving time, indoor/outdoor flag for
    # Strava activities. Nullable, no default — "missing" and "confirmed
    # outdoor/zero-elevation" are different facts (AC3); ADD COLUMN with
    # no NOT NULL/DEFAULT backfills existing rows with NULL, which is
    # correct for them regardless (their raw payloads predate this fix).
    # Renumbered 6 -> 7 when rebased after Issue #46 (which took v6).
    7: """
        ALTER TABLE normalized_activities ADD COLUMN elevation_gain_m REAL;
        ALTER TABLE normalized_activities ADD COLUMN moving_time_s INTEGER;
        ALTER TABLE normalized_activities ADD COLUMN is_indoor INTEGER;
    """,
    # Issue #58 (resolves BL-011): the ride-details response fetched for
    # every successful Peloton class lookup already carries
    # difficulty_estimate at zero marginal network cost — a 7th nullable
    # enrichment column, same all-nullable pattern as the 6 added in v6.
    # Renumbered 7 -> 8 when merged after Issue #48 (which took v7).
    8: """
        ALTER TABLE normalized_activities ADD COLUMN difficulty_estimate REAL;
    """,
    # Issue #47, AC1 only: HR-zone durations (z1-z5, seconds) and Peloton's
    # own effort-points score. Sourced from `effort_zones` on the SAME
    # list-endpoint record download() already fetches every pass (no new
    # network call) — unlike #46's 6 columns above, these have no "didn't
    # attempt this pass" case, so they're unconditional-overwrite in
    # upsert_normalized_activity(), not COALESCE. AC2 of #47 (avg_hr/max_hr/
    # max_power via the separate, still-UNCONFIRMED performance endpoint)
    # is explicitly out of this migration — see
    # docs/trainiq/architecture/47-peloton-heart-rate-capture.md's Task 1.
    # Renumbered 6 -> 7 per the BO's note on issue #47 (Issue #46 took v6),
    # then 7 -> 9 when merged after Issues #48 (v7) and #58 (v8).
    9: """
        ALTER TABLE normalized_activities ADD COLUMN hr_zone_1_s INTEGER;
        ALTER TABLE normalized_activities ADD COLUMN hr_zone_2_s INTEGER;
        ALTER TABLE normalized_activities ADD COLUMN hr_zone_3_s INTEGER;
        ALTER TABLE normalized_activities ADD COLUMN hr_zone_4_s INTEGER;
        ALTER TABLE normalized_activities ADD COLUMN hr_zone_5_s INTEGER;
        ALTER TABLE normalized_activities ADD COLUMN effort_points REAL;
    """,
    # Issue #47, AC2: avg_hr/max_hr/max_power (already-existing columns,
    # schema v1 — no new column for them here) now come from the
    # per-workout performance endpoint instead of being hardcoded None
    # (BL-012, resolved per the BO's live capture, 2026-10-09). The one new
    # column this migration adds is performance_fetch_status: a dedicated
    # "attempted this pass or not" marker, since avg_hr/max_hr/max_power are
    # real nullable facts and "all three NULL" is already the legitimate
    # value for "no HR monitor paired" — a sentinel cannot live inside them
    # the way class_type's own TEXT value doubles as #46's status (see
    # peloton.py's Feature 3.8 docstring).
    #
    # Named performance_fetch_status, not hr_fetch_status, per issue #57's
    # architecture doc (docs/trainiq/architecture/57-peloton-distance-performance-graph-source.md,
    # "Approach"): #57's distance fix and this issue's HR/power both read
    # the SAME fetch_workout_performance() call, so one shared status
    # column is correct — a per-concern column would always move in
    # lockstep with this one.
    # Renumbered 8 -> 10 when merged after Issues #48 (v7) and #58 (v8).
    10: """
        ALTER TABLE normalized_activities ADD COLUMN performance_fetch_status TEXT;
    """,
    # Issue #50: Strava per-activity streams (HR, pace, GPS) + Peloton ride
    # total output. avg_pace_s_per_km/total_output_kj are nullable
    # pass-throughs, same all-nullable pattern as every prior enrichment
    # column (never fabricated). streams_fetch_status is a dedicated
    # "attempted this pass or not" marker for the separate streams
    # enrichment step (trainiq/connectors/strava_streams.py) — written only
    # by that step's own narrow UPDATE, never by upsert_normalized_activity().
    # activity_tracks holds the GPS track as a compact zlib-compressed JSON
    # blob, one row per activity, no duplicates (INSERT OR REPLACE keyed on
    # activity_id).
    11: """
        ALTER TABLE normalized_activities ADD COLUMN avg_pace_s_per_km REAL;
        ALTER TABLE normalized_activities ADD COLUMN streams_fetch_status TEXT;
        ALTER TABLE normalized_activities ADD COLUMN total_output_kj REAL;
        CREATE TABLE activity_tracks (
            activity_id INTEGER PRIMARY KEY REFERENCES normalized_activities(id),
            point_count INTEGER NOT NULL,
            encoding TEXT NOT NULL,
            track BLOB NOT NULL
        );
    """,
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def backup_database(db_path: Path) -> Path | None:
    """Timestamped snapshot before any migration (Milestone 4 §5). Returns
    None if there's nothing to back up yet (fresh install)."""
    if not db_path.exists():
        return None
    backup_dir = db_path.parent / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup_path = backup_dir / f"{db_path.stem}.{stamp}.bak"
    shutil.copy2(db_path, backup_path)
    _prune_backups(backup_dir, db_path.stem, keep=5)
    return backup_path


def _prune_backups(backup_dir: Path, stem: str, keep: int) -> None:
    backups = sorted(backup_dir.glob(f"{stem}.*.bak"))
    if len(backups) > keep:
        for old in backups[:-keep]:
            old.unlink(missing_ok=True)


def get_schema_version(conn: sqlite3.Connection) -> int:
    cur = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='schema_version'"
    )
    if cur.fetchone() is None:
        return 0
    row = conn.execute("SELECT version FROM schema_version").fetchone()
    return row[0] if row else 0


def migrate(db_path: Path) -> int:
    """Runs any pending migrations in order, backing up first. Returns the
    resulting schema version."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    if db_path.exists():
        backup_database(db_path)

    conn = sqlite3.connect(db_path)
    try:
        current = get_schema_version(conn)
        target = CURRENT_SCHEMA_VERSION
        for version in range(current + 1, target + 1):
            script = _MIGRATIONS.get(version)
            if script is None:
                raise RuntimeError(f"No migration defined for version {version}")
            conn.executescript(script)
            if version == 4:
                backfill_weigh_in_plausibility(conn)
            if version == 5:
                decouple_weigh_in_plausibility(conn)
            conn.execute("UPDATE schema_version SET version = ?", (version,))
            conn.commit()
        return get_schema_version(conn)
    finally:
        conn.close()


def open_db(db_path: Path) -> sqlite3.Connection:
    """Ensures the schema is current, then returns a connection."""
    migrate(db_path)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn
