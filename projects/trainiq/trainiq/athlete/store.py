"""
trainiq.athlete.store — Epic 7, slice 2: Athlete Profile persistence

CRUD over the `athlete_profile` singleton table (schema v3, Epic 7 slice
1). Deliberately simple module-level functions, not a class — this is a
single-row table with no lifecycle beyond "load the one row" and "replace
the one row," which doesn't warrant the kind of stateful wrapper
`CredentialStore` needs (that one manages many Keychain items across
providers and credential types; this manages exactly one SQLite row).

Scope note, per the working rules: no wiring into SynchronizationEngine
here — that's the next slice. This module only reads/writes the table
built in slice 1.

AthleteProfile itself remains a pure model (Chief Architect's explicit
instruction after Epic 6) — this module is where the persistence logic
lives instead, never added as methods on the dataclass.
"""

from __future__ import annotations

import sqlite3
from typing import Optional

from trainiq.athlete.profile import AthleteProfile

_COLUMNS = ("sex", "date_of_birth", "resting_hr", "max_hr", "ftp_watts")


def load_athlete_profile(conn: sqlite3.Connection) -> Optional[AthleteProfile]:
    """Returns None only when no profile row has ever been persisted —
    the genuinely "nothing known yet" case (e.g. a fresh install, or
    before initial setup has run). If a row exists but some/all of its
    fields are NULL, this returns a real AthleteProfile with those fields
    set to None, NOT None itself — that distinction matters downstream:
    compute_training_load() gives a more specific reason ("insufficient
    data: missing X") for a partially-filled profile than for a totally
    absent one ("athlete_profile not available"), and collapsing the two
    cases here would lose that precision."""
    row = conn.execute(
        f"SELECT {', '.join(_COLUMNS)} FROM athlete_profile WHERE id = 1"
    ).fetchone()
    if row is None:
        return None
    return AthleteProfile(**dict(zip(_COLUMNS, row)))


def save_athlete_profile(conn: sqlite3.Connection, profile: AthleteProfile) -> None:
    """Replaces the singleton row. Never inserts a second row — the fixed
    id=1 plus INSERT OR REPLACE means this is always an upsert onto the
    one profile the schema's CHECK constraint (slice 1) permits."""
    conn.execute(
        f"""
        INSERT OR REPLACE INTO athlete_profile (id, {', '.join(_COLUMNS)})
        VALUES (1, {', '.join('?' for _ in _COLUMNS)})
        """,
        tuple(getattr(profile, col) for col in _COLUMNS),
    )
    conn.commit()
