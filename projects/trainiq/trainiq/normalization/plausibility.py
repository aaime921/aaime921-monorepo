"""
trainiq.normalization.plausibility — ADR-039 / Issue #38, corrected by Issue #42

Flags a weigh-in record as implausible when it's very unlikely to be the
athlete's own body — e.g. another person, a pet, or an object briefly on
the scale (see issue #38's evidence: 7 of 568 Eufy readings at 18.9-35.8 kg
against the BO's real 80-88.3 kg baseline, every one also carrying a
`body_fat_pct` of 0.0 or 5.0).

Per the requirements (docs/trainiq/requirements/38-*.md), the rule is
relative to the athlete's own history, not a fixed absolute kg range —
athletes' baseline weights differ. Provider-agnostic by design: this is a
property of weight/body-composition values, not of Eufy specifically, so
it lives parallel to `confidence.py`/`load.py`, not inside
`connectors/eufy.py`.

ISSUE #42 CORRECTION: the weight axis and the body-fat axis are now two
fully independent verdicts (`WeighInPlausibility`) instead of one combined
one — a body-fat flag must never suppress a valid weight reading from
analytics. The body-fat floor also gained an explicit `> 0` guard, so it
only evaluates present, non-zero values; a connector that hasn't
normalized its own zero-sentinel (Eufy's own fix lives in
`connectors/eufy.py::normalize()`) still can't have that sentinel
mistaken for a physiological-floor violation here.
"""

from __future__ import annotations

from dataclasses import dataclass
from statistics import median
from typing import Optional, Sequence

# Calibratable defaults, documented in ADR-039 — not derived from a larger
# dataset (the issue only gives us 568 points from one athlete), chosen to
# comfortably separate the BO's ~80-88 kg baseline from the ~19-36 kg
# outliers (a 75%+ gap) while staying loose enough not to flag ordinary
# week-to-week fluctuation or a genuine multi-month trend.
DEFAULT_WEIGHT_DEVIATION_THRESHOLD_PCT = 25.0
DEFAULT_ROLLING_WINDOW_SIZE = 5  # how many prior readings feed the median
DEFAULT_MIN_HISTORY_FOR_WEIGHT_CHECK = 3  # below this, skip the weight-deviation check entirely
DEFAULT_BODY_FAT_FLOOR_PCT = 3.0  # essential-fat floor; see ADR-039's Risks section
                                  # for why this is a conservative second signal, not
                                  # the primary one — the weight axis alone already
                                  # catches all 7 of the BO's known bad records.


@dataclass(frozen=True)
class WeighInPlausibility:
    is_weight_plausible: bool
    weight_reason: Optional[str]  # None iff is_weight_plausible
    is_body_fat_plausible: bool
    body_fat_reason: Optional[str]  # None iff is_body_fat_plausible


def evaluate_weigh_in_plausibility(
    weight_kg: Optional[float],
    body_fat_pct: Optional[float],
    recent_weights_kg: Sequence[float],
    *,
    deviation_threshold_pct: float = DEFAULT_WEIGHT_DEVIATION_THRESHOLD_PCT,
    min_history: int = DEFAULT_MIN_HISTORY_FOR_WEIGHT_CHECK,
    body_fat_floor_pct: float = DEFAULT_BODY_FAT_FLOOR_PCT,
) -> WeighInPlausibility:
    """Pure function, no I/O. `recent_weights_kg` must already be the
    athlete's prior readings whose WEIGHT was not flagged (or was
    BO-confirmed), strictly before this one's timestamp, newest-first,
    already window-limited — this function does no filtering/ordering
    itself.

    Never fabricates a verdict from insufficient data: with fewer than
    `min_history` prior readings, the weight-deviation axis is skipped,
    not assumed-plausible via a default baseline.

    ISSUE #42: the two axes are now fully independent — one can never flag
    the other. A reading whose body-fat axis trips still contributes its
    weight to future rolling windows, since a missing or implausible
    body-composition value says nothing about whether the weight itself
    was measured correctly.

    1. Body-fat floor: `body_fat_pct` present, non-zero, and at or below
       `body_fat_floor_pct`. The `> 0` guard means a missing (`None`) or
       zero-sentinel value never trips this axis.
    2. Weight deviation: `weight_kg` deviates from the median of
       `recent_weights_kg` by more than `deviation_threshold_pct` percent,
       once enough history exists.

    Boundary is strict `>` — a reading exactly at the threshold is NOT
    flagged (deterministic, see tests).
    """
    if body_fat_pct is not None and body_fat_pct > 0 and body_fat_pct <= body_fat_floor_pct:
        is_body_fat_plausible, body_fat_reason = False, (
            f"body_fat_pct {body_fat_pct} is at or below the physiological "
            f"floor of {body_fat_floor_pct}"
        )
    else:
        is_body_fat_plausible, body_fat_reason = True, None

    is_weight_plausible, weight_reason = True, None
    if weight_kg is not None and len(recent_weights_kg) >= min_history:
        baseline = median(recent_weights_kg)
        if baseline > 0:
            deviation_pct = abs(weight_kg - baseline) / baseline * 100
            if deviation_pct > deviation_threshold_pct:
                is_weight_plausible, weight_reason = False, (
                    f"weight_kg {weight_kg} deviates {deviation_pct:.1f}% from the "
                    f"athlete's rolling median baseline of {baseline:.1f} kg, "
                    f"exceeding the {deviation_threshold_pct}% threshold"
                )

    return WeighInPlausibility(
        is_weight_plausible=is_weight_plausible,
        weight_reason=weight_reason,
        is_body_fat_plausible=is_body_fat_plausible,
        body_fat_reason=body_fat_reason,
    )
