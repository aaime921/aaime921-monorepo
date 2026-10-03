"""
trainiq.synthetic_dataset — Feature 0.7 (Priority 1)

Versioned Synthetic Athlete Dataset.

Per the Chief Architect's explicit direction (Tier B roadmap review): this is
not throwaway test data. It is the canonical, versioned regression dataset
that every subsequent epic and every Quality Gate is measured against.

Constitution alignment:
  - Principle 1 (Never fabricate missing data): the dataset deliberately
    includes Unknown-load days, not zero-filled ones, so downstream code is
    forced to handle the distinction correctly from day one.
  - Principle 7 (Quality gates before features): this module's own test
    suite is itself Gate 0 for this dataset — if the dataset can't prove its
    own internal consistency, nothing built on top of it can be trusted.

DATASET_VERSION must be bumped (and a CHANGELOG entry added below) any time
the generated shape changes, since downstream Quality Gates pin against a
specific version to keep regressions detectable rather than silently masked
by a shifting fixture.
"""

from __future__ import annotations

import json
import random
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta, timezone
from enum import Enum
from pathlib import Path
from typing import Optional

from trainiq.athlete.profile import AthleteProfile

DATASET_VERSION = "1.0.0"

# CHANGELOG
# 1.0.0 — initial dataset: covers missing data, delayed sync, cross-provider
#         duplicates, zero-training weeks, rapid weight change, back-to-back
#         hard sessions, a deload week, incomplete records, Peloton-without-HR,
#         and Strava-with-HR-but-no-power. See SCENARIOS below.


class Discipline(str, Enum):
    CYCLING = "Cycling"
    RUNNING = "Running"
    STRENGTH = "Strength"
    OTHER = "Other"


class LoadMethod(str, Enum):
    TSS = "tss"
    TRIMP = "trimp"
    NONE = "null"  # Unknown, per ADR-016 — never coerced to 0


@dataclass
class RawActivity:
    """Mirrors the shape a connector would hand to Normalization (Epic 6)."""
    provider: str
    external_id: str
    start_time: str  # ISO 8601
    duration_s: int
    discipline_raw: str  # provider-native vocabulary, pre-taxonomy-mapping
    avg_hr: Optional[int] = None
    max_hr: Optional[int] = None
    avg_power: Optional[int] = None
    max_power: Optional[int] = None
    distance_m: Optional[float] = None
    calories: Optional[int] = None
    synced_at: Optional[str] = None  # when the connector actually saw this
    # record — deliberately can differ from start_time to model delayed sync


@dataclass
class WeighIn:
    provider: str
    external_id: str
    timestamp: str
    weight_kg: Optional[float] = None
    body_fat_pct: Optional[float] = None
    muscle_mass_pct: Optional[float] = None
@dataclass
class ScenarioMeta:
    name: str
    description: str
    stresses_principle: str  # which Constitution principle this scenario exercises


@dataclass
class SyntheticAthlete:
    athlete_id: str
    profile: AthleteProfile
    activities: list[RawActivity] = field(default_factory=list)
    weigh_ins: list[WeighIn] = field(default_factory=list)
    scenario: ScenarioMeta = None


def _iso(d: date, hour: int = 7, minute: int = 0) -> str:
    return datetime(d.year, d.month, d.day, hour, minute, tzinfo=timezone.utc).isoformat()


def _daterange(start: date, days: int):
    for i in range(days):
        yield start + timedelta(days=i)


# ---------------------------------------------------------------------------
# Scenario builders — each produces one SyntheticAthlete exercising a
# specific "hostile" condition named explicitly in the Tier B review.
# ---------------------------------------------------------------------------

def scenario_complete_athlete(start: date, rng: random.Random) -> SyntheticAthlete:
    """The 'ideal' athlete — complete HR+power data, no gaps. Baseline case,
    not a hostile one, but required so Gates can distinguish 'correctly
    handles messy data' from 'only works on messy data'."""
    activities = []
    for i, d in enumerate(_daterange(start, 28)):
        if i % 7 in (0, 3):  # rest days
            continue
        activities.append(RawActivity(
            provider="strava", external_id=f"complete-{i}", start_time=_iso(d),
            duration_s=3600, discipline_raw="Ride",
            avg_hr=145, max_hr=168, avg_power=210, max_power=310,
            distance_m=30000, calories=650, synced_at=_iso(d, 8),
        ))
    weigh_ins = [
        WeighIn("eufy", f"w-{i}", _iso(d, 6, 30), weight_kg=75.0 - i * 0.02)
        for i, d in enumerate(_daterange(start, 28))
    ]
    return SyntheticAthlete(
        athlete_id="complete_athlete",
        profile=AthleteProfile(sex="male", date_of_birth="1990-01-01",
                                resting_hr=52, max_hr=185, ftp_watts=250),
        activities=activities, weigh_ins=weigh_ins,
        scenario=ScenarioMeta("complete_athlete",
                               "Fully populated baseline — no missing data.",
                               "Baseline (control case, not a hostile scenario)"),
    )


def scenario_missing_load_data(start: date, rng: random.Random) -> SyntheticAthlete:
    """Some activities have neither HR nor power — must become Unknown load,
    never zero. Exercises ADR-016 / Constitution Principle 1 directly."""
    activities = []
    for i, d in enumerate(_daterange(start, 14)):
        if i % 3 == 0:
            continue
        has_data = i % 4 != 0
        activities.append(RawActivity(
            provider="peloton", external_id=f"missing-{i}", start_time=_iso(d),
            duration_s=2700, discipline_raw="strength",
            avg_hr=None if not has_data else 130,
            avg_power=None,  # Peloton strength classes never report power
            calories=None if not has_data else 300,
            synced_at=_iso(d, 9),
        ))
    return SyntheticAthlete(
        athlete_id="missing_load_data",
        profile=AthleteProfile(sex="female", resting_hr=58, max_hr=178),
        activities=activities,
        scenario=ScenarioMeta("missing_load_data",
                               "Activities with no HR and no power — must resolve to Unknown, never 0.",
                               "Principle 1: Never fabricate missing data"),
    )


def scenario_delayed_sync(start: date, rng: random.Random) -> SyntheticAthlete:
    """synced_at is days after start_time — exercises checkpointed/backfill
    sync (ADR-008) and cache invalidation on late-arriving data (Feature 7.2)."""
    activities = []
    for i, d in enumerate(_daterange(start, 10)):
        delay_days = 5 if i == 3 else 0  # one activity arrives 5 days late
        synced = d + timedelta(days=delay_days)
        activities.append(RawActivity(
            provider="strava", external_id=f"delayed-{i}", start_time=_iso(d),
            duration_s=3000, discipline_raw="Run",
            avg_hr=150, distance_m=8000, calories=500,
            synced_at=_iso(synced, 8),
        ))
    return SyntheticAthlete(
        athlete_id="delayed_sync",
        profile=AthleteProfile(sex="male", resting_hr=50, max_hr=190),
        activities=activities,
        scenario=ScenarioMeta("delayed_sync",
                               "One activity's synced_at is 5 days after start_time.",
                               "ADR-008 checkpointed sync / Feature 7.2 cache invalidation"),
    )


def scenario_cross_provider_duplicates(start: date, rng: random.Random) -> SyntheticAthlete:
    """The same real-world ride appears on both Strava and Peloton — exercises
    deduplication confidence scoring (Milestone 4, Section 4)."""
    activities = []
    for i, d in enumerate(_daterange(start, 6)):
        base_hour = 7
        # Peloton ride
        activities.append(RawActivity(
            provider="peloton", external_id=f"dup-p-{i}", start_time=_iso(d, base_hour, 0),
            duration_s=1800, discipline_raw="cycling",
            avg_hr=140, avg_power=180, calories=350, synced_at=_iso(d, base_hour + 1),
        ))
        # Auto-posted to Strava, timestamp shifted by 3 minutes (clock skew)
        activities.append(RawActivity(
            provider="strava", external_id=f"dup-s-{i}", start_time=_iso(d, base_hour, 3),
            duration_s=1790, discipline_raw="Ride",
            avg_hr=141, avg_power=None, calories=352, synced_at=_iso(d, base_hour + 2),
        ))
    return SyntheticAthlete(
        athlete_id="cross_provider_duplicates",
        profile=AthleteProfile(sex="female", resting_hr=55, max_hr=182),
        activities=activities,
        scenario=ScenarioMeta("cross_provider_duplicates",
                               "Every ride appears once via Peloton and once via Strava, with ~3min clock skew.",
                               "Deduplication confidence scoring (Milestone 4 §4)"),
    )


def scenario_zero_training_week(start: date, rng: random.Random) -> SyntheticAthlete:
    """A full week with no activity at all — must remain distinct from a
    week full of Unknown-load days. Exercises Monotony/Strain correctness
    (R-ANLY-02) and Unknown-vs-Missing-vs-Zero (ADR-019)."""
    activities = []
    for i, d in enumerate(_daterange(start, 21)):
        if 7 <= i < 14:
            continue  # the zero-training week — no records at all
        activities.append(RawActivity(
            provider="strava", external_id=f"ztw-{i}", start_time=_iso(d),
            duration_s=2400, discipline_raw="Run", avg_hr=148,
            distance_m=6000, calories=420, synced_at=_iso(d, 8),
        ))
    return SyntheticAthlete(
        athlete_id="zero_training_week",
        profile=AthleteProfile(sex="male", resting_hr=48, max_hr=192),
        activities=activities,
        scenario=ScenarioMeta("zero_training_week",
                               "Days 7-13 have zero activity records (true rest, not Unknown).",
                               "ADR-019: Zero, Missing, and Unknown are distinct states"),
    )


def scenario_rapid_weight_change(start: date, rng: random.Random) -> SyntheticAthlete:
    """A sharp, real weight swing — exercises the recommendation NOT to build
    a fabricated performance-correlation model on top of it (Milestone C §3)."""
    weigh_ins = []
    for i, d in enumerate(_daterange(start, 30)):
        if i < 15:
            w = 80.0 - i * 0.05
        else:
            w = 79.25 - (i - 15) * 0.6  # sudden, unrealistic-looking drop
        weigh_ins.append(WeighIn("eufy", f"rwc-{i}", _iso(d, 6, 45), weight_kg=round(w, 2)))
    return SyntheticAthlete(
        athlete_id="rapid_weight_change",
        profile=AthleteProfile(sex="male", resting_hr=54, max_hr=180),
        weigh_ins=weigh_ins,
        scenario=ScenarioMeta("rapid_weight_change",
                               "A sudden, large weight drop in the back half of the window.",
                               "Milestone C §3: weight is a trend, never an inferred performance driver"),
    )


def scenario_back_to_back_hard_sessions(start: date, rng: random.Random) -> SyntheticAthlete:
    """High load, low variety — exercises Monotony/Strain and Ramp Rate
    elevated-signal generation (Feature 8.3, 8.4, 8.6)."""
    activities = []
    for i, d in enumerate(_daterange(start, 14)):
        activities.append(RawActivity(
            provider="strava", external_id=f"b2b-{i}", start_time=_iso(d),
            duration_s=4200, discipline_raw="Ride",
            avg_hr=165, max_hr=182, avg_power=240, max_power=340,
            distance_m=35000, calories=800, synced_at=_iso(d, 8),
        ))
    return SyntheticAthlete(
        athlete_id="back_to_back_hard_sessions",
        profile=AthleteProfile(sex="female", resting_hr=50, max_hr=188, ftp_watts=230),
        activities=activities,
        scenario=ScenarioMeta("back_to_back_hard_sessions",
                               "14 consecutive high-intensity days, no easy days at all.",
                               "Feature 8.3/8.4/8.6: high Monotony + elevated Ramp Rate signal"),
    )


def scenario_deload_week(start: date, rng: random.Random) -> SyntheticAthlete:
    """Three normal weeks then one deliberately light week — exercises TSB
    trending positive (freshness) rather than flagged as a data problem."""
    activities = []
    for i, d in enumerate(_daterange(start, 28)):
        week = i // 7
        if week == 3:
            if i % 2 == 0:
                continue
            duration, power, hr = 1200, 120, 120
        else:
            if i % 7 in (0,):
                continue
            duration, power, hr = 3600, 220, 155
        activities.append(RawActivity(
            provider="strava", external_id=f"deload-{i}", start_time=_iso(d),
            duration_s=duration, discipline_raw="Ride",
            avg_hr=hr, avg_power=power, distance_m=duration * 8, calories=duration // 6,
            synced_at=_iso(d, 8),
        ))
    return SyntheticAthlete(
        athlete_id="deload_week",
        profile=AthleteProfile(sex="male", resting_hr=49, max_hr=189, ftp_watts=260),
        activities=activities,
        scenario=ScenarioMeta("deload_week",
                               "Weeks 1-3 normal load, week 4 deliberately light (a real deload, not missing data).",
                               "TSB should trend positive/fresh, not be flagged as a data-quality issue"),
    )


def scenario_incomplete_records(start: date, rng: random.Random) -> SyntheticAthlete:
    """Records missing distance, calories, or other secondary fields even
    though HR/power (the load-relevant fields) are present — exercises that
    Normalization only marks Unknown for what's actually load-relevant."""
    activities = []
    for i, d in enumerate(_daterange(start, 12)):
        activities.append(RawActivity(
            provider="peloton", external_id=f"incomplete-{i}", start_time=_iso(d),
            duration_s=1800, discipline_raw="cycling",
            avg_hr=138, avg_power=190,
            distance_m=None,  # Peloton indoor rides often omit distance
            calories=None if i % 2 == 0 else 300,
            synced_at=_iso(d, 8),
        ))
    return SyntheticAthlete(
        athlete_id="incomplete_records",
        profile=AthleteProfile(sex="female", resting_hr=56, max_hr=180),
        activities=activities,
        scenario=ScenarioMeta("incomplete_records",
                               "Secondary fields (distance, calories) missing while HR/power are present.",
                               "Normalization: confidence reflects only load-relevant completeness"),
    )


def scenario_peloton_no_hr(start: date, rng: random.Random) -> SyntheticAthlete:
    """Explicitly named in the Chief Architect's list: Peloton class with no
    HR monitor paired. Only 'output' (power) is available."""
    activities = []
    for i, d in enumerate(_daterange(start, 10)):
        activities.append(RawActivity(
            provider="peloton", external_id=f"pnh-{i}", start_time=_iso(d),
            duration_s=1800, discipline_raw="cycling",
            avg_hr=None, max_hr=None, avg_power=175, max_power=260,
            calories=None, synced_at=_iso(d, 8),
        ))
    return SyntheticAthlete(
        athlete_id="peloton_no_hr",
        profile=AthleteProfile(sex="male", resting_hr=51, max_hr=186, ftp_watts=210),
        activities=activities,
        scenario=ScenarioMeta("peloton_no_hr",
                               "Peloton cycling classes with power but no paired HR monitor.",
                               "Milestone A §4: TSS should compute (power+FTP present) despite no HR"),
    )


def scenario_strava_no_power(start: date, rng: random.Random) -> SyntheticAthlete:
    """Explicitly named in the Chief Architect's list: Strava activity with
    HR but no power meter — must fall back to TRIMP, per ADR-015."""
    activities = []
    for i, d in enumerate(_daterange(start, 10)):
        activities.append(RawActivity(
            provider="strava", external_id=f"snp-{i}", start_time=_iso(d),
            duration_s=3300, discipline_raw="Run",
            avg_hr=152, max_hr=175, avg_power=None, max_power=None,
            distance_m=9000, calories=550, synced_at=_iso(d, 8),
        ))
    return SyntheticAthlete(
        athlete_id="strava_no_power",
        profile=AthleteProfile(sex="female", resting_hr=53, max_hr=183),
        activities=activities,
        scenario=ScenarioMeta("strava_no_power",
                               "Strava runs with HR but no power meter (running, so expected).",
                               "ADR-015: TRIMP fallback when power/FTP unavailable"),
    )


SCENARIOS = [
    scenario_complete_athlete,
    scenario_missing_load_data,
    scenario_delayed_sync,
    scenario_cross_provider_duplicates,
    scenario_zero_training_week,
    scenario_rapid_weight_change,
    scenario_back_to_back_hard_sessions,
    scenario_deload_week,
    scenario_incomplete_records,
    scenario_peloton_no_hr,
    scenario_strava_no_power,
]


def build_dataset(seed: int = 42, start: Optional[date] = None) -> dict:
    """Deterministic build — same seed always produces the same dataset, so
    it can be versioned and diffed like code, not regenerated ad hoc."""
    rng = random.Random(seed)
    start = start or date(2026, 1, 5)  # fixed anchor date, not "today"
    athletes = [builder(start, rng) for builder in SCENARIOS]
    return {
        "dataset_version": DATASET_VERSION,
        "seed": seed,
        "anchor_date": start.isoformat(),
        "athlete_count": len(athletes),
        "athletes": [asdict(a) for a in athletes],
    }


def save_dataset(path: Path, seed: int = 42) -> Path:
    data = build_dataset(seed=seed)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, sort_keys=False))
    return path


def load_dataset(path: Path) -> dict:
    data = json.loads(path.read_text())
    if data.get("dataset_version") != DATASET_VERSION:
        raise ValueError(
            f"Dataset version mismatch: file has {data.get('dataset_version')!r}, "
            f"code expects {DATASET_VERSION!r}. Regenerate with save_dataset()."
        )
    return data


if __name__ == "__main__":
    out = Path(__file__).parent.parent / "data" / "synthetic_athlete_dataset.json"
    save_dataset(out)
    print(f"Wrote dataset v{DATASET_VERSION} ({len(SCENARIOS)} scenarios) to {out}")
