#!/usr/bin/env python3

import csv
from collections import Counter, defaultdict
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

    value = value.split(" (")[0]

    try:
        return datetime.strptime(value, "%Y-%m-%d %H:%M")
    except ValueError:
        return None


print("=" * 70)
print("PELOTON CSV AUDIT")
print("=" * 70)

# ------------------------------------------------------------------
# LOAD
# ------------------------------------------------------------------

try:
    with open(CSV_FILE, newline="", encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))
except FileNotFoundError:
    print(f"ERROR: file not found: {CSV_FILE}")
    raise SystemExit(1)

if not rows:
    print("ERROR: CSV contains no records.")
    raise SystemExit(1)

columns = list(rows[0].keys())

print(f"Total records: {len(rows)}")
print(f"Columns: {len(columns)}")

# ------------------------------------------------------------------
# DATE RANGE
# ------------------------------------------------------------------

dates = [
    parse_date(r["Workout Timestamp"])
    for r in rows
    if parse_date(r["Workout Timestamp"]) is not None
]

if dates:
    first_date = min(dates)
    last_date = max(dates)
    days_covered = (last_date - first_date).days

    print("\nDATE RANGE")
    print(f"  First workout: {first_date}")
    print(f"  Last workout:  {last_date}")
    print(f"  Days covered:  {days_covered}")

# ------------------------------------------------------------------
# WORKOUT TYPES
# ------------------------------------------------------------------

print("\nWORKOUT TYPES")

type_counts = Counter(
    clean(r["Type"]) or "(empty)"
    for r in rows
)

for name, count in type_counts.most_common():
    print(f"{count:4d}  {name}")

# ------------------------------------------------------------------
# FITNESS DISCIPLINES
# ------------------------------------------------------------------

print("\nFITNESS DISCIPLINES")

discipline_counts = Counter(
    clean(r["Fitness Discipline"]) or "(empty)"
    for r in rows
)

for name, count in discipline_counts.most_common():
    print(f"{count:4d}  {name}")

# ------------------------------------------------------------------
# INSTRUCTORS
# ------------------------------------------------------------------

print("\nINSTRUCTORS")

instructor_counts = Counter(
    clean(r["Instructor Name"]) or "(empty)"
    for r in rows
)

for name, count in instructor_counts.most_common():
    print(f"{count:4d}  {name}")

# ------------------------------------------------------------------
# DURATION
# ------------------------------------------------------------------

durations = [
    num(r["Length (minutes)"])
    for r in rows
]

durations = [
    x for x in durations
    if x is not None
]

if durations:
    print("\nDURATION")
    print(f"  Total minutes: {sum(durations):.0f}")
    print(f"  Average:       {sum(durations) / len(durations):.1f}")
    print(f"  Min:           {min(durations):.0f}")
    print(f"  Max:           {max(durations):.0f}")

    print("  Duration distribution:")

    duration_counts = Counter(durations)

    for duration in sorted(duration_counts):
        print(
            f"    {duration:4.0f} min: "
            f"{duration_counts[duration]}"
        )

# ------------------------------------------------------------------
# POWER ZONE
# ------------------------------------------------------------------

power_zone_rows = [
    r for r in rows
    if clean(r["Type"]).lower() == "power zone"
]

pz_watts = [
    num(r["Avg. Watts"])
    for r in power_zone_rows
    if num(r["Avg. Watts"]) is not None
    and num(r["Avg. Watts"]) > 0
]

pz_output = [
    num(r["Total Output"])
    for r in power_zone_rows
    if num(r["Total Output"]) is not None
    and num(r["Total Output"]) > 0
]

print("\nPOWER ZONE")
print(f"  Records: {len(power_zone_rows)}")

if pz_watts:
    print(
        f"  Avg watts:       "
        f"{sum(pz_watts) / len(pz_watts):.1f}"
    )

    print(
        f"  Min avg watts:   "
        f"{min(pz_watts):.0f}"
    )

    print(
        f"  Max avg watts:   "
        f"{max(pz_watts):.0f}"
    )

if pz_output:
    print(
        f"  Total output:    "
        f"{sum(pz_output):.0f} kJ"
    )

# ------------------------------------------------------------------
# FTP TESTS
# ------------------------------------------------------------------

ftp_rows = [
    r for r in rows
    if "FTP Test" in clean(r["Title"])
]

print("\nFTP TESTS")
print(f"  Records: {len(ftp_rows)}")

for r in ftp_rows:
    print(
        f"  {clean(r['Workout Timestamp'])} | "
        f"{clean(r['Title'])} | "
        f"Output={clean(r['Total Output'])} | "
        f"AvgW={clean(r['Avg. Watts'])} | "
        f"HR={clean(r['Avg. Heartrate'])}"
    )

# ------------------------------------------------------------------
# ZERO / SUSPICIOUS RECORDS
# ------------------------------------------------------------------

zero_output = 0
zero_watts = 0
zero_distance = 0

suspicious = []

for r in rows:

    output = num(r["Total Output"])
    watts = num(r["Avg. Watts"])
    distance = num(r["Distance (km)"])

    if output == 0:
        zero_output += 1

    if watts == 0:
        zero_watts += 1

    if distance == 0:
        zero_distance += 1

    if (
        output == 0
        and watts == 0
        and distance == 0
    ):
        suspicious.append(r)

print("\nZERO / SUSPICIOUS RECORDS")

print(f"  Output = 0:   {zero_output}")
print(f"  Watts = 0:    {zero_watts}")
print(f"  Distance = 0: {zero_distance}")

for r in suspicious:
    print(
        f"  ZERO: {clean(r['Workout Timestamp'])} | "
        f"{clean(r['Title'])} | "
        f"{clean(r['Length (minutes)'])} min"
    )

# ------------------------------------------------------------------
# MISSING METRICS
# ------------------------------------------------------------------

metrics = [
    "Total Output",
    "Avg. Watts",
    "Avg. Resistance",
    "Avg. Cadence (RPM)",
    "Avg. Speed (kph)",
    "Distance (km)",
    "Calories Burned",
    "Avg. Heartrate",
]

print("\nMISSING METRICS")

for metric in metrics:

    missing = sum(
        1 for r in rows
        if not clean(r[metric])
    )

    percentage = (
        missing / len(rows) * 100
    )

    print(
        f"  {metric:<25} "
        f"{missing:3d}/{len(rows)} "
        f"({percentage:5.1f}%)"
    )

# ------------------------------------------------------------------
# POTENTIAL DUPLICATES
# ------------------------------------------------------------------

duplicate_groups = defaultdict(int)

for r in rows:

    key = tuple(
        clean(r[c])
        for c in columns
    )

    duplicate_groups[key] += 1

duplicates = [
    (key, count)
    for key, count in duplicate_groups.items()
    if count > 1
]

print("\nPOTENTIAL DUPLICATES")
print(f"  Duplicate groups: {len(duplicates)}")

for key, count in duplicates:

    row = dict(
        zip(columns, key)
    )

    print(
        f"  {count}x | "
        f"{clean(row['Workout Timestamp'])} | "
        f"{clean(row['Title'])}"
    )

# ------------------------------------------------------------------
# POSSIBLE FAILED / RETRY RECORDS
# ------------------------------------------------------------------

print("\nPOSSIBLE FAILED / RETRY RECORDS")

for r in suspicious:

    print(
        f"  {clean(r['Workout Timestamp'])} | "
        f"{clean(r['Title'])} | "
        f"{clean(r['Length (minutes)'])} min"
    )

# ------------------------------------------------------------------
# TOP AVG WATTS
# ------------------------------------------------------------------

print("\nTOP AVG WATTS")

performance = []

for r in rows:

    watts = num(r["Avg. Watts"])

    if watts is not None and watts > 0:
        performance.append(
            (watts, r)
        )

# IMPORTANT:
# Sort ONLY by watts.
# This prevents Python from comparing dictionaries
# when two workouts have identical watt values.

performance_sorted = sorted(
    performance,
    key=lambda x: x[0],
    reverse=True
)

for watts, r in performance_sorted[:10]:

    print(
        f"  {watts:3.0f} W | "
        f"{clean(r['Workout Timestamp'])} | "
        f"{clean(r['Title'])}"
    )

# ------------------------------------------------------------------
# TOP TOTAL OUTPUT
# ------------------------------------------------------------------

print("\nTOP TOTAL OUTPUT")

outputs = []

for r in rows:

    output = num(r["Total Output"])

    if output is not None and output > 0:
        outputs.append(
            (output, r)
        )

outputs_sorted = sorted(
    outputs,
    key=lambda x: x[0],
    reverse=True
)

for output, r in outputs_sorted[:10]:

    print(
        f"  {output:4.0f} kJ | "
        f"{clean(r['Workout Timestamp'])} | "
        f"{clean(r['Title'])}"
    )

# ------------------------------------------------------------------
# FTP PROGRESSION
# ------------------------------------------------------------------

print("\nFTP PROGRESSION")

ftp_values = []

for r in ftp_rows:

    watts = num(r["Avg. Watts"])
    date = parse_date(r["Workout Timestamp"])

    if watts is not None and date is not None:

        ftp_values.append(
            (date, watts, r)
        )

ftp_values.sort(
    key=lambda x: x[0]
)

for date, watts, r in ftp_values:

    print(
        f"  {date.strftime('%Y-%m-%d')} | "
        f"{watts:.0f} W | "
        f"{clean(r['Title'])}"
    )

if len(ftp_values) >= 2:

    first_ftp = ftp_values[0][1]
    last_ftp = ftp_values[-1][1]

    change = last_ftp - first_ftp
    percentage = (
        change / first_ftp * 100
    )

    print(
        f"  Change: {change:+.0f} W "
        f"({percentage:+.1f}%)"
    )

# ------------------------------------------------------------------
# COMPLETE
# ------------------------------------------------------------------

print("\n" + "=" * 70)
print("AUDIT COMPLETE")
print("=" * 70)
