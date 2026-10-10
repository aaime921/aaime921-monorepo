"""
trainiq.export.performance — performance.md (issue #71, AC 7).

"Best 20-min power" is an avg-power proxy, not a true 20-minute peak power
(connectors report activity-level averages, no power stream) — labeled
as such in the output rather than overclaiming precision the data doesn't
support (ADR-028).
"""

from __future__ import annotations

import sqlite3
from datetime import date

from trainiq.export.data import effective_duration_s, load_activities, sport_of
from trainiq.export.fmt import fmt, fmt_pace, md_table

TWENTY_MIN_S = 1200


def render(conn: sqlite3.Connection, primary_ids: set[int], as_of: date) -> str:
    activities = load_activities(conn, primary_ids)
    activities = [a for a in activities if a.start_time.date() <= as_of]

    cycling_candidates = [
        a for a in activities
        if a.discipline == "cycling" and effective_duration_s(a) >= TWENTY_MIN_S and a.avg_power is not None
    ]
    best_20min = max(cycling_candidates, key=lambda a: a.avg_power, default=None)

    runs = [a for a in activities if sport_of(a) == "run"]
    walks = [a for a in activities if sport_of(a) == "walk"]
    longest_run = max(runs, key=lambda a: a.distance_m or 0, default=None)
    longest_walk = max(walks, key=lambda a: a.distance_m or 0, default=None)

    monthly_pace: dict[str, list[float]] = {}
    for a in runs:
        if a.avg_pace_s_per_km is None:
            continue
        monthly_pace.setdefault(a.start_time.strftime("%Y-%m"), []).append(a.avg_pace_s_per_km)
    pace_rows = [
        [month, fmt_pace(sum(paces) / len(paces))]
        for month, paces in sorted(monthly_pace.items())
    ]

    lines = ["# Performance", f"As of: {as_of.isoformat()}", "", "## Personal bests"]

    if best_20min is not None:
        lines.append(
            f"- Best 20-min power (avg-power proxy, not a true 20-min peak): "
            f"{fmt(best_20min.avg_power, 0)} W on {best_20min.start_time.date().isoformat()}."
        )
    else:
        lines.append("- Best 20-min power: -")

    if longest_run is not None:
        lines.append(
            f"- Longest run: {fmt((longest_run.distance_m or 0) / 1000, 2)} km "
            f"on {longest_run.start_time.date().isoformat()}."
        )
    else:
        lines.append("- Longest run: -")

    if longest_walk is not None:
        lines.append(
            f"- Longest walk: {fmt((longest_walk.distance_m or 0) / 1000, 2)} km "
            f"on {longest_walk.start_time.date().isoformat()}."
        )
    else:
        lines.append("- Longest walk: -")

    lines += ["", "## Run pace trend (monthly average)"]
    lines.append(md_table(["Month", "Avg pace"], pace_rows) if pace_rows else "No run pace data.")
    lines.append("")

    return "\n".join(lines)
