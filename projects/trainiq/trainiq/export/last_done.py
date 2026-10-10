"""
trainiq.export.last_done — last_done.md (issue #71, AC 4).

Three groupings, each keyed on the most recent activity matching that key:
Peloton class type, instructor, and the three named sports (run/walk/ride).
"""

from __future__ import annotations

import sqlite3
from datetime import date
from typing import Callable, Optional

from trainiq.export.data import Activity, effective_duration_s, load_activities, sport_of
from trainiq.export.fmt import fmt, md_table

_SPORTS_IN_SCOPE = ("run", "walk", "ride")
MAX_INSTRUCTOR_ROWS = 60

_HEADERS = ["Last done", "Date", "Duration", "Distance (km)", "Avg HR", "Avg power", "Load"]


def _latest_per_key(activities: list[Activity], key_fn: Callable[[Activity], Optional[str]]) -> dict[str, Activity]:
    latest: dict[str, Activity] = {}
    for a in activities:
        key = key_fn(a)
        if key is None:
            continue
        current = latest.get(key)
        if current is None or a.start_time > current.start_time:
            latest[key] = a
    return latest


def _row(label: str, a: Optional[Activity]) -> list[str]:
    if a is None:
        return [label, "-", "-", "-", "-", "-", "-"]
    distance_km = a.distance_m / 1000 if a.distance_m is not None else None
    return [
        label,
        a.start_time.date().isoformat(),
        fmt(effective_duration_s(a) / 60, 0) + " min",
        fmt(distance_km, 2),
        fmt(a.avg_hr, 0),
        fmt(a.avg_power, 0),
        fmt(a.training_load, 1),
    ]


def render(conn: sqlite3.Connection, primary_ids: set[int], as_of: date) -> str:
    activities = load_activities(conn, primary_ids)
    activities = [a for a in activities if a.start_time.date() <= as_of]

    by_sport = _latest_per_key(activities, lambda a: s if (s := sport_of(a)) in _SPORTS_IN_SCOPE else None)
    by_class_type = _latest_per_key(activities, lambda a: a.class_type)
    by_instructor = _latest_per_key(activities, lambda a: a.instructor_name)

    lines = ["# Last done", f"As of: {as_of.isoformat()}", ""]

    lines.append("## By sport")
    sport_rows = [_row(f"sport: {sport}", by_sport.get(sport)) for sport in _SPORTS_IN_SCOPE]
    lines.append(md_table(_HEADERS, sport_rows))
    lines.append("")

    lines.append("## By Peloton class type")
    class_type_items = sorted(by_class_type.items(), key=lambda kv: kv[1].start_time, reverse=True)
    if class_type_items:
        lines.append(md_table(_HEADERS, [_row(f"class: {ct}", a) for ct, a in class_type_items]))
    else:
        lines.append("No class-type data.")
    lines.append("")

    lines.append("## By instructor")
    instructor_items = sorted(by_instructor.items(), key=lambda kv: kv[1].start_time, reverse=True)
    truncated = max(0, len(instructor_items) - MAX_INSTRUCTOR_ROWS)
    instructor_items = instructor_items[:MAX_INSTRUCTOR_ROWS]
    if instructor_items:
        lines.append(md_table(_HEADERS, [_row(f"instructor: {name}", a) for name, a in instructor_items]))
    else:
        lines.append("No instructor data.")
    if truncated:
        lines.append("")
        lines.append(f"Truncated: {truncated} older instructor rows omitted.")
    lines.append("")

    return "\n".join(lines)
