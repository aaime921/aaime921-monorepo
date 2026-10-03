#!/usr/bin/env python3
"""
TrainIQ — Peloton Performance Analysis v3
HR Data Quality + Performance Analysis

Extends analyze_peloton_performance_v2.py (same CSV loading, same helper
functions, same FTP/duration/normalization logic — unchanged where the
architectural spec did not ask for a change). New in v3:

  - Explicit HR classification (VALID / MISSING / SUSPICIOUS_LOW /
    SUSPICIOUS_HIGH / INVALID), general rule, never hardcoded to a
    specific date/record.
  - power_hr_ratio computed ONLY for HR-valid records; null otherwise.
    The record itself remains fully usable for power-only analysis.
  - RAW -> DATA QUALITY -> ANALYTICAL ELIGIBILITY -> DESCRIPTIVE METRICS
    kept as separate passes, not interleaved.
  - overall_status (VALID/INCOMPLETE/ZERO_FAILED) computed from power
    metrics ONLY, exactly as in v2 — an HR anomaly never downgrades it.
    (SUSPICIOUS as a 4th overall_status was NOT implemented — see the
    ASSUMPTION/IMPACT/ALTERNATIVE note delivered alongside this script.)

Does NOT modify the raw CSV. Does NOT require pandas. Does NOT assign
athlete states (FRESH/FATIGUED/etc.) — descriptive only, per spec section 15.
"""

import csv
from collections import Counter, defaultdict
from datetime import datetime
from statistics import mean, median

CSV_FILE = "aimea75_workouts.csv"

# ================================================================
# HR CLASSIFICATION THRESHOLDS — explicit, general, not tied to any
# specific record. See ASSUMPTION note delivered alongside this file
# for why these exact bounds were chosen (no physiological threshold
# was specified in the architectural spec).
# ================================================================
HR_INVALID_MAX = 0          # HR <= 0 is not a physiologically possible reading
HR_SUSPICIOUS_LOW_MAX = 30  # below this, implausible for an active workout
HR_SUSPICIOUS_HIGH_MIN = 220  # above this, exceeds any plausible human ceiling


# ================================================================
# HELPERS (unchanged from v2)
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
    value = value.replace(" (UTC)", "").replace(" (+01)", "")
    for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%d %H:%M:%S"):
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
# HR CLASSIFICATION (new in v3) — general rule, applies to every record
# ================================================================

def classify_hr(raw_hr):
    """Returns (hr_status, hr, hr_exclusion_reason).
    hr is the value to use in HR-based metrics — None unless VALID."""
    if raw_hr is None:
        return "MISSING", None, "no heart rate recorded"
    if raw_hr <= HR_INVALID_MAX:
        return "INVALID", None, "physiologically impossible (<= 0 bpm)"
    if raw_hr < HR_SUSPICIOUS_LOW_MAX:
        return "SUSPICIOUS_LOW", None, "physiologically implausible for an active workout"
    if raw_hr >= HR_SUSPICIOUS_HIGH_MIN:
        return "SUSPICIOUS_HIGH", None, "exceeds plausible human heart rate ceiling"
    return "VALID", raw_hr, None


# ================================================================
# STAGE 1: RAW — load exactly what the CSV contains, no interpretation
# ================================================================

with open(CSV_FILE, newline="", encoding="utf-8-sig") as f:
    raw_rows = list(csv.DictReader(f))

print_header("TRAINIQ PERFORMANCE ANALYSIS v3")
print(f"Total raw records: {len(raw_rows)}")

# ================================================================
# STAGE 2: DATA QUALITY — parse + classify, still one record per raw row,
# nothing removed, nothing imputed
# ================================================================

records = []

for r in raw_rows:
    raw_hr = num(r["Avg. Heartrate"])
    hr_status, hr, hr_exclusion_reason = classify_hr(raw_hr)

    watts = num(r["Avg. Watts"])
    output = num(r["Total Output"])
    resistance = num(r["Avg. Resistance"])
    cadence = num(r["Avg. Cadence (RPM)"])
    speed = num(r["Avg. Speed (kph)"])
    distance = num(r["Distance (km)"])

    # overall_status: power metrics ONLY, unchanged from v2's logic.
    # An HR anomaly never downgrades this — see ASSUMPTION note.
    core_metrics = [output, watts, resistance, cadence, speed, distance]
    all_zero = all(x is not None and x == 0 for x in core_metrics)
    core_missing = any(x is None for x in core_metrics)
    if all_zero:
        overall_status = "ZERO_FAILED"
    elif core_missing:
        overall_status = "INCOMPLETE"
    else:
        overall_status = "VALID"

    records.append({
        "raw": r,
        "date": parse_date(r["Workout Timestamp"]),
        "title": clean(r["Title"]),
        "type": clean(r["Type"]),
        "discipline": clean(r["Fitness Discipline"]),
        "instructor": clean(r["Instructor Name"]),
        "duration": num(r["Length (minutes)"]),
        "output": output,
        "watts": watts,
        "resistance": resistance,
        "cadence": cadence,
        "speed": speed,
        "distance": distance,
        "calories": num(r["Calories Burned"]),
        "raw_hr": raw_hr,
        "hr": hr,                              # None unless hr_status == VALID
        "hr_status": hr_status,
        "hr_exclusion_reason": hr_exclusion_reason,
        "overall_status": overall_status,
        # power_hr_ratio filled in Stage 3, once HR validity is known
        "power_hr_ratio": None,
    })

records.sort(key=lambda r: r["date"] or datetime.min)

# ================================================================
# STAGE 3: ANALYTICAL ELIGIBILITY — derive eligibility flags per metric,
# still no aggregation
# ================================================================

for r in records:
    if r["hr"] is not None and r["watts"] is not None and r["watts"] > 0:
        r["power_hr_ratio"] = r["watts"] / r["hr"]
    # else stays None — record remains fully usable for power-only analysis

# ================================================================
# STAGE 4: DESCRIPTIVE METRICS — everything below only aggregates,
# never reclassifies
# ================================================================

# ---- 2. FTP TIMELINE ----
print_header("FTP TIMELINE")

ftp_rows = [r for r in records if "FTP Test" in r["title"] and r["watts"] is not None]

for r in ftp_rows:
    print(
        f"{r['date'].strftime('%Y-%m-%d')} | {r['watts']:.0f} W | "
        f"Output={r['output']:.0f} kJ | HR={r['raw_hr']} | HR_status={r['hr_status']}"
    )

if len(ftp_rows) >= 2:
    first, last = ftp_rows[0]["watts"], ftp_rows[-1]["watts"]
    print(f"\nFTP change: {last - first:+.0f} W ({pct_change(first, last):+.1f}%)")

# ---- 3. POWER-DURATION PROFILE — ALL WORKOUTS ----
print_header("POWER-DURATION PROFILE — ALL WORKOUTS")

duration_groups = defaultdict(list)
for r in records:
    if r["watts"] is not None and r["watts"] > 0 and r["duration"] is not None:
        duration_groups[int(r["duration"])].append(r)

for duration in sorted(duration_groups):
    group = duration_groups[duration]
    watts_list = [r["watts"] for r in group]
    flag = "  [SMALL N]" if len(group) < 3 else ""
    print(
        f"{duration:>3} min | n={len(group):>2} | avg={mean(watts_list):>6.1f} W | "
        f"median={median(watts_list):>6.1f} W | best={max(watts_list):>6.1f} W{flag}"
    )

# ---- 4. POWER ZONE BASELINE — POWER ZONE ONLY ----
print_header("POWER ZONE BASELINE — POWER ZONE ONLY")

pz_duration = defaultdict(list)
for r in records:
    if r["type"].lower() == "power zone" and r["watts"] is not None and r["watts"] > 0 and r["duration"] is not None:
        pz_duration[int(r["duration"])].append(r)

for duration in sorted(pz_duration):
    group = pz_duration[duration]
    watts_list = [r["watts"] for r in group]
    flag = "  [SMALL N]" if len(group) < 2 else ""
    print(
        f"{duration:>3} min | n={len(group):>2} | baseline avg={mean(watts_list):>6.1f} W | "
        f"median={median(watts_list):>6.1f} W | best={max(watts_list):>6.1f} W{flag}"
    )

# ---- 5. NORMALIZED POWER ZONE PERFORMANCE ----
print_header("NORMALIZED POWER ZONE PERFORMANCE")

# Small-N baselines (n < 2) are excluded from producing a deviation number
# at all — a baseline of one observation cannot be compared against itself
# meaningfully. This mirrors v2's existing behavior; the [SMALL N] flag
# above makes that exclusion visible instead of silent.
baseline = {d: mean(r["watts"] for r in g) for d, g in pz_duration.items() if len(g) >= 2}

normalized = []
for r in records:
    duration = int(r["duration"]) if r["duration"] is not None else None
    if r["type"].lower() == "power zone" and duration in baseline and r["watts"] is not None and r["watts"] > 0:
        b = baseline[duration]
        normalized.append({"record": r, "baseline": b, "deviation": (r["watts"] - b) / b * 100})

normalized.sort(key=lambda x: x["deviation"], reverse=True)

print("Best relative sessions:")
for x in normalized[:10]:
    r = x["record"]
    print(
        f"{x['deviation']:+6.1f}% | {r['date'].strftime('%Y-%m-%d')} | {int(r['duration']):>2} min | "
        f"{r['watts']:.0f} W | baseline={x['baseline']:.1f} W | {r['title']}"
    )
print("\nLowest relative sessions:")
for x in normalized[-10:]:
    r = x["record"]
    print(
        f"{x['deviation']:+6.1f}% | {r['date'].strftime('%Y-%m-%d')} | {int(r['duration']):>2} min | "
        f"{r['watts']:.0f} W | baseline={x['baseline']:.1f} W | {r['title']}"
    )

# ---- 6. MONTHLY TREND ----
print_header("MONTHLY NORMALIZED POWER ZONE TREND")

monthly = defaultdict(list)
for x in normalized:
    monthly[x["record"]["date"].strftime("%Y-%m")].append(x["deviation"])

for month in sorted(monthly):
    values = monthly[month]
    print(
        f"{month} | n={len(values):>2} | avg deviation={mean(values):>+6.1f}% | "
        f"median={median(values):>+6.1f}% | best={max(values):>+6.1f}% | worst={min(values):>+6.1f}%"
    )

# ---- 7. RECENT VS HISTORICAL ----
print_header("RECENT VS HISTORICAL BASELINE")

recent, historical = [], []
if normalized:
    normalized_by_date = sorted(normalized, key=lambda x: x["record"]["date"])
    cutoff = normalized_by_date[-1]["record"]["date"]
    recent = [x for x in normalized_by_date if (cutoff - x["record"]["date"]).days <= 90]
    historical = [x for x in normalized_by_date if (cutoff - x["record"]["date"]).days > 90]

    if recent:
        print(f"Recent window: {recent[0]['record']['date'].strftime('%Y-%m-%d')} -> {recent[-1]['record']['date'].strftime('%Y-%m-%d')}")
        print(
            f"n={len(recent)} | avg deviation={mean(x['deviation'] for x in recent):+.1f}% | "
            f"median={median(x['deviation'] for x in recent):+.1f}%"
        )
    if historical:
        print(f"Historical window: {historical[0]['record']['date'].strftime('%Y-%m-%d')} -> {historical[-1]['record']['date'].strftime('%Y-%m-%d')}")
        print(
            f"n={len(historical)} | avg deviation={mean(x['deviation'] for x in historical):+.1f}% | "
            f"median={median(x['deviation'] for x in historical):+.1f}%"
        )

# ---- 8. HEART RATE DATA QUALITY ----
print_header("HEART RATE DATA QUALITY")

hr_status_counts = Counter(r["hr_status"] for r in records)
hr_present = sum(1 for r in records if r["raw_hr"] is not None)
hr_usable = hr_status_counts.get("VALID", 0)

print(f"HR records present:      {hr_present}")
print(f"HR valid:                {hr_status_counts.get('VALID', 0)}")
print(f"HR missing:               {hr_status_counts.get('MISSING', 0)}")
print(f"HR suspicious low:        {hr_status_counts.get('SUSPICIOUS_LOW', 0)}")
print(f"HR suspicious high:       {hr_status_counts.get('SUSPICIOUS_HIGH', 0)}")
print(f"HR invalid:               {hr_status_counts.get('INVALID', 0)}")
print(f"HR usable:                {hr_usable}/{len(records)}")

flagged = [r for r in records if r["hr_status"] not in ("VALID", "MISSING")]
if flagged:
    print("\nFlagged HR records:")
    for r in flagged:
        print(
            f"  {r['date'].strftime('%Y-%m-%d %H:%M')} | raw_hr={r['raw_hr']} | "
            f"status={r['hr_status']} | reason={r['hr_exclusion_reason']} | {r['title']}"
        )

# ---- 9. POWER / HEART RATE (valid-HR records only) ----
print_header("POWER / HEART RATE")

hr_valid_records = [r for r in records if r["power_hr_ratio"] is not None]
print(f"Valid W/HR records: {len(hr_valid_records)}")

if hr_valid_records:
    ratios = [r["power_hr_ratio"] for r in hr_valid_records]
    print(f"Average W/HR: {mean(ratios):.3f}")
    print(f"Median W/HR:  {median(ratios):.3f}")

    print("\nBy duration (valid HR only):")
    ratio_groups = defaultdict(list)
    for r in hr_valid_records:
        if r["duration"] is not None:
            ratio_groups[int(r["duration"])].append(r["power_hr_ratio"])
    for duration in sorted(ratio_groups):
        values = ratio_groups[duration]
        print(f"{duration:>3} min | n={len(values):>2} | avg={mean(values):.3f} | median={median(values):.3f}")

excluded_from_whr = [r for r in records if r["raw_hr"] is not None and r["power_hr_ratio"] is None]
print(f"\nRecords with an HR value present but EXCLUDED from W/HR: {len(excluded_from_whr)}")
for r in excluded_from_whr:
    print(f"  {r['date'].strftime('%Y-%m-%d %H:%M')} | raw_hr={r['raw_hr']} | status={r['hr_status']} | {r['title']}")

# ---- 10. ZERO / FAILED REVIEW ----
print_header("ZERO / FAILED REVIEW")

zero_records = [r for r in records if r["output"] == 0 and r["watts"] == 0 and r["distance"] == 0]

for r in zero_records:
    print(f"{r['date'].strftime('%Y-%m-%d %H:%M')} | {int(r['duration']) if r['duration'] is not None else 'n/a'} min | {r['title']}")
    nearby = [
        o for o in records
        if o is not r and o["date"] is not None and r["date"] is not None
        and abs((o["date"] - r["date"]).total_seconds()) <= 300
    ]
    for o in nearby:
        label = "POSSIBLE RETRY / DUPLICATE-ATTEMPT"
        print(
            f"    [{label}] {o['date'].strftime('%Y-%m-%d %H:%M')} | "
            f"{int(o['duration']) if o['duration'] is not None else 'n/a'} min | "
            f"{o['watts'] if o['watts'] is not None else 'n/a'} W | "
            f"{o['output'] if o['output'] is not None else 'n/a'} kJ | {o['title']}"
        )

# ---- 11. DATA QUALITY CLASSIFICATION ----
print_header("DATA QUALITY CLASSIFICATION")

overall_counts = Counter(r["overall_status"] for r in records)
for status in ("VALID", "INCOMPLETE", "ZERO_FAILED"):
    print(f"{status:<15} {overall_counts.get(status, 0):>3}")

print("\nRecords requiring review (overall_status != VALID):")
for r in records:
    if r["overall_status"] != "VALID":
        print(
            f"{r['overall_status']:<15} | {r['date'].strftime('%Y-%m-%d %H:%M')} | "
            f"{int(r['duration']) if r['duration'] is not None else 'n/a'} min | {r['title']}"
        )

# ---- 12. TRAINIQ SIGNAL SUMMARY (descriptive only) ----
print_header("TRAINIQ SIGNAL SUMMARY")
print("This section is descriptive only. It does not assign an athlete state.")

if len(ftp_rows) >= 2:
    first, last = ftp_rows[0]["watts"], ftp_rows[-1]["watts"]
    print(f"FTP direction: {first:.0f} W -> {last:.0f} W ({last - first:+.0f} W)")

if recent:
    print(f"Recent normalized PZ performance: {mean(x['deviation'] for x in recent):+.1f}% vs duration baseline")

print(f"HR data usability: {hr_usable}/{len(records)} records")
print(f"Valid records: {overall_counts.get('VALID', 0)}")
print(f"Incomplete records: {overall_counts.get('INCOMPLETE', 0)}")
print(f"Zero/failed candidates: {overall_counts.get('ZERO_FAILED', 0)}")

print()
print("=" * 70)
print("PERFORMANCE ANALYSIS v3 COMPLETE")
print("=" * 70)
