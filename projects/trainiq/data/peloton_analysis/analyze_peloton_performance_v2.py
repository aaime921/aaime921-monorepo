#!/usr/bin/env python3

import csv
import math
from collections import Counter, defaultdict
from datetime import datetime
from statistics import mean, median


CSV_FILE = "aimea75_workouts.csv"


# ================================================================
# HELPERS
# ================================================================

def clean(value):
    return (value or "").strip()


def num(value):
    value = clean(value)
    if not value:
        return None
    value = value.replace("%", "")
    try:
        return float(value)
    except ValueError:
        return None


def parse_date(value):
    value = clean(value)
    if not value:
        return None

    # Remove timezone label because this analysis only needs ordering.
    value = value.replace(" (UTC)", "").replace(" (+01)", "")

    for fmt in (
        "%Y-%m-%d %H:%M",
        "%Y-%m-%d %H:%M:%S",
    ):
        try:
            return datetime.strptime(value, fmt)
        except ValueError:
            pass

    return None


def print_header(title):
    print()
    print("=" * 70)
    print(title)
    print("=" * 70)


def pct_change(first, last):
    if first in (None, 0) or last is None:
        return None
    return (last - first) / first * 100


# ================================================================
# LOAD
# ================================================================

with open(CSV_FILE, newline="", encoding="utf-8-sig") as f:
    rows = list(csv.DictReader(f))

records = []

for r in rows:
    records.append({
        "raw": r,
        "date": parse_date(r["Workout Timestamp"]),
        "title": clean(r["Title"]),
        "type": clean(r["Type"]),
        "discipline": clean(r["Fitness Discipline"]),
        "instructor": clean(r["Instructor Name"]),
        "duration": num(r["Length (minutes)"]),
        "output": num(r["Total Output"]),
        "watts": num(r["Avg. Watts"]),
        "resistance": num(r["Avg. Resistance"]),
        "cadence": num(r["Avg. Cadence (RPM)"]),
        "speed": num(r["Avg. Speed (kph)"]),
        "distance": num(r["Distance (km)"]),
        "calories": num(r["Calories Burned"]),
        "hr": num(r["Avg. Heartrate"]),
    })

records.sort(key=lambda r: r["date"] or datetime.min)

print_header("PELOTON PERFORMANCE ANALYSIS v2")
print(f"Total records: {len(records)}")


# ================================================================
# 1. FTP TIMELINE
# ================================================================

ftp_rows = []

for r in records:
    if "FTP Test" in r["title"] and r["watts"] is not None:
        ftp_rows.append(r)

print_header("FTP TIMELINE")

for r in ftp_rows:
    hr_text = f"{r['hr']:.1f}" if r["hr"] is not None else "n/a"

    print(
        f"{r['date'].strftime('%Y-%m-%d')} | "
        f"{r['watts']:.0f} W | "
        f"Output={r['output']:.0f} kJ | "
        f"HR={hr_text}"
    )

if len(ftp_rows) >= 2:
    first = ftp_rows[0]["watts"]
    last = ftp_rows[-1]["watts"]
    change = last - first
    change_pct = pct_change(first, last)

    print()
    print(
        f"FTP change: {change:+.0f} W "
        f"({change_pct:+.1f}%)"
    )


# ================================================================
# 2. PERFORMANCE BY DURATION
# ================================================================

print_header("POWER-DURATION PROFILE")

duration_groups = defaultdict(list)

for r in records:
    if (
        r["watts"] is not None
        and r["watts"] > 0
        and r["duration"] is not None
    ):
        duration_groups[int(r["duration"])].append(r)

for duration in sorted(duration_groups):
    group = duration_groups[duration]

    # Small groups remain visible but are explicitly marked.
    avg = mean(r["watts"] for r in group)
    med = median(r["watts"] for r in group)
    best = max(r["watts"] for r in group)

    flag = "  [SMALL N]" if len(group) < 3 else ""

    print(
        f"{duration:>3} min | "
        f"n={len(group):>2} | "
        f"avg={avg:>6.1f} W | "
        f"median={med:>6.1f} W | "
        f"best={best:>6.1f} W"
        f"{flag}"
    )


# ================================================================
# 3. POWER ZONE BASELINE BY DURATION
# ================================================================

print_header("POWER ZONE BASELINE BY DURATION")

pz_duration = defaultdict(list)

for r in records:
    if (
        r["type"].lower() == "power zone"
        and r["watts"] is not None
        and r["watts"] > 0
        and r["duration"] is not None
    ):
        pz_duration[int(r["duration"])].append(r)

for duration in sorted(pz_duration):
    group = pz_duration[duration]

    avg = mean(r["watts"] for r in group)
    med = median(r["watts"] for r in group)
    best = max(r["watts"] for r in group)

    print(
        f"{duration:>3} min | "
        f"n={len(group):>2} | "
        f"baseline avg={avg:>6.1f} W | "
        f"median={med:>6.1f} W | "
        f"best={best:>6.1f} W"
    )


# ================================================================
# 4. NORMALIZED PERFORMANCE
#
# For every valid PZ workout:
#   deviation = (watts - duration baseline) / baseline
#
# This is NOT a physiological score.
# It is simply a within-dataset comparison against the user's
# own historical performance at the same duration.
# ================================================================

print_header("NORMALIZED POWER ZONE PERFORMANCE")

baseline = {}

for duration, group in pz_duration.items():
    if len(group) >= 2:
        baseline[duration] = mean(r["watts"] for r in group)

normalized = []

for r in records:
    duration = (
        int(r["duration"])
        if r["duration"] is not None
        else None
    )

    if (
        r["type"].lower() == "power zone"
        and duration in baseline
        and r["watts"] is not None
        and r["watts"] > 0
    ):
        b = baseline[duration]
        deviation = (r["watts"] - b) / b * 100

        normalized.append({
            "record": r,
            "baseline": b,
            "deviation": deviation,
        })

normalized.sort(
    key=lambda x: x["deviation"],
    reverse=True,
)

print("Best relative sessions:")

for x in normalized[:10]:
    r = x["record"]

    print(
        f"{x['deviation']:+6.1f}% | "
        f"{r['date'].strftime('%Y-%m-%d')} | "
        f"{int(r['duration']):>2} min | "
        f"{r['watts']:.0f} W | "
        f"baseline={x['baseline']:.1f} W | "
        f"{r['title']}"
    )

print()
print("Lowest relative sessions:")

for x in normalized[-10:]:
    r = x["record"]

    print(
        f"{x['deviation']:+6.1f}% | "
        f"{r['date'].strftime('%Y-%m-%d')} | "
        f"{int(r['duration']):>2} min | "
        f"{r['watts']:.0f} W | "
        f"baseline={x['baseline']:.1f} W | "
        f"{r['title']}"
    )


# ================================================================
# 5. MONTHLY NORMALIZED PERFORMANCE
# ================================================================

print_header("MONTHLY NORMALIZED POWER ZONE TREND")

monthly = defaultdict(list)

for x in normalized:
    r = x["record"]
    month = r["date"].strftime("%Y-%m")
    monthly[month].append(x["deviation"])

for month in sorted(monthly):
    values = monthly[month]

    print(
        f"{month} | "
        f"n={len(values):>2} | "
        f"avg deviation={mean(values):>+6.1f}% | "
        f"median={median(values):>+6.1f}% | "
        f"best={max(values):>+6.1f}% | "
        f"worst={min(values):>+6.1f}%"
    )


# ================================================================
# 6. RECENT VS HISTORICAL
# ================================================================

print_header("RECENT VS HISTORICAL BASELINE")

if normalized:
    normalized_by_date = sorted(
        normalized,
        key=lambda x: x["record"]["date"]
    )

    cutoff = normalized_by_date[-1]["record"]["date"]

    # Approximate 90-day comparison window.
    recent = [
        x for x in normalized_by_date
        if (cutoff - x["record"]["date"]).days <= 90
    ]

    historical = [
        x for x in normalized_by_date
        if (cutoff - x["record"]["date"]).days > 90
    ]

    if recent:
        print(
            f"Recent window: "
            f"{recent[0]['record']['date'].strftime('%Y-%m-%d')} -> "
            f"{recent[-1]['record']['date'].strftime('%Y-%m-%d')}"
        )
        print(
            f"n={len(recent)} | "
            f"avg deviation={mean(x['deviation'] for x in recent):+.1f}% | "
            f"median={median(x['deviation'] for x in recent):+.1f}%"
        )

    if historical:
        print(
            f"Historical: n={len(historical)} | "
            f"avg deviation={mean(x['deviation'] for x in historical):+.1f}% | "
            f"median={median(x['deviation'] for x in historical):+.1f}%"
        )


# ================================================================
# 7. HEART RATE / POWER
#
# Correctly defined as watts per bpm.
# Only meaningful as a descriptive metric inside comparable
# sessions; no physiological interpretation is made here.
# ================================================================

print_header("POWER / HEART RATE")

hr_records = [
    r for r in records
    if (
        r["type"].lower() == "power zone"
        and r["watts"] is not None
        and r["watts"] > 0
        and r["hr"] is not None
        and r["hr"] > 0
    )
]

ratios = [
    r["watts"] / r["hr"]
    for r in hr_records
]

print(f"PZ records with HR: {len(hr_records)}")

if ratios:
    print(f"Average watts/HR: {mean(ratios):.3f}")
    print(f"Median watts/HR:  {median(ratios):.3f}")

    # More useful: same-duration groups.
    print()
    print("By duration:")

    ratio_groups = defaultdict(list)

    for r in hr_records:
        ratio_groups[int(r["duration"])].append(
            r["watts"] / r["hr"]
        )

    for duration in sorted(ratio_groups):
        values = ratio_groups[duration]

        print(
            f"{duration:>3} min | "
            f"n={len(values):>2} | "
            f"avg={mean(values):.3f} | "
            f"median={median(values):.3f}"
        )


# ================================================================
# 8. DATA QUALITY CLASSIFICATION
# ================================================================

print_header("DATA QUALITY CLASSIFICATION")

classified = []

for r in records:

    metrics = [
        r["output"],
        r["watts"],
        r["resistance"],
        r["cadence"],
        r["speed"],
        r["distance"],
    ]

    all_zero = all(
        x is not None and x == 0
        for x in metrics
    )

    core_missing = any(
        x is None
        for x in metrics
    )

    if all_zero:
        status = "ZERO/FAILED"
    elif core_missing:
        status = "INCOMPLETE"
    else:
        status = "VALID"

    # A separate flag for suspicious immediate retry pattern.
    classified.append((r, status))


counts = Counter(status for _, status in classified)

for status in (
    "VALID",
    "INCOMPLETE",
    "ZERO/FAILED",
):
    print(
        f"{status:<15} "
        f"{counts.get(status, 0):>3}"
    )

print()
print("Records requiring review:")

for r, status in classified:
    if status != "VALID":
        print(
            f"{status:<15} | "
            f"{r['date'].strftime('%Y-%m-%d %H:%M')} | "
            f"{int(r['duration']) if r['duration'] is not None else 'n/a'} min | "
            f"{r['title']}"
        )


# ================================================================
# 9. RETRY / ZERO RECORD DETAILS
# ================================================================

print_header("ZERO / RETRY REVIEW")

zero_records = [
    r for r in records
    if (
        r["output"] == 0
        and r["watts"] == 0
        and r["distance"] == 0
    )
]

for r in zero_records:
    print(
        f"{r['date'].strftime('%Y-%m-%d %H:%M')} | "
        f"{int(r['duration']) if r['duration'] is not None else 'n/a'} min | "
        f"{r['title']}"
    )

    # Look for another workout within ±5 minutes.
    nearby = []

    for other in records:
        if other is r:
            continue

        if other["date"] is None or r["date"] is None:
            continue

        delta = abs(
            (other["date"] - r["date"]).total_seconds()
        )

        if delta <= 300:
            nearby.append(other)

    for other in nearby:
        print(
            f"    nearby: "
            f"{other['date'].strftime('%Y-%m-%d %H:%M')} | "
            f"{int(other['duration']) if other['duration'] is not None else 'n/a'} min | "
            f"{other['watts'] if other['watts'] is not None else 'n/a'} W | "
            f"{other['output'] if other['output'] is not None else 'n/a'} kJ | "
            f"{other['title']}"
        )


# ================================================================
# 10. TRAINIQ SIGNAL SUMMARY
# ================================================================

print_header("TRAINIQ SIGNAL SUMMARY")

print(
    "This section is descriptive only. "
    "It does not assign an athlete state."
)

if len(ftp_rows) >= 2:
    first = ftp_rows[0]["watts"]
    last = ftp_rows[-1]["watts"]

    print(
        f"FTP direction: {first:.0f} W -> {last:.0f} W "
        f"({last-first:+.0f} W)"
    )

if normalized:
    recent_values = [
        x["deviation"]
        for x in normalized
        if (
            cutoff - x["record"]["date"]
        ).days <= 90
    ]

    if recent_values:
        print(
            f"Recent normalized PZ performance: "
            f"{mean(recent_values):+.1f}% vs duration baseline"
        )

print(
    f"Valid records: {counts.get('VALID', 0)}"
)

print(
    f"Incomplete records: {counts.get('INCOMPLETE', 0)}"
)

print(
    f"Zero/failed candidates: {counts.get('ZERO/FAILED', 0)}"
)

print()
print("=" * 70)
print("PERFORMANCE ANALYSIS v2 COMPLETE")
print("=" * 70)
