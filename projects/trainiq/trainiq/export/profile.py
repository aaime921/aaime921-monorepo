"""
trainiq.export.profile — profile.md (issue #71, AC 2).

MD only — the architecture doc's "JSON companions only for recent/load/
weight" leaves this (and last_done/performance) Markdown-only.
"""

from __future__ import annotations

import sqlite3
from datetime import date
from pathlib import Path

from trainiq.athlete.store import load_athlete_profile
from trainiq.config import get_weight_goal
from trainiq.export.fmt import fmt, md_table


def _ftp_history(conn: sqlite3.Connection, as_of: date) -> list[tuple[str, str]]:
    """Date + FTP for every FTP Test ride, oldest first. The schema has no
    column for the resulting FTP value a test ride produced, so the value
    is always "-" today — a verification gap flagged for the BO in the PR,
    never inferred from avg_power."""
    rows = conn.execute(
        "SELECT start_time FROM normalized_activities "
        "WHERE activity_title IS NOT NULL AND LOWER(activity_title) LIKE '%ftp test%' "
        "ORDER BY start_time"
    ).fetchall()
    as_of_str = as_of.isoformat()
    history = []
    for row in rows:
        day = row["start_time"][:10]
        if day <= as_of_str:
            history.append((day, "-"))
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
    history = _ftp_history(conn, as_of)
    if history:
        lines.append(md_table(["Date", "FTP (W)"], [[d, v] for d, v in history]))
    else:
        lines.append("No FTP Test rides found.")
    lines.append("")

    return "\n".join(lines)
