"""
trainiq.normalization.load — Epic 6, slice 4: Training Load Computation

Per ADR-015 (Milestone A): TRIMP is the primary cross-provider metric; TSS
is an opportunistic enhancement when power + FTP are both available. Per
ADR-016: never fabricate — when the athlete's physiological baseline isn't
available, training_load resolves to None with a specific, logged reason,
never a guess.

Independence from source_confidence (Chief Architect's explicit sequencing
for this slice): training_load and source_confidence describe two
different things — training_load estimates physiological stress;
source_confidence describes how complete the observed record was. Neither
reads the other, and this module has no dependency on
trainiq.normalization.confidence at all (verified by this module's own
test file, the same containment pattern used for the taxonomy and
confidence slices).

Q1 resolution (Epic 6 Discovery Report, approved by the Chief Architect):
no `athlete_profile` persistence exists yet — that's Epic 7, Feature 7.1.
This module is fully implemented (method selection, prerequisite
validation, reason logging) but will resolve every record to
training_load=None, method=UNKNOWN until a caller supplies a real,
populated AthleteProfile. This is not a placeholder standing in for future
logic — it IS the correct behavior per ADR-016, applied to a case where
the missing input is a whole persistent profile rather than a single
field. When Epic 7 exists, this module requires zero changes; only the
caller starts passing a populated profile instead of None.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import Enum
from typing import Optional

from trainiq.athlete.profile import AthleteProfile
from trainiq.logging_setup import diagnostic_logger


class TrainingLoadMethod(str, Enum):
    TSS = "tss"
    TRIMP = "trimp"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class TrainingLoadResult:
    load: Optional[float]
    method: TrainingLoadMethod
    # Populated whenever load is None; always None when load is a real
    # value — the two fields are mutually exclusive by construction, never
    # both meaningful at once.
    reason: Optional[str]


def compute_training_load(normalized: dict, profile: Optional[AthleteProfile]) -> TrainingLoadResult:
    """`normalized` is an ACTIVITY-shaped record (avg_power/avg_hr/duration_s
    present or None, per each connector's normalize() output). `profile` is
    the athlete's persistent physiological facts, or None if not yet
    available (see module docstring, Q1).

    Selection order, per ADR-015: TSS opportunistically first (requires
    avg_power AND profile.ftp_watts), falling back to TRIMP (requires
    avg_hr AND profile.sex AND profile.resting_hr AND profile.max_hr),
    falling back to Unknown with a specific reason — never a blended guess
    between the two methods."""
    if profile is None:
        return _unknown("athlete_profile not available (Epic 7 not yet implemented)")

    duration_s = normalized.get("duration_s")
    if not duration_s or duration_s <= 0:
        return _unknown("no valid duration_s on this record")

    avg_power = normalized.get("avg_power")
    if avg_power is not None and profile.ftp_watts:
        return _compute_tss(avg_power, duration_s, profile.ftp_watts)

    avg_hr = normalized.get("avg_hr")
    has_hr_baseline = bool(profile.sex and profile.resting_hr and profile.max_hr)
    if avg_hr is not None and has_hr_baseline:
        return _compute_trimp(avg_hr, duration_s, profile)

    missing = []
    if avg_power is None or not profile.ftp_watts:
        missing.append("power+FTP")
    if avg_hr is None or not has_hr_baseline:
        missing.append("HR+baseline(sex/resting_hr/max_hr)")
    return _unknown(f"insufficient data for TSS or TRIMP: missing {' and '.join(missing)}")


def _unknown(reason: str) -> TrainingLoadResult:
    diagnostic_logger().info(f"normalization: training_load unknown — {reason}")
    return TrainingLoadResult(load=None, method=TrainingLoadMethod.UNKNOWN, reason=reason)


def _compute_tss(avg_power: float, duration_s: int, ftp_watts: int) -> TrainingLoadResult:
    """Coggan TSS, using avg_power as a proxy for Normalized Power (NP) —
    a documented, deliberate simplification: TrainIQ's connectors report
    average power only, not the full power stream NP requires (Milestone 1
    §4's stream-vs-summary tradeoff). IF = avg_power/FTP;
    TSS = (duration_s * avg_power * IF) / (FTP * 3600) * 100."""
    intensity_factor = avg_power / ftp_watts
    tss = (duration_s * avg_power * intensity_factor) / (ftp_watts * 3600) * 100
    return TrainingLoadResult(load=round(tss, 1), method=TrainingLoadMethod.TSS, reason=None)


def _compute_trimp(avg_hr: int, duration_s: int, profile: AthleteProfile) -> TrainingLoadResult:
    """Banister TRIMP, sex-specific exponential weighting (Milestone A §3).
    heart-rate-reserve is clamped to [0, 1] — an engineering judgment, not
    a researched constant: real-world HR readings and stored baselines are
    noisy enough that a value outside this range (avg_hr below
    resting_hr, or above max_hr, both observed in practice from imperfect
    sensor data) would make the exponential term blow up or invert rather
    than degrade gracefully. Flagged explicitly as a defensive choice, the
    same way Milestone C flagged its own threshold calibrations."""
    hrr = (avg_hr - profile.resting_hr) / (profile.max_hr - profile.resting_hr)
    hrr = max(0.0, min(hrr, 1.0))
    duration_min = duration_s / 60
    if profile.sex == "female":
        weighting_constant, exponent_coefficient = 0.86, 1.67
    else:
        weighting_constant, exponent_coefficient = 0.64, 1.92
    trimp = duration_min * hrr * weighting_constant * math.exp(exponent_coefficient * hrr)
    return TrainingLoadResult(load=round(trimp, 1), method=TrainingLoadMethod.TRIMP, reason=None)
