"""
trainiq.export.weight — weight.md/.json (issue #71, AC 6).

Weekly averages from Eufy weigh-ins over the 26 ISO weeks ending at
`as_of`'s week, excluding flagged readings (`load_weigh_ins` already
applies that filter). Rate of change is the least-squares slope (kg/week)
over whichever weeks have at least one reading — never interpolated for
weeks with none.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from trainiq.config import get_weight_goal
from trainiq.export.data import load_weigh_ins
from trainiq.export.fmt import fmt, md_table

WEEKS = 26


def _iso_week_start(d: date) -> date:
    return d - timedelta(days=d.isoweekday() - 1)


def _end_of_day(d: date) -> datetime:
    return datetime.combine(d, datetime.max.time(), tzinfo=timezone.utc)


def _least_squares_slope(xs: list[float], ys: list[float]) -> Optional[float]:
    """kg/week trend over the weeks that have data; `None` with fewer than
    two points — a slope needs at least two, never invented from one."""
    n = len(xs)
    if n < 2:
        return None
    mean_x = sum(xs) / n
    mean_y = sum(ys) / n
    numerator = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys))
    denominator = sum((x - mean_x) ** 2 for x in xs)
    if denominator == 0:
        return None
    return numerator / denominator


def render(conn: sqlite3.Connection, config_path: Path, as_of: date) -> tuple[str, str]:
    this_week_start = _iso_week_start(as_of)
    week_starts = [this_week_start - timedelta(weeks=i) for i in range(WEEKS - 1, -1, -1)]
    window_start = week_starts[0]

    weigh_ins = load_weigh_ins(
        conn,
        since=datetime.combine(window_start, datetime.min.time(), tzinfo=timezone.utc),
        until=_end_of_day(as_of),
    )

    weeks_data = []
    for week_start in week_starts:
        week_end = min(week_start + timedelta(days=6), as_of)
        readings = [
            w.weight_kg for w in weigh_ins
            if week_start <= w.timestamp.date() <= week_end and w.weight_kg is not None
        ]
        avg = round(sum(readings) / len(readings), 2) if readings else None
        weeks_data.append({
            "week_start": week_start.isoformat(),
            "week_end": week_end.isoformat(),
            "n_readings": len(readings),
            "avg_weight_kg": avg,
        })

    weeks_with_data = [(i, w["avg_weight_kg"]) for i, w in enumerate(weeks_data) if w["avg_weight_kg"] is not None]
    rate_kg_per_week = None
    if len(weeks_with_data) >= 2:
        xs = [float(i) for i, _ in weeks_with_data]
        ys = [v for _, v in weeks_with_data]
        rate_kg_per_week = _least_squares_slope(xs, ys)
    latest_avg = weeks_with_data[-1][1] if weeks_with_data else None

    goal = get_weight_goal(config_path)
    progress_lines = []
    if goal is None:
        progress_lines.append("Goal: not configured.")
    else:
        progress_lines.append(
            f"Goal: {fmt(goal.start_weight_kg)} kg -> {fmt(goal.goal_weight_kg)} kg "
            f"(-{fmt(goal.start_weight_kg - goal.goal_weight_kg)} kg)."
        )
        if latest_avg is not None:
            kg_lost = goal.start_weight_kg - latest_avg
            kg_remaining = latest_avg - goal.goal_weight_kg
            progress_lines.append(f"Progress: {fmt(kg_lost)} kg lost, {fmt(kg_remaining)} kg remaining.")

    headers = ["Week", "Readings", "Avg weight (kg)"]
    rows = [
        [f"{w['week_start']} – {w['week_end']}", str(w["n_readings"]), fmt(w["avg_weight_kg"], 2)]
        for w in weeks_data
    ]

    lines = ["# Weight", f"As of: {as_of.isoformat()}", "", md_table(headers, rows), ""]
    lines.append(
        f"Rate of change: {fmt(rate_kg_per_week, 3)} kg/week."
        if rate_kg_per_week is not None
        else "Rate of change: not enough data."
    )
    lines += progress_lines
    lines.append("")
    md = "\n".join(lines)

    payload = {
        "as_of": as_of.isoformat(),
        "weeks": weeks_data,
        "rate_kg_per_week": round(rate_kg_per_week, 3) if rate_kg_per_week is not None else None,
        "goal": None if goal is None else {
            "start_weight_kg": goal.start_weight_kg, "goal_weight_kg": goal.goal_weight_kg,
        },
        "latest_avg_weight_kg": latest_avg,
    }
    js = json.dumps(payload, sort_keys=True, indent=2)
    return md, js
