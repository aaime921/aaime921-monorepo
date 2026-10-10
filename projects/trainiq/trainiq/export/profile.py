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
    conn: sqlite3.Connection, as_of: date, current_ftp: Optional[int]
) -> list[list[str]]:
    """Date, avg power and estimated FTP (= round(0.95 * avg power), the
    standard 20-min-test estimate) for every real FTP Test ride, oldest
    first. Marks the row(s) matching the profile's current FTP and the
    row with the highest estimate as "current"/"best" (BO, issue #71
    rework) — this is what closes QA's "FTP value column is always -"
    gap; the schema still has no stored FTP-test-result column, so the
    value is derived from avg_power rather than read back."""
    rows = conn.execute(
        "SELECT start_time, duration_s, avg_power FROM normalized_activities "
        "WHERE activity_title IS NOT NULL AND LOWER(activity_title) LIKE '%ftp test%' "
        "ORDER BY start_time"
    ).fetchall()
    as_of_str = as_of.isoformat()

    entries: list[tuple[str, Optional[int], Optional[int]]] = []
    for row in rows:
        if row["duration_s"] is None or row["duration_s"] < _FTP_TEST_MIN_DURATION_S:
            continue
        day = parse_timestamp(row["start_time"]).date().isoformat()
        if day > as_of_str:
            continue
        avg_power = row["avg_power"]
        estimate = round(avg_power * 0.95) if avg_power is not None else None
        entries.append((day, avg_power, estimate))

    estimates = [e for _, _, e in entries if e is not None]
    best = max(estimates) if estimates else None

    history = []
    for day, avg_power, estimate in entries:
        tags = []
        if estimate is not None and estimate == best:
            tags.append("best")
        if estimate is not None and current_ftp is not None and estimate == current_ftp:
            tags.append("current")
        history.append([day, fmt(avg_power), fmt(estimate), ", ".join(tags)])
    return history


def render(conn: sqlite3.Connection, config_path: Path, as_of: date) -> str:
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
    current_ftp = profile.ftp_watts if profile is not None else None
    history = _ftp_history(conn, as_of, current_ftp)
    if history:
        lines.append(md_table(["Date", "Avg power (W)", "Est. FTP (W)", "Note"], history))
    else:
        lines.append("No qualifying FTP Test rides found.")
    lines.append("")

    return "\n".join(lines)
