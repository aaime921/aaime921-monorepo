#!/usr/bin/env python3

import csv
import math
from collections import defaultdict
from datetime import datetime


CSV_FILE = "aimea75_workouts.csv"


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

    # Peloton exports may contain timezone labels.
    for fmt in (
        "%Y-%m-%d %H:%M:%S (%z)",
        "%Y-%m-%d %H:%M (%z)",
    ):
        try:
            return datetime.strptime(value, fmt)
        except ValueError:
            pass

    # Fallback: remove timezone suffix.
    value = value.split(" (")[0]

    for fmt in (
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d %H:%M",
    ):
        try:
            return datetime.strptime(value, fmt)
        except ValueError:
            pass

    return None


def mean(values):
    values = [v for v in values if v is not None]
    return sum(values) / len(values) if values else None


def median(values):
    values = sorted(v for v in values if v is not None)
    if not values:
        return None

    n = len(values)
    mid = n // 2

    if n % 2:
        return values[mid]

    return (values[mid - 1] + values[mid]) / 2


def pct_change(first, last):
    if first in (None, 0) or last is None:
        return None
    return (last - first) / first * 100


def print_header(title):
    print("\n" + "=" * 70)
    print(title)
    print("=" * 70)


# ------------------------------------------------------------------
# LOAD
# ------------------------------------------------------------------

with open(CSV_FILE, newline="", encoding="utf-8-sig") as f:
    rows = list(csv.DictReader(f))


print_header("PELOTON PERFORMANCE ANALYSIS")

print(f"Total records: {len(rows)}")


# ------------------------------------------------------------------
# NORMALIZE
# ------------------------------------------------------------------

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


# ------------------------------------------------------------------
# OVERVIEW
# ------------------------------------------------------------------

print_header("OVERVIEW")

valid = [r for r in records if r["date"] is not None]

if valid:
    first = valid[0]["date"]
    last = valid[-1]["date"]
    days = (last.date() - first.date()).days

    print(f"First workout: {first}")
    print(f"Last workout:  {last}")
    print(f"Days covered:  {days}")

durations = [r["duration"] for r in records if r["duration"] is not None]

print(f"Total minutes: {sum(durations):.0f}")
print(f"Average workout duration: {mean(durations):.1f} min")
print(f"Median workout duration: {median(durations):.1f} min")


# ------------------------------------------------------------------
# POWER ZONE
# ------------------------------------------------------------------

print_header("POWER ZONE PERFORMANCE")

pz = [
    r for r in records
    if r["type"] == "Power Zone"
    and r["watts"] is not None
    and r["watts"] > 0
]

print(f"Power Zone records: {len(pz)}")

watts = [r["watts"] for r in pz]
outputs = [r["output"] for r in pz if r["output"] is not None]

if watts:
    print(f"Average watts: {mean(watts):.1f}")
    print(f"Median watts:  {median(watts):.1f}")
    print(f"Min watts:     {min(watts):.0f}")
    print(f"Max watts:     {max(watts):.0f}")

if outputs:
    print(f"Total output:  {sum(outputs):.0f} kJ")
    print(f"Average output: {mean(outputs):.1f} kJ")


# ------------------------------------------------------------------
# POWER ZONE BY DURATION
# ------------------------------------------------------------------

print_header("POWER ZONE BY DURATION")

duration_groups = defaultdict(list)

for r in pz:
    duration = r["duration"]

    if duration is not None:
        duration_groups[int(duration)].append(r["watts"])

for duration in sorted(duration_groups):
    values = duration_groups[duration]

    print(
        f"{duration:>3} min | "
        f"n={len(values):>2} | "
        f"avg={mean(values):6.1f} W | "
        f"median={median(values):6.1f} W | "
        f"best={max(values):6.1f} W"
    )


# ------------------------------------------------------------------
# FTP TESTS
# ------------------------------------------------------------------

print_header("FTP TEST PROGRESSION")

ftp_rows = [
    r for r in records
    if "FTP Test" in r["title"]
    and r["watts"] is not None
    and r["watts"] > 0
]

for r in ftp_rows:
    if r["hr"] is not None:
        hr_text = f"{r['hr']:.2f}"
    else:
        hr_text = "n/a"

    output_text = (
        f"{r['output']:.0f} kJ"
        if r["output"] is not None
        else "n/a"
    )

    print(
        f"{r['date'].strftime('%Y-%m-%d')} | "
        f"{r['watts']:.0f} W | "
        f"Output={output_text} | "
        f"HR={hr_text}"
    )

if len(ftp_rows) >= 2:
    first_ftp = ftp_rows[0]["watts"]
    last_ftp = ftp_rows[-1]["watts"]

    change = last_ftp - first_ftp
    percentage = pct_change(first_ftp, last_ftp)

    print()
    print(
        f"FTP change: {change:+.0f} W "
        f"({percentage:+.1f}%)"
    )


# ------------------------------------------------------------------
# TOP PERFORMANCE
# ------------------------------------------------------------------

print_header("TOP AVG WATTS")

performance = [
    r for r in records
    if r["watts"] is not None
    and r["watts"] > 0
]

performance.sort(
    key=lambda r: r["watts"],
    reverse=True,
)

for r in performance[:15]:
    print(
        f"{r['watts']:>4.0f} W | "
        f"{r['duration']:>2.0f} min | "
        f"{r['date'].strftime('%Y-%m-%d')} | "
        f"{r['title']}"
    )


# ------------------------------------------------------------------
# TOP OUTPUT
# ------------------------------------------------------------------

print_header("TOP TOTAL OUTPUT")

outputs_sorted = [
    r for r in records
    if r["output"] is not None
    and r["output"] > 0
]

outputs_sorted.sort(
    key=lambda r: r["output"],
    reverse=True,
)

for r in outputs_sorted[:15]:
    print(
        f"{r['output']:>4.0f} kJ | "
        f"{r['duration']:>2.0f} min | "
        f"{r['date'].strftime('%Y-%m-%d')} | "
        f"{r['title']}"
    )


# ------------------------------------------------------------------
# RECENT PERFORMANCE
# ------------------------------------------------------------------

print_header("RECENT POWER ZONE PERFORMANCE")

recent_pz = [
    r for r in pz
    if r["date"] is not None
]

recent_pz.sort(
    key=lambda r: r["date"],
    reverse=True,
)

for r in recent_pz[:15]:
    print(
        f"{r['date'].strftime('%Y-%m-%d')} | "
        f"{r['duration']:>2.0f} min | "
        f"{r['watts']:>3.0f} W | "
        f"{r['output']:>3.0f} kJ | "
        f"HR={r['hr']:.1f}" if r["hr"] is not None
        else
        f"{r['date'].strftime('%Y-%m-%d')} | "
        f"{r['duration']:>2.0f} min | "
        f"{r['watts']:>3.0f} W | "
        f"{r['output']:>3.0f} kJ | HR=n/a"
    )


# ------------------------------------------------------------------
# HEART RATE / POWER
# ------------------------------------------------------------------

print_header("POWER / HEART RATE")

hr_records = [
    r for r in pz
    if r["hr"] is not None
    and r["hr"] > 0
    and r["watts"] is not None
    and r["watts"] > 0
]

print(f"Power Zone records with HR: {len(hr_records)}")

if hr_records:
    ratios = [
        r["watts"] / r["hr"]
        for r in hr_records
    ]

    print(
        f"Average watts/HR: {mean(ratios):.3f}"
    )

    print(
        f"Median watts/HR:  {median(ratios):.3f}"
    )


# ------------------------------------------------------------------
# MONTHLY TREND
# ------------------------------------------------------------------

print_header("MONTHLY POWER ZONE TREND")

monthly = defaultdict(list)

for r in pz:
    if r["date"] is not None:
        key = r["date"].strftime("%Y-%m")
        monthly[key].append(r)


for month in sorted(monthly):
    values = monthly[month]

    print(
        f"{month} | "
        f"n={len(values):>2} | "
        f"avg={mean([r['watts'] for r in values]):6.1f} W | "
        f"best={max(r['watts'] for r in values):6.1f} W"
    )


# ------------------------------------------------------------------
# ZERO / INVALID RECORDS
# ------------------------------------------------------------------

print_header("ZERO / INVALID RECORDS")

zero_records = []

for r in records:
    output = r["output"]
    watts = r["watts"]
    distance = r["distance"]

    if (
        output == 0
        and watts == 0
        and distance == 0
    ):
        zero_records.append(r)

print(f"Records with all-zero performance: {len(zero_records)}")

for r in zero_records:
    print(
        f"{r['date']} | "
        f"{r['duration']:.0f} min | "
        f"{r['title']}"
    )


# ------------------------------------------------------------------
# DATA QUALITY
# ------------------------------------------------------------------

print_header("DATA QUALITY")

fields = [
    ("Total Output", "output"),
    ("Avg. Watts", "watts"),
    ("Avg. Resistance", "resistance"),
    ("Avg. Cadence", "cadence"),
    ("Avg. Speed", "speed"),
    ("Distance", "distance"),
    ("Calories", "calories"),
    ("Avg. Heartrate", "hr"),
]

for label, key in fields:
    missing = sum(
        1 for r in records
        if r[key] is None
    )

    print(
        f"{label:<20} "
        f"{missing:>3}/{len(records)} missing"
    )


# ------------------------------------------------------------------
# KEY FINDINGS
# ------------------------------------------------------------------

print_header("KEY FINDINGS")

if ftp_rows:
    print(
        f"FTP progression: "
        f"{ftp_rows[0]['watts']:.0f} W -> "
        f"{ftp_rows[-1]['watts']:.0f} W"
    )

if pz:
    print(
        f"Power Zone average: "
        f"{mean([r['watts'] for r in pz]):.1f} W"
    )

if performance:
    best = performance[0]
    print(
        f"Best avg power: "
        f"{best['watts']:.0f} W "
        f"({best['title']})"
    )

print(
    f"All-zero records requiring review: "
    f"{len(zero_records)}"
)

print()
print("=" * 70)
print("PERFORMANCE ANALYSIS COMPLETE")
print("=" * 70)
