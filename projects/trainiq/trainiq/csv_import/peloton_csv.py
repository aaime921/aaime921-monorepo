"""
trainiq.csv_import.peloton_csv — Peloton manual CSV export importer

Approved integration point (repository-audited, not assumed):
`trainiq.normalization.engine.build_canonical_record()` — the same
function every live connector's sync path already uses. This importer
does not invoke SynchronizationEngine at all: that class also owns
checkpoint/lifecycle-state concerns (ADR-008/ADR-010/ADR-038) that do not
apply to a one-time manual CSV import — reusing it would entangle this
importer with machinery it has no business touching. Only
`build_canonical_record()` and the same `INSERT OR IGNORE` + conditional
`UPDATE` persistence pattern already established for live sync
(RC1-HF-006) are reused here, duplicated at the SQL level rather than
imported, for the same reason SynchronizationEngine itself was not reused.

Architectural decisions this importer follows, all closed by the
Architect, none reopened here:
  - source_confidence keeps its existing completeness-ratio semantics —
    Peloton HR/data-quality classification never touches it.
  - dedup_links is NOT written to or read from.
  - No new schema field, no migration.
  - RecordKind.ACTIVITY, existing routing, no shape-sniffing.

Finding surfaced during the mandatory pre-implementation audit, resolved
minimally rather than escalated as a blocker (the fix is a data-format
normalization within THIS importer's own responsibility, not a taxonomy
or schema change): the CSV's "Fitness Discipline" column is Title Case
("Cycling", "Stretching"), but the existing `_PELOTON_MAP` in
`taxonomy.py` expects lowercase keys ("cycling", "stretching") — verified
directly: `map_discipline("peloton", "Cycling")` returns `Discipline.OTHER`
without a case-fold, `Discipline.CYCLING` with one. Resolved by
lowercasing the CSV value before it reaches `map_discipline()` — no
change to `taxonomy.py` itself.

External ID derivation (Decision 3, empirically justified, not assumed):
`Workout Timestamp` alone is already unique across all 120 real CSV rows
(verified directly: 120/120 unique, 0 collisions). `Title` and
`Length (minutes)` are added to the fingerprint anyway, defensively,
since a future export could plausibly contain two workouts in the same
clock-minute — this costs nothing and removes that single point of
fragility. The fingerprint is a SHA-256 hash of these three raw fields,
cleaned and lowercased for determinism, joined by a literal separator.
"""

from __future__ import annotations

import csv
import hashlib
import re
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from trainiq.connectors.base import RecordKind
from trainiq.normalization.engine import build_canonical_record

# ARCH-PEL-003: real timezone conversion, not a suffix strip. zoneinfo is
# stdlib (available per pyproject.toml's requires-python >= 3.11) — no new
# dependency introduced. "Europe/London" correctly resolves BST vs GMT
# based on the actual date (DST-aware), rather than a hardcoded "BST always
# means +01" rule the Architect explicitly ruled out.
_LONDON_TZ = ZoneInfo("Europe/London")


class UnknownTimezoneLabelError(Exception):
    """Raised when a Workout Timestamp's parenthetical suffix is not one
    of the three verified-present labels (UTC, +01, BST). Per ARCH-PEL-003
    Section 5.8: fail explicitly, never silently guess or drop."""

PROVIDER = "peloton_csv"  # distinct from "peloton" (the live-API provider,
# currently blocked per the parallel Discovery investigation) — deliberately
# a different provider string, so CSV-imported and any future live-synced
# Peloton data are NEVER silently merged by UNIQUE(provider, external_id)
# collision. Reconciling the two remains an explicit future decision
# (dedup_links, not activated here, per Decision 2).


@dataclass
class ImportResult:
    records_seen: int = 0
    records_inserted: int = 0
    records_updated: int = 0
    records_skipped_no_identity: int = 0
    records_skipped_missing_required_field: int = 0
    unique_external_ids: int = 0
    collisions: list[tuple[str, str]] = field(default_factory=list)


def _clean(value: str | None) -> str:
    return (value or "").strip()


def _num(value: str | None):
    value = _clean(value)
    if not value:
        return None
    value = value.replace("%", "")
    try:
        return float(value)
    except ValueError:
        return None


def _parse_timestamp(value: str) -> datetime | None:
    """ARCH-PEL-003: real timezone conversion to UTC, timezone-aware,
    always explicit +00:00 offset in the resulting isoformat() string.

    Extracts the parenthetical suffix explicitly (does not just strip it)
    and interprets it per the Architect's verified-real set:
      (UTC) -> already UTC, tzinfo=timezone.utc directly.
      (+01) -> a literal numeric offset, tzinfo=timezone(timedelta(hours=1)),
               then converted to UTC.
      (BST) -> British Summer Time, resolved via zoneinfo's "Europe/London"
               (DST-aware, based on the actual date — NOT a hardcoded
               "BST always means +01" rule, since a fixed offset would be
               wrong for a record incorrectly labeled BST outside the
               real DST window; localizing via zoneinfo lets the
               timezone database itself determine the correct offset for
               that specific date).
    Raises UnknownTimezoneLabelError for anything else — never silently
    guessed, never silently dropped, per ARCH-PEL-003 Section 5.8."""
    value = _clean(value)
    if not value:
        return None

    match = re.match(r"^(.*?)\s*\(([^)]*)\)\s*$", value)
    if match is None:
        raise UnknownTimezoneLabelError(
            f"Workout Timestamp has no recognizable parenthetical timezone label: {value!r}"
        )
    naive_part, label = match.group(1), match.group(2)

    naive_dt = None
    for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%d %H:%M:%S"):
        try:
            naive_dt = datetime.strptime(naive_part, fmt)
            break
        except ValueError:
            pass
    if naive_dt is None:
        return None  # unparseable date/time portion — a different failure mode than an unknown tz label

    if label == "UTC":
        aware_dt = naive_dt.replace(tzinfo=timezone.utc)
    elif label == "+01":
        aware_dt = naive_dt.replace(tzinfo=timezone(timedelta(hours=1)))
    elif label == "BST":
        aware_dt = naive_dt.replace(tzinfo=_LONDON_TZ)
    else:
        raise UnknownTimezoneLabelError(
            f"Unrecognized timezone label {label!r} in Workout Timestamp {value!r} — "
            f"only UTC, +01, BST are supported (ARCH-PEL-003)"
        )

    return aware_dt.astimezone(timezone.utc)


def compute_external_id(row: dict) -> str | None:
    """Deterministic fingerprint from Workout Timestamp + Title +
    Length (minutes) — see module docstring for why these three fields.
    Returns None if the timestamp itself (the one field verified to
    already be unique alone) is missing — a record with no timestamp
    cannot be given a stable identity and is not silently assigned one."""
    timestamp = _clean(row.get("Workout Timestamp"))
    if not timestamp:
        return None
    title = _clean(row.get("Title")).lower()
    length = _clean(row.get("Length (minutes)"))
    fingerprint_input = f"{timestamp.lower()}|{title}|{length}"
    return hashlib.sha256(fingerprint_input.encode("utf-8")).hexdigest()[:32]


def _row_to_normalized(row: dict, external_id: str) -> dict:
    """Builds the dict build_canonical_record() expects for
    RecordKind.ACTIVITY — external_id, start_time, duration_s required;
    everything else optional, per _build_activity_record()'s verified
    contract."""
    start_time = _parse_timestamp(row.get("Workout Timestamp", ""))
    duration_min = _num(row.get("Length (minutes)"))
    discipline_raw = _clean(row.get("Fitness Discipline")).lower() or None  # see module docstring: case-fold fix

    return {
        "external_id": external_id,
        "start_time": start_time.isoformat() if start_time else None,
        "duration_s": int(duration_min * 60) if duration_min is not None else None,
        "discipline_raw": discipline_raw,
        "distance_m": (_num(row.get("Distance (km)")) or 0) * 1000 if row.get("Distance (km)") else None,
        "avg_hr": _num(row.get("Avg. Heartrate")),  # RAW observation, preserved
        # exactly as exported — never nulled here because a value looks
        # implausible; that classification is a separate, analytical
        # concern (v3), not a canonical-storage concern (spec Section 7).
        "max_hr": None,  # not present in this CSV export — genuinely absent, not guessed
        "avg_power": _num(row.get("Avg. Watts")),
        "max_power": None,  # not present in this CSV export
        "calories": _num(row.get("Calories Burned")),
    }


def _upsert_activity(conn: sqlite3.Connection, record: dict) -> str:
    """Same INSERT OR IGNORE + conditional UPDATE pattern already
    established for live sync (RC1-HF-006) — duplicated here rather than
    calling SynchronizationEngine's private method, per this module's own
    docstring on why that class isn't reused."""
    cursor = conn.execute(
        """
        INSERT OR IGNORE INTO normalized_activities
            (provider, external_id, start_time, duration_s, discipline, distance_m,
             avg_hr, max_hr, avg_power, max_power, calories,
             training_load, training_load_method, source_confidence)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            PROVIDER, record["external_id"], record["start_time"], record["duration_s"],
            record["discipline"], record["distance_m"], record["avg_hr"], record["max_hr"],
            record["avg_power"], record["max_power"], record["calories"],
            record["training_load"], record["training_load_method"], record["source_confidence"],
        ),
    )
    if cursor.rowcount == 1:
        return "inserted"

    conn.execute(
        """
        UPDATE normalized_activities SET
            start_time = ?, duration_s = ?, discipline = ?, distance_m = ?,
            avg_hr = ?, max_hr = ?, avg_power = ?, max_power = ?, calories = ?,
            training_load = ?, training_load_method = ?, source_confidence = ?
        WHERE provider = ? AND external_id = ?
        """,
        (
            record["start_time"], record["duration_s"], record["discipline"], record["distance_m"],
            record["avg_hr"], record["max_hr"], record["avg_power"], record["max_power"],
            record["calories"], record["training_load"], record["training_load_method"],
            record["source_confidence"], PROVIDER, record["external_id"],
        ),
    )
    return "updated"


def import_peloton_csv(csv_path: str, conn: sqlite3.Connection) -> ImportResult:
    """Imports a Peloton manual-export CSV into normalized_activities via
    the existing, approved canonical path. Idempotent: re-running against
    the same file produces zero new rows, only updates, per
    UNIQUE(provider, external_id)."""
    result = ImportResult()
    seen_ids: dict[str, str] = {}

    with open(csv_path, newline="", encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))

    result.records_seen = len(rows)

    for row in rows:
        external_id = compute_external_id(row)
        if external_id is None:
            result.records_skipped_no_identity += 1
            continue

        identity_key = f"{_clean(row.get('Workout Timestamp'))}"
        if external_id in seen_ids and seen_ids[external_id] != identity_key:
            result.collisions.append((seen_ids[external_id], identity_key))
        seen_ids[external_id] = identity_key

        normalized = _row_to_normalized(row, external_id)

        # normalized_activities.start_time and .duration_s are NOT NULL
        # (verified in schema.py). No real row in this dataset lacks
        # these (verified empirically), but a future export theoretically
        # could — reported and skipped, not silently defaulted or
        # crashed on, per this spec's stop-and-report discipline.
        if normalized["start_time"] is None or normalized["duration_s"] is None:
            result.records_skipped_missing_required_field += 1
            continue

        canonical = build_canonical_record(PROVIDER, RecordKind.ACTIVITY, normalized, athlete_profile=None)

        outcome = _upsert_activity(conn, canonical)
        if outcome == "inserted":
            result.records_inserted += 1
        else:
            result.records_updated += 1

    conn.commit()
    result.unique_external_ids = len(seen_ids)
    return result
