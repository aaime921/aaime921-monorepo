"""
trainiq.export.data — shared data access for the coach export (issue #71).

Every function here is pure read-only SQL plus Python filtering/sorting —
no rendering, no file I/O. `load_activities` takes the caller's own
`primary_ids` (from `trainiq.dedup.detector.primary_activity_ids()`) rather
than computing it itself, so a single export run only pays for that query
once (see `trainiq.export.run_export`) even though every renderer calls in
here.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Optional


@dataclass(frozen=True)
class Activity:
    id: int
    provider: str
    start_time: datetime  # always UTC-aware
    duration_s: int
    moving_time_s: Optional[int]
    discipline: str
    distance_m: Optional[float]
    avg_hr: Optional[int]
    avg_power: Optional[int]
    training_load: Optional[float]
    training_load_method: str
    activity_title: Optional[str]
    instructor_name: Optional[str]
    class_type: Optional[str]
    sport_type_raw: Optional[str]
    avg_pace_s_per_km: Optional[float]


_ACTIVITY_COLUMNS = (
    "id", "provider", "start_time", "duration_s", "moving_time_s", "discipline",
    "distance_m", "avg_hr", "avg_power", "training_load", "training_load_method",
    "activity_title", "instructor_name", "class_type", "sport_type_raw", "avg_pace_s_per_km",
)


@dataclass(frozen=True)
class WeighIn:
    id: int
    timestamp: datetime  # always UTC-aware
    weight_kg: Optional[float]


def _parse_iso(raw: str) -> datetime:
    """`normalized_activities.start_time`/`weigh_ins.timestamp` are stored
    as ISO-8601 strings; some are naive (treated as UTC, matching how
    they're produced — see connectors' normalize()), some already
    tz-aware. Always returns a UTC-aware datetime, so callers can compare
    across providers without a separate naive/aware branch each time."""
    parsed = datetime.fromisoformat(raw)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def load_activities(
    conn: sqlite3.Connection,
    primary_ids: set[int],
    since: Optional[datetime] = None,
    until: Optional[datetime] = None,
) -> list[Activity]:
    """Every `normalized_activities` row whose id is in `primary_ids` —
    dedup already applied by the caller, this function does not re-derive
    it — optionally windowed to `[since, until]` inclusive. Sorted by
    `(start_time, id)` so output ordering is deterministic (AC 11/12)."""
    rows = conn.execute(
        f"SELECT {', '.join(_ACTIVITY_COLUMNS)} FROM normalized_activities"
    ).fetchall()

    activities = []
    for row in rows:
        if row["id"] not in primary_ids:
            continue
        start_time = _parse_iso(row["start_time"])
        if since is not None and start_time < since:
            continue
        if until is not None and start_time > until:
            continue
        activities.append(
            Activity(
                id=row["id"],
                provider=row["provider"],
                start_time=start_time,
                duration_s=row["duration_s"],
                moving_time_s=row["moving_time_s"],
                discipline=row["discipline"],
                distance_m=row["distance_m"],
                avg_hr=row["avg_hr"],
                avg_power=row["avg_power"],
                training_load=row["training_load"],
                training_load_method=row["training_load_method"],
                activity_title=row["activity_title"],
                instructor_name=row["instructor_name"],
                class_type=row["class_type"],
                sport_type_raw=row["sport_type_raw"],
                avg_pace_s_per_km=row["avg_pace_s_per_km"],
            )
        )

    activities.sort(key=lambda a: (a.start_time, a.id))
    return activities


def load_weigh_ins(
    conn: sqlite3.Connection,
    since: Optional[datetime] = None,
    until: Optional[datetime] = None,
) -> list[WeighIn]:
    """Excludes any reading flagged implausible on the weight axis unless
    the BO has since confirmed it valid (ADR-039) — the same rule the
    normalization layer applies, re-stated here because this is a direct
    read of `weigh_ins`, not a pass through `build_canonical_record()`."""
    rows = conn.execute(
        "SELECT id, timestamp, weight_kg FROM weigh_ins "
        "WHERE is_weight_flagged_implausible = 0 OR bo_confirmed_valid = 1"
    ).fetchall()

    weigh_ins = []
    for row in rows:
        timestamp = _parse_iso(row["timestamp"])
        if since is not None and timestamp < since:
            continue
        if until is not None and timestamp > until:
            continue
        weigh_ins.append(WeighIn(id=row["id"], timestamp=timestamp, weight_kg=row["weight_kg"]))

    weigh_ins.sort(key=lambda w: (w.timestamp, w.id))
    return weigh_ins


def resolve_as_of(conn: sqlite3.Connection, override: Optional[date]) -> date:
    """`--as-of` override, else the latest date of any activity or
    (unflagged) weigh-in — never wall-clock "now" (AC 11: same DB -> same
    files). Only falls back to today's UTC date when the DB has neither —
    an edge case a real database never hits, but `run_export` must still
    resolve to something rather than crash on a brand-new install."""
    if override is not None:
        return override

    latest_activity = conn.execute(
        "SELECT MAX(start_time) AS m FROM normalized_activities"
    ).fetchone()["m"]
    latest_weigh_in = conn.execute(
        "SELECT MAX(timestamp) AS m FROM weigh_ins "
        "WHERE is_weight_flagged_implausible = 0 OR bo_confirmed_valid = 1"
    ).fetchone()["m"]

    candidates = [_parse_iso(c) for c in (latest_activity, latest_weigh_in) if c]
    if not candidates:
        return datetime.now(timezone.utc).date()
    return max(candidates).date()


def sport_of(activity: Activity) -> str:
    """run / walk / ride / other. Ride and run come straight from the
    canonical `discipline`; walk has no discipline of its own (Strava
    files it under `other`), so it's detected from `sport_type_raw`
    (Strava's raw "Walk") or a Peloton walking class_type. Unrecognized
    `other` activities (yoga, strength, meditation, ...) fall back to
    "other", never guessed into one of the three named sports."""
    if activity.discipline == "cycling":
        return "ride"
    if activity.discipline == "running":
        return "run"
    if activity.discipline == "other":
        if activity.sport_type_raw == "Walk":
            return "walk"
        if activity.class_type and "walk" in activity.class_type.lower():
            return "walk"
    return "other"
