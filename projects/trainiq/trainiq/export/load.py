"""
trainiq.export.load — load.md/.json (issue #71, AC 5).

CTL (42-day EWMA of daily load) / ATL (7-day EWMA) / TSB (CTL - ATL,
computed from the PRIOR day's CTL/ATL, i.e. the form the athlete woke up
with) are seeded at zero on the day of the first in-scope activity and run
forward one day at a time through `as_of` — not just over the 12-week
reporting window, since EWMA state depends on the athlete's whole history.
A day with no activity contributes a daily load of 0, same as a rest day
really did contribute zero stress; a day with an activity whose load is
genuinely Unknown (missing HR/power baseline) also contributes 0 to the
EWMA, but is counted separately in `activities_without_load` rather than
being silently indistinguishable from an actual rest day (ADR-016).
"""

from __future__ import annotations

import json
import math
import sqlite3
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone

from trainiq.export.data import Activity, load_activities
from trainiq.export.fmt import fmt, md_table

WEEKS = 12
CTL_TAU_DAYS = 42
ATL_TAU_DAYS = 7
_CTL_K = 1 - math.exp(-1 / CTL_TAU_DAYS)
_ATL_K = 1 - math.exp(-1 / ATL_TAU_DAYS)


@dataclass(frozen=True)
class _DayState:
    ctl: float
    atl: float
    tsb: float


def _end_of_day(d: date) -> datetime:
    return datetime.combine(d, datetime.max.time(), tzinfo=timezone.utc)


def _daily_totals(activities: list[Activity]) -> tuple[dict[date, float], dict[date, int]]:
    totals: dict[date, float] = {}
    no_load: dict[date, int] = {}
    for a in activities:
        day = a.start_time.date()
        if a.training_load is None:
            no_load[day] = no_load.get(day, 0) + 1
        else:
            totals[day] = totals.get(day, 0.0) + a.training_load
    return totals, no_load


def _run_ctl_atl(daily_totals: dict[date, float], first_day: date, as_of: date) -> dict[date, _DayState]:
    states: dict[date, _DayState] = {}
    ctl = 0.0
    atl = 0.0
    day = first_day
    while day <= as_of:
        prior_ctl, prior_atl = ctl, atl
        daily_load = daily_totals.get(day, 0.0)
        ctl = prior_ctl + (daily_load - prior_ctl) * _CTL_K
        atl = prior_atl + (daily_load - prior_atl) * _ATL_K
        states[day] = _DayState(ctl=ctl, atl=atl, tsb=prior_ctl - prior_atl)
        day += timedelta(days=1)
    return states


def _iso_week_start(d: date) -> date:
    return d - timedelta(days=d.isoweekday() - 1)


def render(conn: sqlite3.Connection, primary_ids: set[int], as_of: date) -> tuple[str, str]:
    activities = load_activities(conn, primary_ids, since=None, until=_end_of_day(as_of))
    daily_totals, daily_no_load = _daily_totals(activities)

    first_day = min((a.start_time.date() for a in activities), default=as_of)
    states = _run_ctl_atl(daily_totals, first_day, as_of)

    this_week_start = _iso_week_start(as_of)
    week_starts = [this_week_start - timedelta(weeks=i) for i in range(WEEKS - 1, -1, -1)]

    weeks_data = []
    total_no_load = 0
    for week_start in week_starts:
        week_end = min(week_start + timedelta(days=6), as_of)
        week_activities = [a for a in activities if week_start <= a.start_time.date() <= week_end]
        week_load = sum(a.training_load for a in week_activities if a.training_load is not None)
        no_load_count = sum(1 for a in week_activities if a.training_load is None)
        total_no_load += no_load_count
        end_state = states.get(week_end)
        weeks_data.append({
            "week_start": week_start.isoformat(),
            "week_end": week_end.isoformat(),
            "activities": len(week_activities),
            "duration_h": round(sum(a.duration_s for a in week_activities) / 3600, 2),
            "load": round(week_load, 1),
            "ctl": round(end_state.ctl, 1) if end_state else None,
            "atl": round(end_state.atl, 1) if end_state else None,
            "tsb": round(end_state.tsb, 1) if end_state else None,
        })

    headers = ["Week", "Activities", "Duration (h)", "Load", "CTL", "ATL", "TSB"]
    rows = [
        [
            f"{w['week_start']} – {w['week_end']}",
            str(w["activities"]),
            fmt(w["duration_h"], 2),
            fmt(w["load"], 1),
            fmt(w["ctl"], 1),
            fmt(w["atl"], 1),
            fmt(w["tsb"], 1),
        ]
        for w in weeks_data
    ]

    lines = [
        "# Load",
        f"As of: {as_of.isoformat()}",
        "",
        md_table(headers, rows),
        "",
        f"Activities without a computed load (missing HR/power baseline): {total_no_load}.",
        "Load mixes TSS (power-based) and TRIMP (HR-based) depending on what each "
        "activity has data for — comparable as an aggregate trend, not activity-to-activity.",
        "",
        "- CTL (Chronic Training Load, 42-day average): long-term fitness.",
        "- ATL (Acute Training Load, 7-day average): short-term fatigue.",
        "- TSB (Training Stress Balance, CTL minus ATL): form — positive is fresher, negative is more fatigued.",
        "",
    ]
    md = "\n".join(lines)

    payload = {"as_of": as_of.isoformat(), "weeks": weeks_data, "activities_without_load": total_no_load}
    js = json.dumps(payload, sort_keys=True, indent=2)
    return md, js
