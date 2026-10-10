"""
trainiq.export.recent — recent.md/.json (issue #71, AC 3).

Window: the 30 days ending at `as_of` (inclusive on both ends), displayed
newest-first and capped at `MAX_ROWS` for the size target (AC 1).
"""

from __future__ import annotations

import json
import sqlite3
from datetime import date, datetime, timedelta, timezone

from trainiq.export.data import load_activities, sport_of
from trainiq.export.fmt import fmt, fmt_pace, md_table

WINDOW_DAYS = 30
MAX_ROWS = 200


def _start_of_day(d: date) -> datetime:
    return datetime.combine(d, datetime.min.time(), tzinfo=timezone.utc)


def _end_of_day(d: date) -> datetime:
    return datetime.combine(d, datetime.max.time(), tzinfo=timezone.utc)


def render(conn: sqlite3.Connection, primary_ids: set[int], as_of: date) -> tuple[str, str]:
    since = _start_of_day(as_of - timedelta(days=WINDOW_DAYS - 1))
    until = _end_of_day(as_of)
    activities = load_activities(conn, primary_ids, since=since, until=until)
    activities = list(reversed(activities))  # newest first for display

    truncated = max(0, len(activities) - MAX_ROWS)
    shown = activities[:MAX_ROWS]

    headers = [
        "Date", "Sport", "Class title", "Instructor", "Class type",
        "Duration", "Distance (km)", "Avg HR", "Avg power", "Load", "Pace",
    ]
    rows = []
    records = []
    for a in shown:
        sport = sport_of(a)
        distance_km = a.distance_m / 1000 if a.distance_m is not None else None
        pace_s_per_km = a.avg_pace_s_per_km if sport == "run" else None
        rows.append([
            a.start_time.date().isoformat(),
            sport,
            a.activity_title or "-",
            a.instructor_name or "-",
            a.class_type or "-",
            fmt(a.duration_s / 60, 0) + " min",
            fmt(distance_km, 2),
            fmt(a.avg_hr, 0),
            fmt(a.avg_power, 0),
            fmt(a.training_load, 1),
            fmt_pace(pace_s_per_km),
        ])
        records.append({
            "date": a.start_time.date().isoformat(),
            "sport": sport,
            "activity_title": a.activity_title,
            "instructor_name": a.instructor_name,
            "class_type": a.class_type,
            "duration_s": a.duration_s,
            "distance_m": a.distance_m,
            "avg_hr": a.avg_hr,
            "avg_power": a.avg_power,
            "training_load": a.training_load,
            "training_load_method": a.training_load_method,
            "pace_s_per_km": pace_s_per_km,
        })

    lines = ["# Recent (last 30 days)", f"As of: {as_of.isoformat()}", ""]
    lines.append(md_table(headers, rows) if rows else "No activity in the last 30 days.")
    if truncated:
        lines.append("")
        lines.append(f"Truncated: {truncated} older rows omitted.")
    lines.append("")
    md = "\n".join(lines)

    js = json.dumps(
        {"as_of": as_of.isoformat(), "activities": records, "truncated_count": truncated},
        sort_keys=True, indent=2,
    )
    return md, js
