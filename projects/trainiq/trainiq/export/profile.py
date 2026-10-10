"""
trainiq.export.profile — profile.md (issue #71, AC 2).

MD only — the architecture doc's "JSON companions only for recent/load/
weight" leaves this (and last_done/performance) Markdown-only.
"""

from __future__ import annotations

import sqlite3
from datetime import date
from pathlib import Path
from typing import Optional

from trainiq.athlete.store import load_athlete_profile
from trainiq.config import get_weight_goal
from trainiq.export.data import parse_timestamp
from trainiq.export.fmt import fmt, md_table

# Below this, an "FTP Test" ride is a warm-up or an aborted attempt, not a
# real test (BO, issue #71 rework) — excluded from the estimate entirely,
# not shown with a blank value.
_FTP_TEST_MIN_DURATION_S = 15 * 60


def _ftp_history(
    conn: sqlite3.Connection, as_of: date, primary_ids: set[int]
) -> list[list[str]]:
    """Date, avg power and estimated FTP (= round(0.95 * avg power), the
    standard 20-min-test estimate) for every real FTP Test ride, oldest
    first, one row per date — filtered to `primary_ids` so a linked
    Peloton/Strava pair of the same test doesn't produce a duplicate row
    for the Strava copy (no power data on that side) (issue #79). If two
    primary rows still share a date, the one with the larger avg_power
    wins (non-NULL over NULL). The row(s) with the latest date and the
    row with the highest estimate are marked "latest"/"best" — one row
    may carry both; rows with no estimate (no avg_power) are not shown,
    and there is no "current" tag (the profile FTP is shown separately,
    see `render`)."""
    rows = conn.execute(
        "SELECT id, start_time, duration_s, moving_time_s, avg_power FROM normalized_activities "
        "WHERE activity_title IS NOT NULL AND LOWER(activity_title) LIKE '%ftp test%' "
        "ORDER BY start_time"
    ).fetchall()
    as_of_str = as_of.isoformat()

    best_power_by_day: dict[str, Optional[int]] = {}
    for row in rows:
        if row["id"] not in primary_ids:
            continue
        duration_s = row["moving_time_s"] if row["moving_time_s"] is not None else row["duration_s"]
        if duration_s is None or duration_s < _FTP_TEST_MIN_DURATION_S:
            continue
        day = parse_timestamp(row["start_time"]).date().isoformat()
        if day > as_of_str:
            continue
        avg_power = row["avg_power"]
        current_best = best_power_by_day.get(day)
        if day not in best_power_by_day or (
            avg_power is not None and (current_best is None or avg_power > current_best)
        ):
            best_power_by_day[day] = avg_power

    entries: list[tuple[str, Optional[int], int]] = []
    for day, avg_power in sorted(best_power_by_day.items()):
        if avg_power is None:
            continue
        entries.append((day, avg_power, round(avg_power * 0.95)))
    if not entries:
        return []

    latest_day = entries[-1][0]
    best_day = max(entries, key=lambda e: (e[2], e[0]))[0]

    history = []
    for day, avg_power, estimate in entries:
        tags = []
        if day == latest_day:
            tags.append("latest")
        if day == best_day:
            tags.append("best")
        history.append([day, fmt(avg_power), fmt(estimate), ", ".join(tags)])
    return history


def render(conn: sqlite3.Connection, config_path: Path, as_of: date, primary_ids: set[int]) -> str:
    profile = load_athlete_profile(conn)
    goal = get_weight_goal(config_path)

    lines = ["# Profile", f"As of: {as_of.isoformat()}", "", "## Athlete"]
    if profile is None:
        lines.append("No athlete profile stored.")
    else:
        lines.append(f"- Sex: {fmt(profile.sex)}")
        lines.append(f"- Date of birth: {fmt(profile.date_of_birth)}")
        lines.append(f"- FTP: {fmt(profile.ftp_watts)} W")
        lines.append(f"- Resting HR: {fmt(profile.resting_hr)} bpm")
        lines.append(f"- Max HR: {fmt(profile.max_hr)} bpm")

    lines += ["", "## Weight goal"]
    if goal is None:
        lines.append("Goal: not configured.")
    else:
        lines.append(
            f"- Start: {fmt(goal.start_weight_kg)} kg, "
            f"Goal: {fmt(goal.goal_weight_kg)} kg "
            f"(-{fmt(goal.start_weight_kg - goal.goal_weight_kg)} kg)."
        )

    lines += ["", "## FTP history"]
    history = _ftp_history(conn, as_of, primary_ids)
    if history:
        lines.append(md_table(["Date", "Avg power (W)", "Est. FTP (W)", "Note"], history))
    else:
        lines.append("No qualifying FTP Test rides found.")
    lines.append("")

    current_ftp = profile.ftp_watts if profile is not None else None
    if current_ftp is None:
        lines.append("Current FTP (profile): -")
    else:
        lines.append(f"Current FTP (profile): {current_ftp} W")
    lines.append("")

    return "\n".join(lines)
